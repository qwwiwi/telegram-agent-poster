"""Pre-publish check: length, secrets, HTML validity (offline)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths  # noqa: E402,F401

from check import check_post, has_errors, visible_length  # noqa: E402


def codes(text: str, photo: bool = False, level: str | None = None) -> set[str]:
    return {i.code for i in check_post(text, has_photo=photo) if level is None or i.level == level}


# Fake secrets are assembled at runtime so the repo never contains a token-like literal.
FAKE_BOT_TOKEN = "1234567890" + ":" + "A" * 35
FAKE_CHAT_ID = "-100" + "1234567890"
FAKE_IP = "192.168." + "1.20"
FAKE_PATH = "/" + "Users" + "/someone/project"
FAKE_OPENAI = "sk-" + "a1" * 15


class LengthTests(unittest.TestCase):
    def test_tags_do_not_count(self) -> None:
        self.assertEqual(visible_length("<b>Hi</b> &amp; <a href=\"https://x.io\">you</a>"), len("Hi & you"))

    def test_ok_post_has_no_errors(self) -> None:
        self.assertFalse(has_errors(check_post("<b>Заголовок</b>\n\nТекст поста.")))

    def test_text_limit_4096(self) -> None:
        self.assertIn("too-long", codes("a" * 4097))
        self.assertNotIn("too-long", codes("a" * 4096))

    def test_tags_are_not_counted_against_the_limit(self) -> None:
        self.assertNotIn("too-long", codes("<b>" + "a" * 4090 + "</b>"))

    def test_caption_limit_is_a_warning_not_an_error(self) -> None:
        issues = check_post("a" * 1100, has_photo=True)
        self.assertIn("caption-too-long", {i.code for i in issues})
        self.assertFalse(has_errors(issues))

    def test_empty(self) -> None:
        self.assertEqual(codes("   \n"), {"empty"})


class SecretTests(unittest.TestCase):
    def test_each_secret_kind_is_blocked(self) -> None:
        for sample in (FAKE_BOT_TOKEN, FAKE_CHAT_ID, FAKE_IP, FAKE_PATH, FAKE_OPENAI,
                       "password = hunter22hunter", "-----BEGIN RSA " + "PRIVATE KEY-----"):
            with self.subTest(sample=sample[:12]):
                self.assertIn("secret", codes(f"Hello {sample} world", level="error"))

    def test_message_does_not_echo_the_secret(self) -> None:
        for issue in check_post(f"x {FAKE_BOT_TOKEN}"):
            self.assertNotIn(FAKE_BOT_TOKEN, issue.message)

    def test_normal_prose_passes(self) -> None:
        self.assertNotIn("secret", codes("Токен бота лежит в .env, а не в гите. Ссылка: https://t.me/example"))


class HtmlTests(unittest.TestCase):
    def test_all_telegram_formats_are_valid(self) -> None:
        text = (
            "<b>bold</b> <i>it</i> <u>u</u> <s>s</s> <code>c</code> <tg-spoiler>hidden</tg-spoiler> "
            "<span class=\"tg-spoiler\">hidden</span>\n<a href=\"https://example.com\">link</a>\n"
            "<blockquote>quote</blockquote>\n<blockquote expandable>long quote</blockquote>\n"
            "<pre><code class=\"language-python\">print(1)</code></pre>"
        )
        self.assertEqual(codes(text, level="error"), set())

    def test_unsupported_tags(self) -> None:
        for tag in ("br", "p", "h1", "ul"):
            with self.subTest(tag=tag):
                self.assertIn("bad-tag", codes(f"a <{tag}> b"))

    def test_unclosed_and_misnested(self) -> None:
        self.assertIn("unclosed", codes("<b>bold"))
        self.assertIn("nesting", codes("<b><i>x</b></i>"))
        self.assertIn("nesting", codes("oops </b>"))

    def test_nested_blockquote_and_formatting_in_code(self) -> None:
        self.assertIn("nesting", codes("<blockquote><blockquote>x</blockquote></blockquote>"))
        self.assertIn("nesting", codes("<code><b>x</b></code>"))

    def test_raw_angle_bracket_and_ampersand(self) -> None:
        self.assertIn("raw-lt", codes("I <3 Telegram"))
        self.assertIn("raw-amp", codes("Q&A session", level="warning"))
        self.assertNotIn("raw-amp", codes("Q&amp;A session"))

    def test_link_rules(self) -> None:
        self.assertIn("link", codes("<a>no href</a>", level="error"))
        self.assertIn("link", codes("<a href=\"javascript:alert(1)\">x</a>", level="error"))
        self.assertIn("link", codes("<a href=\"http://example.com\">x</a>", level="warning"))

    def test_span_only_as_spoiler(self) -> None:
        self.assertIn("bad-attr", codes("<span class=\"red\">x</span>"))


if __name__ == "__main__":
    unittest.main()
