#!/usr/bin/env python3
"""Read public view counts of channel posts from the t.me/s/<channel> web preview.

Usage:
    python3 poster/views.py [channel] [--json] [--no-save]

No bot token and no user account are needed: the page is public. It works only for channels
with a public @username. Every run appends one line per post to data/views-history.jsonl
together with the post age, because views keep growing for weeks: compare posts only at
the same age (for example 24 hours after publishing), never raw totals of old and new posts.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from tgcommon import REPO_ROOT, load_env, setup_logging  # noqa: E402

LOG = logging.getLogger("poster.views")

HISTORY = REPO_ROOT / "data" / "views-history.jsonl"
RE_VIEWS = re.compile(r'tgme_widget_message_views">([^<]+)<')
RE_TIME = re.compile(r'datetime="([^"]+)"')
RE_REACT_BLOCK = re.compile(r'js-message_reactions">(.*?)</div>', re.S)
RE_REACT = re.compile(r"<b>([^<]*)</b></i>(\d+)")
RE_CHANNEL = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")


def parse_views(raw: str) -> int:
    """Telegram abbreviates large counts: '1.2K', '3M'."""
    text = raw.strip().replace(",", "").replace(" ", "")
    multiplier = {"K": 1_000, "M": 1_000_000}.get(text[-1:].upper())
    if multiplier:
        return int(float(text[:-1]) * multiplier)
    return int(text)


def parse_preview(page: str, channel: str, now: datetime) -> list[dict[str, object]]:
    """Extract posts (id, views, reactions, age) from the preview HTML.

    Args:
        page: HTML of https://t.me/s/<channel>.
        channel: Channel username without @.
        now: Reference time for the age calculation (aware UTC).

    Returns:
        One dict per post that has an id, a view counter and a timestamp.
    """
    id_re = re.compile(rf'data-post="{re.escape(channel)}/(\d+)"', re.I)
    rows: list[dict[str, object]] = []
    # Split at every post marker so a block never swallows the next (possibly foreign) post.
    for block in re.split(r'(?=data-post=")', page)[1:]:
        m_id, m_views, m_time = id_re.match(block), RE_VIEWS.search(block), RE_TIME.search(block)
        if not (m_id and m_views and m_time):
            continue
        try:
            posted = datetime.fromisoformat(m_time.group(1))
            views = parse_views(m_views.group(1))
        except ValueError:
            LOG.warning("skipping post %s: unreadable time or views", m_id.group(1))
            continue
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        react_block = RE_REACT_BLOCK.search(block)
        reactions = RE_REACT.findall(react_block.group(1)) if react_block else []
        rows.append({
            "snapshot_ts": now.isoformat(timespec="seconds"),
            "message_id": int(m_id.group(1)),
            "posted_at": posted.astimezone(timezone.utc).isoformat(timespec="minutes"),
            "age_hours": round((now - posted).total_seconds() / 3600, 1),
            "views": views,
            "reactions": sum(int(count) for _, count in reactions),
        })
    return rows


def fetch_preview(channel: str) -> str:
    """Download the public preview page."""
    request = urllib.request.Request(
        f"https://t.me/s/{channel}", headers={"User-Agent": "Mozilla/5.0 (compatible; channel-stats)"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def normalize_channel(raw: str) -> str:
    """Accept '@name', 'name' or 'https://t.me/name'; return the bare username.

    Raises:
        ValueError: If it is not a public username (numeric ids have no web preview).
    """
    name = re.sub(r"^(?:https?://)?t\.me/(?:s/)?", "", raw.strip()).lstrip("@").split("/")[0]
    if not RE_CHANNEL.fullmatch(name):
        raise ValueError(f"{raw!r} is not a public channel @username (the web preview needs one)")
    return name


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Public view counts of channel posts")
    parser.add_argument("channel", nargs="?", help="@username (default: CHANNEL from .env)")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument("--no-save", action="store_true", help="do not append to the history file")
    args = parser.parse_args(argv)

    setup_logging(logging.WARNING)
    load_env()
    try:
        channel = normalize_channel(args.channel or os.environ.get("CHANNEL", ""))
        rows = parse_preview(fetch_preview(channel), channel, datetime.now(timezone.utc))
    except ValueError as exc:
        LOG.error("%s", exc)
        return 2
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        LOG.error("could not load the preview: %s", exc)
        return 1
    if not rows:
        LOG.error("the preview returned no posts (private channel, or the web preview is off)")
        return 1
    if not args.no_save:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"{'post':>6} {'views':>8} {'reactions':>10} {'age, h':>8}")
        for row in sorted(rows, key=lambda r: -int(r["message_id"])):  # type: ignore[call-overload]
            print(f"{row['message_id']:>6} {row['views']:>8} {row['reactions']:>10} {row['age_hours']:>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
