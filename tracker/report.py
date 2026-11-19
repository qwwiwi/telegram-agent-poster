#!/usr/bin/env python3
"""Subscriber report: per invite link, per day, and net growth.

Usage:
    python3 tracker/report.py [--days 30] [--tz Europe/Berlin] [--json]

Definitions:
  * joined  – joins inside the period, grouped by the invite link name (no link = direct);
  * left    – leaves inside the period, attributed to the link the user joined with last;
  * net     – joined minus left;
  * active  – people who joined via the link and have not left (all time, not only the period).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import Event, Store  # noqa: E402
from tgcommon import load_env  # noqa: E402

LOG = logging.getLogger("tracker.report")

DIRECT_LABEL = "(no link)"


@dataclass
class LinkStats:
    """Numbers for one invite link."""

    name: str
    joined: int = 0
    left: int = 0
    active: int = 0

    @property
    def net(self) -> int:
        """Joins minus leaves in the period."""
        return self.joined - self.left


@dataclass
class DayStats:
    """Numbers for one calendar day."""

    day: str
    joined: int = 0
    left: int = 0

    @property
    def net(self) -> int:
        """Joins minus leaves on this day."""
        return self.joined - self.left


@dataclass
class Report:
    """Aggregated numbers for a period."""

    since: datetime
    until: datetime
    links: list[LinkStats] = field(default_factory=list)
    days: list[DayStats] = field(default_factory=list)
    joined: int = 0
    left: int = 0
    members_first: int | None = None
    members_last: int | None = None

    @property
    def net(self) -> int:
        """Net growth of the period (joins minus leaves)."""
        return self.joined - self.left


def link_label(event: Event) -> str:
    """Display name of the link behind a join event."""
    if event.invite_name:
        return event.invite_name
    if event.invite_url:
        return "(unnamed link)"
    return DIRECT_LABEL


def aggregate(
    events: list[Event],
    since: datetime,
    until: datetime,
    tz: tzinfo = timezone.utc,
    snapshots: list[tuple[datetime, int]] | None = None,
) -> Report:
    """Aggregate join/leave events into a Report.

    Args:
        events: ALL events (including those before `since`: they are needed to attribute leaves).
        since: Period start (inclusive, aware datetime).
        until: Period end (exclusive, aware datetime).
        tz: Time zone for day buckets.
        snapshots: Optional (time, member_count) pairs for the member count line.

    Returns:
        The aggregated report. Links are sorted by joins (desc), days ascending (empty days included).
    """
    ordered = sorted(events, key=lambda e: (e.occurred_at, e.event_id))
    last_link: dict[int, str] = {}  # user -> label of the link of the latest join
    active: set[int] = set()
    links: dict[str, LinkStats] = {}
    days: dict[date, DayStats] = {}

    def link(name: str) -> LinkStats:
        return links.setdefault(name, LinkStats(name))

    def day(d: date) -> DayStats:
        return days.setdefault(d, DayStats(d.isoformat()))

    for event in ordered:
        in_period = since <= event.occurred_at < until
        bucket = event.occurred_at.astimezone(tz).date()
        if event.kind == "join":
            label = link_label(event)
            last_link[event.user_id] = label
            active.add(event.user_id)
            if in_period:
                link(label).joined += 1
                day(bucket).joined += 1
        elif event.kind == "leave":
            label = last_link.get(event.user_id, DIRECT_LABEL)
            active.discard(event.user_id)
            if in_period:
                link(label).left += 1
                day(bucket).left += 1
    for user_id in active:
        link(last_link[user_id]).active += 1

    report = Report(since=since, until=until)
    report.links = sorted(links.values(), key=lambda s: (-s.joined, -s.active, s.name))
    start_day, end_day = since.astimezone(tz).date(), (until - timedelta(seconds=1)).astimezone(tz).date()
    current = start_day
    while current <= end_day:
        day(current)
        current += timedelta(days=1)
    report.days = [days[d] for d in sorted(days)]
    report.joined = sum(s.joined for s in report.links)
    report.left = sum(s.left for s in report.links)
    if snapshots:
        in_range = [count for ts, count in snapshots if since <= ts < until]
        if in_range:
            report.members_first, report.members_last = in_range[0], in_range[-1]
    return report


def render_text(report: Report) -> str:
    """Human-readable report."""
    lines = [
        f"Period: {report.since:%Y-%m-%d} .. {(report.until - timedelta(seconds=1)):%Y-%m-%d}",
        f"Joined: {report.joined}   Left: {report.left}   Net growth: {report.net:+d}",
    ]
    if report.members_first is not None and report.members_last is not None:
        lines.append(f"Members (snapshots): {report.members_first} -> {report.members_last}")
    lines += ["", "By invite link:", f"  {'link':<34}{'joined':>7}{'left':>6}{'net':>6}{'active':>8}"]
    for s in report.links:
        lines.append(f"  {s.name:<34}{s.joined:>7}{s.left:>6}{s.net:>+6d}{s.active:>8}")
    if not report.links:
        lines.append("  (no events in this period)")
    lines += ["", "By day:", f"  {'day':<12}{'joined':>7}{'left':>6}{'net':>6}"]
    for d in report.days:
        lines.append(f"  {d.day:<12}{d.joined:>7}{d.left:>6}{d.net:>+6d}")
    return "\n".join(lines)


def to_json(report: Report) -> str:
    """Machine-readable report (for the agent)."""
    data = {
        "since": report.since.isoformat(),
        "until": report.until.isoformat(),
        "joined": report.joined,
        "left": report.left,
        "net": report.net,
        "members_first": report.members_first,
        "members_last": report.members_last,
        "links": [{**asdict(s), "net": s.net} for s in report.links],
        "days": [{**asdict(d), "net": d.net} for d in report.days],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Subscriber report per invite link and per day")
    parser.add_argument("--days", type=int, default=30, help="period length in days (default 30)")
    parser.add_argument("--tz", default="UTC", help="time zone for day buckets, e.g. Europe/Berlin")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")
    try:
        tz: tzinfo = ZoneInfo(args.tz)
    except ZoneInfoNotFoundError:
        parser.error(f"unknown time zone: {args.tz}")

    logging.basicConfig(level=logging.WARNING)
    load_env()
    store = Store(os.environ.get("DATABASE_URL", ""))
    try:
        now = datetime.now(timezone.utc)
        local_today = now.astimezone(tz).date()
        since = datetime.combine(local_today - timedelta(days=args.days - 1), datetime.min.time(), tzinfo=tz)
        until = datetime.combine(local_today + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        report = aggregate(store.fetch_events(), since, until, tz, store.fetch_snapshots())
    finally:
        store.close()
    print(to_json(report) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
