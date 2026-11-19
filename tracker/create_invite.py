#!/usr/bin/env python3
"""Create a named invite link for the channel (one link per traffic source).

Usage:
    python3 tracker/create_invite.py <name> [--limit N] [--expire-days N] [--request] [--new]
    python3 tracker/create_invite.py --list

The name is what you see in the report ("instagram_bio", "youtube_desc", "friend_anna").
Telegram allows names of up to 32 characters. If a link with this name was already created
here, it is printed again instead of making a duplicate (use --new to force a new one).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import Store  # noqa: E402
from tgcommon import ConfigError, TelegramError, api_call, load_env, require_env, setup_logging  # noqa: E402

LOG = logging.getLogger("tracker.invite")

MAX_NAME_LENGTH = 32  # Telegram limit for the link name
MAX_MEMBER_LIMIT = 99999  # Telegram limit for member_limit


class InviteNameError(ValueError):
    """The requested link name cannot be used."""


def normalize_name(raw: str) -> str:
    """Clean a link name: trim, collapse whitespace, enforce Telegram's 32-char limit.

    Args:
        raw: Name as typed by the user or the agent.

    Returns:
        The normalized name.

    Raises:
        InviteNameError: If the name is empty, has control characters or is too long.
    """
    # Tabs and newlines are plain whitespace (collapsed below); other control chars are rejected.
    if any((ord(ch) < 32 and not ch.isspace()) or ord(ch) == 127 for ch in raw):
        raise InviteNameError("name contains control characters")
    name = re.sub(r"\s+", " ", raw).strip()
    if not name:
        raise InviteNameError("name is empty")
    if len(name) > MAX_NAME_LENGTH:
        raise InviteNameError(f"name is {len(name)} characters, Telegram allows {MAX_NAME_LENGTH}")
    return name


def build_params(
    channel: str,
    name: str,
    member_limit: int | None = None,
    expire_days: float | None = None,
    creates_join_request: bool = False,
    now: float | None = None,
) -> dict[str, object]:
    """Build createChatInviteLink parameters.

    Args:
        channel: @username or numeric chat id.
        name: Already normalized link name.
        member_limit: Max members who can join via this link (1..99999).
        expire_days: Link lifetime in days.
        creates_join_request: Require admin approval (cannot be combined with member_limit).
        now: Current unix time (for tests).

    Raises:
        InviteNameError: On contradictory or out-of-range options.
    """
    params: dict[str, object] = {"chat_id": channel, "name": name}
    # Default limit 99999: links without a member_limit sometimes break, so every
    # link gets one unless it requires admin approval (Telegram forbids both).
    if member_limit is None and not creates_join_request:
        member_limit = MAX_MEMBER_LIMIT
    if member_limit is not None:
        if not 1 <= member_limit <= MAX_MEMBER_LIMIT:
            raise InviteNameError(f"limit must be 1..{MAX_MEMBER_LIMIT}")
        if creates_join_request:
            raise InviteNameError("--request cannot be combined with --limit")
        params["member_limit"] = member_limit
    if expire_days is not None:
        if expire_days <= 0:
            raise InviteNameError("expire-days must be positive")
        params["expire_date"] = int((now if now is not None else time.time()) + expire_days * 86400)
    if creates_join_request:
        params["creates_join_request"] = True
    return params


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Create a named invite link for the channel")
    parser.add_argument("name", nargs="?", help="link name, e.g. instagram_bio (max 32 chars)")
    parser.add_argument("--limit", type=int, help="max number of members for this link (default 99999)")
    parser.add_argument("--expire-days", type=float, help="link lifetime in days")
    parser.add_argument("--request", action="store_true", help="members must be approved by an admin")
    parser.add_argument("--new", action="store_true", help="create a new link even if the name exists")
    parser.add_argument("--list", action="store_true", help="list links created with this tool")
    args = parser.parse_args(argv)

    setup_logging()
    load_env()
    store = Store(os.environ.get("DATABASE_URL", ""))
    try:
        if args.list:
            for name, url, created in store.list_links():
                print(f"{name}\t{url}\t{created}")
            return 0
        if not args.name:
            parser.error("name is required (or use --list)")
        try:
            name = normalize_name(args.name)
            existing = None if args.new else store.find_link_by_name(name)
            if existing:
                print(f"{name}\t{existing}\t(already exists)")
                return 0
            token, channel = require_env("BOT_TOKEN"), require_env("CHANNEL")
            params = build_params(channel, name, args.limit, args.expire_days, args.request)
            result = api_call(token, "createChatInviteLink", params)
        except (InviteNameError, ConfigError) as exc:
            LOG.error("%s", exc)
            return 2
        except TelegramError as exc:
            LOG.error("Telegram refused: %s", exc)
            return 1
        url = result.get("invite_link") if isinstance(result, dict) else None
        if not isinstance(url, str):
            LOG.error("unexpected answer from Telegram")
            return 1
        store.add_link(url, name)
        print(f"{name}\t{url}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
