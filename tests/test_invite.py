"""Invite-link name handling and createChatInviteLink parameters (offline)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths  # noqa: E402,F401

from create_invite import InviteNameError, build_params, normalize_name  # noqa: E402


class NameTests(unittest.TestCase):
    def test_trims_and_collapses_whitespace(self) -> None:
        self.assertEqual(normalize_name("  instagram   bio \t"), "instagram bio")

    def test_keeps_unicode(self) -> None:
        self.assertEqual(normalize_name("Друг Анна"), "Друг Анна")

    def test_exactly_32_chars_is_ok_33_is_not(self) -> None:
        self.assertEqual(len(normalize_name("a" * 32)), 32)
        with self.assertRaises(InviteNameError):
            normalize_name("a" * 33)

    def test_empty_and_control_chars(self) -> None:
        for bad in ("", "   ", "\t\n", "a\x00b", "a\x1bb"):
            with self.subTest(bad=bad), self.assertRaises(InviteNameError):
                normalize_name(bad)
        self.assertEqual(normalize_name("line\nbreak"), "line break")


class ParamsTests(unittest.TestCase):
    def test_minimal(self) -> None:
        self.assertEqual(build_params("@chan", "promo"), {"chat_id": "@chan", "name": "promo", "member_limit": 99999})

    def test_limit_and_expire(self) -> None:
        params = build_params("@chan", "promo", member_limit=50, expire_days=2, now=1_000_000)
        self.assertEqual(params["member_limit"], 50)
        self.assertEqual(params["expire_date"], 1_000_000 + 2 * 86400)

    def test_limit_range(self) -> None:
        for bad in (0, 100_000):
            with self.subTest(limit=bad), self.assertRaises(InviteNameError):
                build_params("@chan", "p", member_limit=bad)

    def test_join_request_excludes_limit(self) -> None:
        with self.assertRaises(InviteNameError):
            build_params("@chan", "p", member_limit=5, creates_join_request=True)
        self.assertTrue(build_params("@chan", "p", creates_join_request=True)["creates_join_request"])

    def test_expire_must_be_positive(self) -> None:
        with self.assertRaises(InviteNameError):
            build_params("@chan", "p", expire_days=0)


if __name__ == "__main__":
    unittest.main()
