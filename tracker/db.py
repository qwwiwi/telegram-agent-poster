"""Storage for the tracker: SQLite by default, PostgreSQL when DATABASE_URL points to one.

Schema (all timestamps are ISO-8601 UTC strings, so the same SQL works on both engines):
    invite_links(url PRIMARY KEY, name, created_at)
    events(id PRIMARY KEY, user_id, username, kind 'join'|'leave', invite_url, invite_name, occurred_at)
    snapshots(id, member_count, recorded_at)

`events` is an append-only log. The id is derived from the Telegram update, so a redelivered
update is stored once (INSERT ... ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger("tracker.db")

DEFAULT_SQLITE = Path(__file__).resolve().parent.parent / "data" / "tracker.db"

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS invite_links (
    url TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, username TEXT,
    kind TEXT NOT NULL, invite_url TEXT, invite_name TEXT, occurred_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_time ON events (occurred_at);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, member_count INTEGER NOT NULL, recorded_at TEXT NOT NULL);
"""

SCHEMA_POSTGRES = SCHEMA_SQLITE.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


@dataclass(frozen=True)
class Event:
    """One join or leave of a channel member.

    Attributes:
        event_id: Deterministic id (same Telegram update -> same id).
        user_id: Telegram user id.
        username: Telegram @username without the @ (may be None).
        kind: "join" or "leave".
        invite_url: Invite link URL used to join (None for leaves and direct joins).
        invite_name: Name given to the link at creation (None if unknown).
        occurred_at: Event time, timezone-aware UTC.
    """

    event_id: str
    user_id: int
    username: str | None
    kind: str
    invite_url: str | None
    invite_name: str | None
    occurred_at: datetime


def make_event_id(update_id: int | None, user_id: int, kind: str, occurred_at: datetime) -> str:
    """Deterministic event id: a redelivered update or a replay maps to the same row."""
    return f"{update_id}:{user_id}:{kind}:{int(occurred_at.timestamp())}"


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value: str) -> datetime:
    """Parse a stored timestamp into an aware UTC datetime."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class Store:
    """Thin DB layer. One instance per process; not thread-safe."""

    def __init__(self, database_url: str = "") -> None:
        """Open the database.

        Args:
            database_url: "" (default SQLite file in data/), "sqlite:///path/to.db",
                or "postgresql://user:pass@host/db" (needs `pip install psycopg[binary]`).
        """
        self.is_postgres = database_url.startswith(("postgres://", "postgresql://"))
        self._conn: Any
        self._last_rowcount = 0
        if self.is_postgres:
            try:
                import psycopg  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("PostgreSQL needs: pip install 'psycopg[binary]'") from exc
            self._conn = psycopg.connect(database_url, autocommit=True)
            self._exec_script(SCHEMA_POSTGRES)
        else:
            path = self._sqlite_path(database_url)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path))
            self._conn.executescript(SCHEMA_SQLITE)
            self._conn.commit()
        LOG.info("storage: %s", "postgres" if self.is_postgres else "sqlite")

    @staticmethod
    def _sqlite_path(database_url: str) -> Path:
        if not database_url:
            return DEFAULT_SQLITE
        if database_url.startswith("sqlite:///"):
            return Path(database_url[len("sqlite:///"):]).expanduser()
        raise ValueError("DATABASE_URL must be empty, sqlite:///path or postgresql://...")

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.is_postgres else sql

    def _exec_script(self, script: str) -> None:
        for statement in filter(None, (s.strip() for s in script.split(";"))):
            self._conn.execute(statement)

    def _run(self, sql: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        cursor = self._conn.execute(self._sql(sql), params)
        self._last_rowcount = cursor.rowcount
        rows = cursor.fetchall() if cursor.description else []
        if not self.is_postgres:
            self._conn.commit()
        return rows

    def add_event(self, event: Event) -> bool:
        """Store an event. Returns False if it was already stored (idempotent)."""
        self._run(
            "INSERT INTO events (id, user_id, username, kind, invite_url, invite_name, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (event.event_id, event.user_id, event.username, event.kind, event.invite_url,
             event.invite_name, event.occurred_at.astimezone(timezone.utc).isoformat(timespec="seconds")),
        )
        return self._last_rowcount > 0

    def fetch_events(self) -> list[Event]:
        """All events ordered by time."""
        rows = self._run(
            "SELECT id, user_id, username, kind, invite_url, invite_name, occurred_at "
            "FROM events ORDER BY occurred_at, id"
        )
        return [Event(r[0], int(r[1]), r[2], r[3], r[4], r[5], parse_ts(r[6])) for r in rows]

    def add_link(self, url: str, name: str) -> None:
        """Remember a created invite link (the Bot API cannot list links later)."""
        self._run(
            "INSERT INTO invite_links (url, name, created_at) VALUES (?, ?, ?) ON CONFLICT (url) DO NOTHING",
            (url, name, now_iso()),
        )

    def find_link_by_name(self, name: str) -> str | None:
        """Return the URL of the newest link with this name, if any."""
        rows = self._run("SELECT url FROM invite_links WHERE name = ? ORDER BY created_at DESC LIMIT 1", (name,))
        return rows[0][0] if rows else None

    def list_links(self) -> list[tuple[str, str, str]]:
        """All known links as (name, url, created_at), newest first."""
        rows = self._run("SELECT name, url, created_at FROM invite_links ORDER BY created_at DESC")
        return [(r[0], r[1], r[2]) for r in rows]

    def add_snapshot(self, member_count: int) -> None:
        """Record the current member count."""
        self._run("INSERT INTO snapshots (member_count, recorded_at) VALUES (?, ?)", (member_count, now_iso()))

    def fetch_snapshots(self) -> list[tuple[datetime, int]]:
        """Member count snapshots as (time, count), oldest first."""
        rows = self._run("SELECT recorded_at, member_count FROM snapshots ORDER BY recorded_at, id")
        return [(parse_ts(r[0]), int(r[1])) for r in rows]

    def close(self) -> None:
        """Close the connection."""
        self._conn.close()
