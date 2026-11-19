#!/usr/bin/env python3
"""Publish a post to the channel from a text or HTML file through the Bot API.

Usage:
    python3 poster/post.py draft.html --dry-run                 # check + show what would be sent
    python3 poster/post.py draft.html --photo cover.png --yes   # publish for real

The file is Telegram HTML (see FORMATTING.md). Use --plain for a file with ordinary text
(it is escaped for you). Publishing needs --yes (or an interactive "y"); an agent should
always run --dry-run first and show the result to the owner of the channel.

With a photo: if the text fits a caption (1024 visible characters) it is sent as the caption,
otherwise the photo goes first and the text follows as a separate message.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check import MAX_CAPTION, check_post, format_issues, has_errors, visible_length  # noqa: E402
from tgcommon import ConfigError, TelegramError, REPO_ROOT, api_call, load_env, require_env, setup_logging  # noqa: E402

LOG = logging.getLogger("poster.post")

POSTS_LOG = REPO_ROOT / "data" / "posts.jsonl"
PHOTO_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # Bot API limit for sendPhoto uploads


@dataclass
class Request:
    """One Bot API call prepared for sending."""

    method: str
    params: dict[str, object]
    files: dict[str, Path] = field(default_factory=dict)


def parse_button(spec: str) -> dict[str, str]:
    """Parse "Button text|https://url" into an inline keyboard button.

    Raises:
        ValueError: If the format or the URL is wrong.
    """
    text, sep, url = spec.partition("|")
    text, url = text.strip(), url.strip()
    if not sep or not text or not url:
        raise ValueError(f"button must look like «Text|https://url», got: {spec!r}")
    if not re.match(r"(?i)https?://\S+$", url):
        raise ValueError(f"button URL must start with http:// or https://, got: {url!r}")
    if len(text) > 64:
        raise ValueError("button text is longer than 64 characters")
    return {"text": text, "url": url}


def keyboard(buttons: list[dict[str, str]]) -> dict[str, object] | None:
    """One button per row, as an inline keyboard (None if there are no buttons)."""
    return {"inline_keyboard": [[b] for b in buttons]} if buttons else None


def build_requests(
    chat: str,
    text: str,
    photo: Path | None = None,
    buttons: list[dict[str, str]] | None = None,
    silent: bool = False,
    no_preview: bool = False,
) -> list[Request]:
    """Turn a post into the list of Bot API calls (pure, no network).

    Args:
        chat: Channel @username or numeric id.
        text: Telegram HTML body.
        photo: Optional image file.
        buttons: Optional inline buttons (attached to the last message).
        silent: Send without a notification sound.
        no_preview: Disable the link preview.
    """
    markup = keyboard(buttons or [])
    common: dict[str, object] = {"chat_id": chat, "parse_mode": "HTML", "disable_notification": silent or None}
    if photo is None:
        params = {**common, "text": text, "disable_web_page_preview": no_preview or None, "reply_markup": markup}
        return [Request("sendMessage", params)]
    if visible_length(text) <= MAX_CAPTION:
        params = {**common, "caption": text, "reply_markup": markup}
        return [Request("sendPhoto", params, {"photo": photo})]
    first = Request("sendPhoto", {"chat_id": chat, "disable_notification": silent or None}, {"photo": photo})
    second = Request("sendMessage", {**common, "text": text, "disable_web_page_preview": no_preview or None, "reply_markup": markup})
    return [first, second]


def read_post(path: Path, plain: bool) -> str:
    """Read the post file; with plain=True escape it into safe HTML."""
    text = path.read_text(encoding="utf-8").strip("\n")
    return html.escape(text, quote=False) if plain else text


def validate_photo(photo: Path) -> None:
    """Raise ValueError if the photo cannot be uploaded."""
    if not photo.is_file():
        raise ValueError(f"photo not found: {photo}")
    if photo.suffix.lower() not in PHOTO_SUFFIXES:
        raise ValueError("photo must be png, jpg or webp")
    if photo.stat().st_size > MAX_PHOTO_BYTES:
        raise ValueError("photo is larger than 10 MB: compress it")


def describe(requests: list[Request]) -> str:
    """Short human description of what will be sent."""
    lines = []
    for n, req in enumerate(requests, 1):
        body = str(req.params.get("text") or req.params.get("caption") or "")
        extras = [k for k in ("reply_markup", "disable_notification", "disable_web_page_preview") if req.params.get(k)]
        files = ", ".join(f"{k}={p.name}" for k, p in req.files.items())
        lines.append(f"  {n}. {req.method}  chars={visible_length(body) if body else 0}"
                     f"{'  file: ' + files if files else ''}{'  ' + ','.join(extras) if extras else ''}")
    return "\n".join(lines)


def post_url(chat: str, message_id: int) -> str | None:
    """Public link of the message if the channel has a @username."""
    return f"https://t.me/{chat.lstrip('@')}/{message_id}" if chat.startswith("@") else None


def log_post(chat: str, message_id: int, text: str, path: Path) -> None:
    """Append a line to data/posts.jsonl so later reports can match posts by id."""
    POSTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    first_line = next((ln for ln in html.unescape(re.sub(r"<[^>]+>", "", text)).splitlines() if ln.strip()), "")
    entry = {
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chat": chat,
        "message_id": message_id,
        "title": first_line[:80],
        "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
        "source": path.name,
    }
    with POSTS_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Publish a post to the Telegram channel")
    parser.add_argument("file", type=Path, help="post text: Telegram HTML (or plain text with --plain)")
    parser.add_argument("--photo", type=Path, help="image to attach (png/jpg/webp)")
    parser.add_argument("--button", action="append", default=[], metavar="TEXT|URL", help="inline link button, repeatable")
    parser.add_argument("--plain", action="store_true", help="the file is plain text, escape it")
    parser.add_argument("--silent", action="store_true", help="no notification sound")
    parser.add_argument("--no-preview", action="store_true", help="disable the link preview")
    parser.add_argument("--chat", help="override CHANNEL from .env (e.g. a test channel)")
    parser.add_argument("--dry-run", action="store_true", help="check and show, send nothing")
    parser.add_argument("--yes", action="store_true", help="publish without the interactive question")
    args = parser.parse_args(argv)

    setup_logging()
    load_env()
    try:
        text = read_post(args.file, args.plain)
        buttons = [parse_button(spec) for spec in args.button]
        if args.photo:
            validate_photo(args.photo)
    except (OSError, ValueError) as exc:
        LOG.error("%s", exc)
        return 2

    issues = check_post(text, has_photo=args.photo is not None)
    print("Pre-publish check:")
    print(format_issues(issues))
    if has_errors(issues):
        print("Not published: fix the errors above.")
        return 1

    channel = args.chat or os.environ.get("CHANNEL", "")
    requests = build_requests(channel or "<CHANNEL>", text, args.photo, buttons, args.silent, args.no_preview)
    print(f"Channel: {channel or '<not set>'}")
    print("Would send:" if args.dry_run else "Sending:")
    print(describe(requests))
    if args.dry_run:
        print("Dry run: nothing was sent.")
        return 0

    try:
        token = require_env("BOT_TOKEN")
        channel = args.chat or require_env("CHANNEL")
    except ConfigError as exc:
        LOG.error("%s", exc)
        return 2
    requests = build_requests(channel, text, args.photo, buttons, args.silent, args.no_preview)
    if not args.yes:
        if not sys.stdin.isatty():
            print("Not published: pass --yes to confirm (no terminal to ask).")
            return 1
        if input(f"Publish to {channel}? [y/N] ").strip().lower() != "y":
            print("Cancelled.")
            return 1

    last_id: int | None = None
    for req in requests:
        try:
            result = api_call(token, req.method, req.params, req.files or None, timeout=120)
        except TelegramError as exc:
            LOG.error("Telegram refused: %s", exc)
            return 1
        if isinstance(result, dict) and isinstance(result.get("message_id"), int):
            last_id = result["message_id"]
            print(f"Sent: message_id={last_id} {post_url(channel, last_id) or ''}".rstrip())
    if last_id is not None:
        log_post(channel, last_id, text, args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
