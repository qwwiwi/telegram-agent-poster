#!/usr/bin/env python3
"""Subscription tracker: logs who joins or leaves the channel and through which invite link.

Run:  python3 tracker/bot.py
Needs in .env: BOT_TOKEN and CHANNEL (the bot must be an admin of the channel).
Optional: DATABASE_URL (default: SQLite file data/tracker.db).

The bot uses long polling (getUpdates), so it needs no public server or webhook.
Every join/leave is written to the `events` table; a member count snapshot is stored
every hour. Run `python3 tracker/report.py` to read the numbers.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import Event, Store, make_event_id  # noqa: E402
from tgcommon import ConfigError, TelegramError, api_call, load_env, require_env, setup_logging  # noqa: E402

LOG = logging.getLogger("tracker.bot")

POLL_TIMEOUT = 50  # seconds of long polling
SNAPSHOT_INTERVAL = 3600  # seconds between member count snapshots
MEMBER_STATUSES = {"member", "administrator", "creator"}


def is_member(chat_member: dict[str, object]) -> bool:
    """True if a ChatMember object describes someone who is currently in the channel."""
    status = chat_member.get("status")
    if status in MEMBER_STATUSES:
        return True
    # A restricted user stays a member only while is_member is true.
    return status == "restricted" and chat_member.get("is_member") is True


def parse_chat_member_update(update: dict[str, object], channel_id: int) -> Event | None:
    """Convert a Bot API update into a join/leave Event.

    Args:
        update: One element of the getUpdates result.
        channel_id: Numeric id of the tracked channel (updates from other chats are ignored).

    Returns:
        An Event, or None if the update is not a join/leave of this channel
        (other chat, role change of an existing member, malformed payload).
    """
    raw = update.get("chat_member")
    if not isinstance(raw, dict):
        return None
    chat = raw.get("chat")
    old, new = raw.get("old_chat_member"), raw.get("new_chat_member")
    if not (isinstance(chat, dict) and isinstance(old, dict) and isinstance(new, dict)):
        return None
    if chat.get("id") != channel_id:
        return None
    was_member, now_member = is_member(old), is_member(new)
    if was_member == now_member:
        return None
    user = new.get("user")
    date = raw.get("date")
    if not isinstance(user, dict) or not isinstance(user.get("id"), int) or not isinstance(date, int):
        return None
    occurred_at = datetime.fromtimestamp(date, tz=timezone.utc)
    kind = "join" if now_member else "leave"
    link = raw.get("invite_link") if now_member else None
    invite_url = invite_name = None
    if isinstance(link, dict):
        url, name = link.get("invite_link"), link.get("name")
        invite_url = url if isinstance(url, str) else None
        invite_name = name if isinstance(name, str) and name else None
    update_id = update.get("update_id")
    return Event(
        event_id=make_event_id(update_id if isinstance(update_id, int) else None, user["id"], kind, occurred_at),
        user_id=user["id"],
        username=user.get("username") if isinstance(user.get("username"), str) else None,
        kind=kind,
        invite_url=invite_url,
        invite_name=invite_name,
        occurred_at=occurred_at,
    )


def handle_updates(updates: list[object], channel_id: int, store: Store, offset: int) -> int:
    """Store the join/leave events found in a getUpdates batch.

    Returns:
        The next getUpdates offset (confirms everything up to the last update id).
    """
    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            offset = max(offset, update_id + 1)
        event = parse_chat_member_update(update, channel_id)
        if event is not None and store.add_event(event):
            LOG.info("%s user=%s via %s", event.kind, event.user_id, event.invite_name or "direct/unknown")
    return offset


def resolve_channel(token: str, channel: str) -> int:
    """Turn @username or a numeric id from .env into the numeric chat id."""
    chat = api_call(token, "getChat", {"chat_id": channel})
    if not isinstance(chat, dict) or not isinstance(chat.get("id"), int):
        raise TelegramError("getChat returned an unexpected answer")
    return chat["id"]


def take_snapshot(token: str, channel: str, store: Store) -> None:
    """Store the current member count."""
    count = api_call(token, "getChatMemberCount", {"chat_id": channel})
    if isinstance(count, int):
        store.add_snapshot(count)
        LOG.info("snapshot: %d members", count)


def run(token: str, channel: str, store: Store) -> None:
    """Poll for chat_member updates forever."""
    channel_id = resolve_channel(token, channel)
    me = api_call(token, "getMe")
    LOG.info("bot @%s tracks channel %s", me.get("username") if isinstance(me, dict) else "?", channel)
    offset = 0
    next_snapshot = 0.0
    while True:
        if time.monotonic() >= next_snapshot:
            try:
                take_snapshot(token, channel, store)
            except TelegramError as exc:
                LOG.warning("snapshot failed: %s", exc)
            next_snapshot = time.monotonic() + SNAPSHOT_INTERVAL
        try:
            updates = api_call(
                token, "getUpdates",
                {"offset": offset, "timeout": POLL_TIMEOUT, "allowed_updates": ["chat_member"]},
                timeout=POLL_TIMEOUT + 15,
            )
        except TelegramError as exc:
            LOG.warning("polling error: %s; retry in 5s", exc)
            time.sleep(5)
            continue
        if isinstance(updates, list):
            offset = handle_updates(updates, channel_id, store, offset)


def main() -> int:
    """Entry point."""
    setup_logging()
    load_env()
    try:
        token = require_env("BOT_TOKEN")
        channel = require_env("CHANNEL")
    except ConfigError as exc:
        LOG.error("%s", exc)
        return 1
    store = Store(os.environ.get("DATABASE_URL", ""))
    try:
        run(token, channel, store)
    except KeyboardInterrupt:
        LOG.info("stopped")
    except TelegramError as exc:
        LOG.error("fatal: %s", exc)
        return 1
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
