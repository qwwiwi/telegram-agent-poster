"""chat_member update parsing and the SQLite store (offline, temp files only)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths  # noqa: E402,F401

from bot import parse_chat_member_update  # noqa: E402
from db import Store  # noqa: E402
from report import aggregate  # noqa: E402

CHANNEL = -(10**12 + 1)  # fake channel id used only in tests
DATE = 1_790_000_000


def update(old: str, new: str, link: dict[str, object] | None = None, chat: int = CHANNEL,
           update_id: int = 1, **member: object) -> dict[str, object]:
    chat_member: dict[str, object] = {
        "chat": {"id": chat},
        "date": DATE,
        "old_chat_member": {"status": old, "user": {"id": 42}},
        "new_chat_member": {"status": new, "user": {"id": 42, "username": "someone"}, **member},
    }
    if link:
        chat_member["invite_link"] = link
    return {"update_id": update_id, "chat_member": chat_member}


class ParseTests(unittest.TestCase):
    def test_join_with_named_link(self) -> None:
        event = parse_chat_member_update(
            update("left", "member", {"invite_link": "https://t.me/+abc", "name": "insta"}), CHANNEL)
        assert event is not None
        self.assertEqual((event.kind, event.user_id, event.username, event.invite_name, event.invite_url),
                         ("join", 42, "someone", "insta", "https://t.me/+abc"))

    def test_join_without_link_and_leave(self) -> None:
        join = parse_chat_member_update(update("left", "member"), CHANNEL)
        leave = parse_chat_member_update(update("member", "left", {"invite_link": "https://t.me/+x", "name": "n"}), CHANNEL)
        assert join is not None and leave is not None
        self.assertIsNone(join.invite_name)
        self.assertEqual(leave.kind, "leave")
        self.assertIsNone(leave.invite_name)  # leaves carry no link

    def test_kicked_is_a_leave_and_restricted_member_stays(self) -> None:
        kicked = parse_chat_member_update(update("member", "kicked"), CHANNEL)
        assert kicked is not None
        self.assertEqual(kicked.kind, "leave")
        self.assertIsNone(parse_chat_member_update(update("member", "restricted", is_member=True), CHANNEL))

    def test_ignored_updates(self) -> None:
        self.assertIsNone(parse_chat_member_update(update("member", "administrator"), CHANNEL))  # role change
        self.assertIsNone(parse_chat_member_update(update("left", "member", chat=-1), CHANNEL))  # other chat
        self.assertIsNone(parse_chat_member_update({"update_id": 1, "message": {}}, CHANNEL))
        self.assertIsNone(parse_chat_member_update({"update_id": 1, "chat_member": {"chat": {"id": CHANNEL}}}, CHANNEL))

    def test_same_update_gets_the_same_id(self) -> None:
        a = parse_chat_member_update(update("left", "member", update_id=7), CHANNEL)
        b = parse_chat_member_update(update("left", "member", update_id=7), CHANNEL)
        c = parse_chat_member_update(update("left", "member", update_id=8), CHANNEL)
        assert a and b and c
        self.assertEqual(a.event_id, b.event_id)
        self.assertNotEqual(a.event_id, c.event_id)


class StoreTests(unittest.TestCase):
    def test_idempotent_events_links_and_report_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(f"sqlite:///{tmp}/t.db")
            try:
                join = parse_chat_member_update(update("left", "member", {"invite_link": "https://t.me/+a", "name": "insta"}), CHANNEL)
                assert join is not None
                self.assertTrue(store.add_event(join))
                self.assertFalse(store.add_event(join))  # redelivery is a no-op
                store.add_link("https://t.me/+a", "insta")
                self.assertEqual(store.find_link_by_name("insta"), "https://t.me/+a")
                self.assertIsNone(store.find_link_by_name("nope"))
                store.add_snapshot(10)
                events = store.fetch_events()
                self.assertEqual(len(events), 1)
                since = datetime.fromtimestamp(DATE - 86400, tz=timezone.utc)
                until = datetime.fromtimestamp(DATE + 86400, tz=timezone.utc)
                report = aggregate(events, since, until, snapshots=store.fetch_snapshots())
                self.assertEqual((report.joined, report.links[0].name), (1, "insta"))
            finally:
                store.close()

    def test_rejects_unknown_database_url(self) -> None:
        with self.assertRaises(ValueError):
            Store("mysql://x")


if __name__ == "__main__":
    unittest.main()
