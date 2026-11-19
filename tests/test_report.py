"""Report aggregation: per link, per day, net growth (offline)."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths  # noqa: E402,F401

from db import Event  # noqa: E402
from report import DIRECT_LABEL, aggregate, render_text, to_json  # noqa: E402

UTC = timezone.utc
SINCE = datetime(2026, 11, 10, tzinfo=UTC)
UNTIL = datetime(2026, 11, 13, tzinfo=UTC)  # exclusive: days 10, 11, 12
_counter = 0


def ev(kind: str, user: int, when: datetime, name: str | None = None, url: str | None = None) -> Event:
    global _counter
    _counter += 1
    if name and not url:
        url = f"https://t.me/+{name}"
    return Event(f"e{_counter}", user, None, kind, url, name, when)


class AggregateTests(unittest.TestCase):
    def test_counts_per_link_and_net(self) -> None:
        events = [
            ev("join", 1, datetime(2026, 11, 10, 9, tzinfo=UTC), "insta"),
            ev("join", 2, datetime(2026, 11, 10, 10, tzinfo=UTC), "insta"),
            ev("join", 3, datetime(2026, 11, 11, 10, tzinfo=UTC), "youtube"),
            ev("leave", 2, datetime(2026, 11, 12, 10, tzinfo=UTC)),
        ]
        report = aggregate(events, SINCE, UNTIL)
        by_name = {s.name: s for s in report.links}
        self.assertEqual((by_name["insta"].joined, by_name["insta"].left, by_name["insta"].active), (2, 1, 1))
        self.assertEqual((by_name["youtube"].joined, by_name["youtube"].left, by_name["youtube"].active), (1, 0, 1))
        self.assertEqual((report.joined, report.left, report.net), (3, 1, 2))
        self.assertEqual([s.name for s in report.links], ["insta", "youtube"])  # sorted by joins

    def test_per_day_includes_empty_days(self) -> None:
        events = [
            ev("join", 1, datetime(2026, 11, 10, 23, 59, tzinfo=UTC), "a"),
            ev("join", 2, datetime(2026, 11, 12, 0, 0, tzinfo=UTC), "a"),
            ev("leave", 1, datetime(2026, 11, 12, 5, tzinfo=UTC)),
        ]
        report = aggregate(events, SINCE, UNTIL)
        self.assertEqual([(d.day, d.joined, d.left, d.net) for d in report.days],
                         [("2026-11-10", 1, 0, 1), ("2026-11-11", 0, 0, 0), ("2026-11-12", 1, 1, 0)])

    def test_leave_is_attributed_to_the_latest_join_even_before_period(self) -> None:
        events = [
            ev("join", 7, datetime(2026, 10, 1, tzinfo=UTC), "old"),
            ev("leave", 7, datetime(2026, 10, 5, tzinfo=UTC)),
            ev("join", 7, datetime(2026, 10, 6, tzinfo=UTC), "second"),
            ev("leave", 7, datetime(2026, 11, 11, tzinfo=UTC)),
        ]
        report = aggregate(events, SINCE, UNTIL)
        self.assertEqual({s.name: (s.joined, s.left, s.active) for s in report.links}, {"second": (0, 1, 0)})

    def test_rejoin_counts_as_active_once_with_new_link(self) -> None:
        events = [
            ev("join", 5, datetime(2026, 11, 10, tzinfo=UTC), "a"),
            ev("leave", 5, datetime(2026, 11, 11, tzinfo=UTC)),
            ev("join", 5, datetime(2026, 11, 12, tzinfo=UTC), "b"),
        ]
        report = aggregate(events, SINCE, UNTIL)
        by_name = {s.name: s for s in report.links}
        self.assertEqual(by_name["a"].active, 0)
        self.assertEqual(by_name["b"].active, 1)

    def test_join_without_link_is_direct(self) -> None:
        report = aggregate([ev("join", 1, datetime(2026, 11, 11, tzinfo=UTC))], SINCE, UNTIL)
        self.assertEqual(report.links[0].name, DIRECT_LABEL)

    def test_events_outside_period_are_not_counted_but_still_active(self) -> None:
        events = [ev("join", 1, datetime(2026, 11, 1, tzinfo=UTC), "early"), ev("join", 2, datetime(2026, 11, 20, tzinfo=UTC), "late")]
        report = aggregate(events, SINCE, UNTIL)
        self.assertEqual(report.joined, 0)
        self.assertEqual({s.name: s.active for s in report.links}, {"early": 1, "late": 1})

    def test_day_buckets_follow_time_zone(self) -> None:
        # 23:30 UTC on Nov 10 is already Nov 11 in Berlin (UTC+1 in November).
        berlin = ZoneInfo("Europe/Berlin")
        since = datetime(2026, 11, 10, tzinfo=berlin)
        until = datetime(2026, 11, 12, tzinfo=berlin)
        report = aggregate([ev("join", 1, datetime(2026, 11, 10, 23, 30, tzinfo=UTC), "a")], since, until, berlin)
        self.assertEqual([(d.day, d.joined) for d in report.days], [("2026-11-10", 0), ("2026-11-11", 1)])

    def test_member_count_snapshots(self) -> None:
        snaps = [(SINCE + timedelta(hours=1), 100), (SINCE + timedelta(days=2), 108), (UNTIL + timedelta(days=1), 999)]
        report = aggregate([], SINCE, UNTIL, snapshots=snaps)
        self.assertEqual((report.members_first, report.members_last), (100, 108))

    def test_render_and_json_do_not_crash_on_empty(self) -> None:
        report = aggregate([], SINCE, UNTIL)
        self.assertIn("no events", render_text(report))
        self.assertIn('"net": 0', to_json(report))


if __name__ == "__main__":
    unittest.main()
