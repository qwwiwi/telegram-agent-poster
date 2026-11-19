"""End-to-end run of the scripts against a local mock of the Bot API (no real network).

Starts an HTTP server on 127.0.0.1 that answers the Bot API methods the project uses and
records every request. Skipped automatically where local sockets are not available.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths  # noqa: E402,F401

import bot  # noqa: E402
import create_invite  # noqa: E402
import post  # noqa: E402
from db import Store  # noqa: E402
from tgcommon import api_call  # noqa: E402

TOKEN = "123456789:" + "T" * 35  # fake, assembled so no token-like literal sits in the repo
CHAT_ID = -(10**12 + 7)
RECORDED: list[dict[str, object]] = []


class MockBotApi(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # silence the server log
        pass

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        method = self.path.rsplit("/", 1)[-1]
        ctype = self.headers.get("Content-Type", "")
        RECORDED.append({"method": method, "ctype": ctype, "body": body, "path": self.path})
        params = json.loads(body) if ctype.startswith("application/json") and body else {}
        results: dict[str, object] = {
            "getChat": {"id": CHAT_ID},
            "getMe": {"username": "mock_bot"},
            "getChatMemberCount": 12,
            "createChatInviteLink": {"invite_link": "https://t.me/+mock", "name": params.get("name")},
            "sendMessage": {"message_id": 101},
            "sendPhoto": {"message_id": 102},
            "getUpdates": [{
                "update_id": 5,
                "chat_member": {
                    "chat": {"id": CHAT_ID}, "date": 1_790_000_000,
                    "old_chat_member": {"status": "left", "user": {"id": 9}},
                    "new_chat_member": {"status": "member", "user": {"id": 9, "username": "u"}},
                    "invite_link": {"invite_link": "https://t.me/+mock", "name": "demo"},
                },
            }],
        }
        payload = {"ok": True, "result": results[method]} if method in results else {"ok": False, "description": "no"}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def sockets_available() -> bool:
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


@unittest.skipUnless(sockets_available(), "local sockets are not available")
class EndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        RECORDED.clear()
        self.server = HTTPServer(("127.0.0.1", 0), MockBotApi)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        env = {
            "TELEGRAM_API_URL": f"http://127.0.0.1:{self.server.server_port}",
            "BOT_TOKEN": TOKEN,
            "CHANNEL": "@mock_channel",
            "DATABASE_URL": f"sqlite:///{self.dir}/t.db",
            "ENV_FILE": str(self.dir / "absent.env"),
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        log_patch = mock.patch.object(post, "POSTS_LOG", self.dir / "posts.jsonl")
        log_patch.start()
        self.addCleanup(log_patch.stop)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def run_main(self, module: object, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = module.main(argv)  # type: ignore[attr-defined]
        return code, out.getvalue()

    def test_create_invite_then_reuse(self) -> None:
        code, out = self.run_main(create_invite, ["  instagram   bio "])
        self.assertEqual((code, out.strip()), (0, "instagram bio\thttps://t.me/+mock"))
        sent = json.loads(RECORDED[-1]["body"])  # type: ignore[arg-type]
        self.assertEqual((sent["chat_id"], sent["name"]), ("@mock_channel", "instagram bio"))
        calls = len(RECORDED)
        code, out = self.run_main(create_invite, ["instagram bio"])
        self.assertIn("already exists", out)
        self.assertEqual(len(RECORDED), calls)  # no second API call

    def test_post_dry_run_sends_nothing_and_publish_does(self) -> None:
        draft = self.dir / "d.html"
        draft.write_text("<b>Заголовок</b>\n\nТекст", encoding="utf-8")
        code, out = self.run_main(post, [str(draft), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("Dry run", out)
        self.assertEqual(RECORDED, [])
        code, out = self.run_main(post, [str(draft), "--yes", "--button", "Go|https://example.com"])
        self.assertEqual(code, 0, out)
        self.assertIn("https://t.me/mock_channel/101", out)
        sent = json.loads(RECORDED[-1]["body"])  # type: ignore[arg-type]
        self.assertEqual((RECORDED[-1]["method"], sent["parse_mode"]), ("sendMessage", "HTML"))
        self.assertEqual(sent["reply_markup"], {"inline_keyboard": [[{"text": "Go", "url": "https://example.com"}]]})
        self.assertIn("message_id", (self.dir / "posts.jsonl").read_text(encoding="utf-8"))

    def test_post_with_photo_is_multipart(self) -> None:
        draft, image = self.dir / "d.html", self.dir / "c.png"
        draft.write_text("<b>Short</b>", encoding="utf-8")
        image.write_bytes(b"\x89PNG fake")
        code, _ = self.run_main(post, [str(draft), "--photo", str(image), "--yes"])
        self.assertEqual(code, 0)
        self.assertTrue(str(RECORDED[-1]["ctype"]).startswith("multipart/form-data"))
        self.assertIn(b'name="photo"; filename="c.png"', RECORDED[-1]["body"])  # type: ignore[operator]
        self.assertIn(b'name="caption"', RECORDED[-1]["body"])  # type: ignore[operator]

    def test_post_with_secret_is_blocked_before_any_request(self) -> None:
        draft = self.dir / "d.html"
        draft.write_text("my token " + TOKEN, encoding="utf-8")
        code, out = self.run_main(post, [str(draft), "--yes"])
        self.assertEqual(code, 1)
        self.assertIn("Not published", out)
        self.assertEqual(RECORDED, [])

    def test_post_without_yes_and_without_terminal_does_not_publish(self) -> None:
        draft = self.dir / "d.html"
        draft.write_text("hello", encoding="utf-8")
        with mock.patch.object(sys, "stdin", io.StringIO("")):
            code, _ = self.run_main(post, [str(draft)])
        self.assertEqual(code, 1)
        self.assertEqual(RECORDED, [])

    def test_tracker_polls_updates_and_stores_the_join(self) -> None:
        channel_id = bot.resolve_channel(TOKEN, "@mock_channel")
        self.assertEqual(channel_id, CHAT_ID)
        updates = api_call(TOKEN, "getUpdates", {"offset": 0, "timeout": 0, "allowed_updates": ["chat_member"]})
        store = Store(os.environ["DATABASE_URL"])
        try:
            offset = bot.handle_updates(updates, channel_id, store, 0)  # type: ignore[arg-type]
            offset_again = bot.handle_updates(updates, channel_id, store, offset)
            events = store.fetch_events()
        finally:
            store.close()
        self.assertEqual((offset, offset_again), (6, 6))
        self.assertEqual([(e.kind, e.invite_name) for e in events], [("join", "demo")])  # redelivery stored once

    def test_token_never_appears_in_error_messages(self) -> None:
        from tgcommon import TelegramError
        with self.assertRaises(TelegramError) as ctx:
            api_call(TOKEN, "unknownMethod")
        self.assertNotIn(TOKEN, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
