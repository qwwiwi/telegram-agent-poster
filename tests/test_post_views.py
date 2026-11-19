"""post.py request building and views.py preview parsing (offline)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths  # noqa: E402,F401

from post import build_requests, parse_button, post_url, read_post, validate_photo  # noqa: E402
from tgcommon import parse_env_text  # noqa: E402
from views import normalize_channel, parse_preview, parse_views  # noqa: E402


class PostTests(unittest.TestCase):
    def test_text_only(self) -> None:
        (req,) = build_requests("@chan", "<b>Hi</b>")
        self.assertEqual(req.method, "sendMessage")
        self.assertEqual(req.params["parse_mode"], "HTML")
        self.assertEqual(req.params["text"], "<b>Hi</b>")

    def test_photo_with_short_text_uses_caption(self) -> None:
        (req,) = build_requests("@chan", "short", photo=Path("c.png"))
        self.assertEqual((req.method, req.params["caption"]), ("sendPhoto", "short"))
        self.assertEqual(list(req.files), ["photo"])

    def test_photo_with_long_text_is_two_messages(self) -> None:
        first, second = build_requests("@chan", "x" * 1025, photo=Path("c.png"), buttons=[parse_button("Go|https://example.com")])
        self.assertEqual((first.method, second.method), ("sendPhoto", "sendMessage"))
        self.assertNotIn("caption", first.params)
        self.assertIn("reply_markup", second.params)  # buttons go with the text
        self.assertNotIn("reply_markup", first.params)

    def test_caption_limit_counts_visible_text_only(self) -> None:
        text = "<b>" + "x" * 1000 + "</b>" + "<i></i>" * 20  # >1024 raw, 1000 visible
        (req,) = build_requests("@chan", text, photo=Path("c.png"))
        self.assertEqual(req.method, "sendPhoto")

    def test_buttons(self) -> None:
        self.assertEqual(parse_button("Подписаться | https://t.me/+abc"), {"text": "Подписаться", "url": "https://t.me/+abc"})
        for bad in ("no-separator", "|https://x.io", "Go|ftp://x.io", "Go|"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_button(bad)

    def test_plain_file_is_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.txt"
            path.write_text("a < b & c\n", encoding="utf-8")
            self.assertEqual(read_post(path, plain=True), "a &lt; b &amp; c")
            self.assertEqual(read_post(path, plain=False), "a < b & c")

    def test_photo_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = Path(tmp) / "a.png", Path(tmp) / "a.gif"
            good.write_bytes(b"x")
            bad.write_bytes(b"x")
            validate_photo(good)
            for p in (bad, Path(tmp) / "missing.png"):
                with self.subTest(p=p.name), self.assertRaises(ValueError):
                    validate_photo(p)

    def test_post_url(self) -> None:
        self.assertEqual(post_url("@chan", 5), "https://t.me/chan/5")
        self.assertIsNone(post_url("-" + "5", 5))


class EnvTests(unittest.TestCase):
    def test_parse_env_text(self) -> None:
        text = "# c\nA=1\nexport B=\"two words\"\nC='x' \nD=val # comment\n\nbad line\n"
        self.assertEqual(parse_env_text(text), {"A": "1", "B": "two words", "C": "x", "D": "val"})


class RequireEnvTests(unittest.TestCase):
    def test_placeholder_and_empty_values_are_rejected(self) -> None:
        import os
        from unittest import mock
        from tgcommon import ConfigError, require_env

        for value in ("", "  ", "<<PLACEHOLDER: token>>"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"X_TEST_VAR": value}):
                with self.assertRaises(ConfigError):
                    require_env("X_TEST_VAR")
        with mock.patch.dict(os.environ, {"X_TEST_VAR": "ok"}):
            self.assertEqual(require_env("X_TEST_VAR"), "ok")


PREVIEW = """
<div class="tgme_widget_message" data-post="mychan/10">
 <span class="tgme_widget_message_views">1.2K</span>
 <time datetime="2026-11-18T10:00:00+00:00"></time>
 <div class="tgme_widget_message_reactions js-message_reactions"><span><i class="emoji"><b>X</b></i>5</span><span><i><b>Y</b></i>2</span></div>
</div>
<div class="tgme_widget_message" data-post="mychan/11">
 <span class="tgme_widget_message_views">87</span>
 <time datetime="2026-11-19T09:00:00+00:00"></time>
</div>
<div data-post="mychan/12"><time datetime="2026-11-19T09:30:00+00:00"></time></div>
<div data-post="other/99"><span class="tgme_widget_message_views">5</span><time datetime="2026-11-19T09:30:00+00:00"></time></div>
"""


class ViewsTests(unittest.TestCase):
    def test_parse_views(self) -> None:
        self.assertEqual([parse_views(x) for x in ("87", "1.2K", "3M", "1 234")], [87, 1200, 3_000_000, 1234])

    def test_parse_preview_and_age(self) -> None:
        now = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
        rows = parse_preview(PREVIEW, "mychan", now)
        self.assertEqual([(r["message_id"], r["views"], r["reactions"], r["age_hours"]) for r in rows],
                         [(10, 1200, 7, 24.0), (11, 87, 0, 1.0)])  # 12 has no views, 99 is another channel

    def test_normalize_channel(self) -> None:
        for raw in ("@mychan", "mychan", "https://t.me/mychan", "https://t.me/s/mychan/"):
            self.assertEqual(normalize_channel(raw), "mychan")
        for bad in ("", "-" + "5", "ab"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_channel(bad)


if __name__ == "__main__":
    unittest.main()
