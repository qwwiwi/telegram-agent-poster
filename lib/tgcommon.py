"""Shared helpers: .env loading and a tiny stdlib-only Telegram Bot API client.

No third-party dependencies on purpose: the whole project runs on plain Python 3.11+.
The bot token is read from the environment (or .env) and never printed or logged.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import uuid
import urllib.error
import urllib.request
from pathlib import Path

LOG = logging.getLogger("tgcommon")

REPO_ROOT = Path(__file__).resolve().parent.parent
API_URL = "https://api.telegram.org"
DEFAULT_TIMEOUT = 30


class TelegramError(Exception):
    """Raised when the Bot API answers ok=false or the request cannot be completed."""


class ConfigError(Exception):
    """Raised when a required setting is missing."""


def parse_env_text(text: str) -> dict[str, str]:
    """Parse the text of a .env file into a dict.

    Supports KEY=VALUE lines, `export KEY=VALUE`, comments (#) and single or double quotes.

    Args:
        text: Raw file content.

    Returns:
        Mapping of variable names to values (unquoted, stripped).
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def load_env(path: Path | None = None) -> None:
    """Load .env into os.environ without overriding variables that are already set.

    Args:
        path: Explicit file. Defaults to $ENV_FILE or <repo root>/.env.
    """
    env_path = path or Path(os.environ.get("ENV_FILE", REPO_ROOT / ".env"))
    try:
        text = env_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        LOG.debug("no env file at %s, using process environment", env_path)
        return
    for key, value in parse_env_text(text).items():
        os.environ.setdefault(key, value)


def require_env(name: str) -> str:
    """Return a required environment variable or raise ConfigError."""
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("<<"):
        raise ConfigError(f"{name} is not set: fill it in .env (see .env.example)")
    return value


def redact(text: str, token: str) -> str:
    """Remove the bot token from a string before it is logged or shown."""
    return text.replace(token, "<token>") if token else text


def _encode_multipart(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]:
    """Build a multipart/form-data body. Returns (body, content_type)."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for name, path in files.items():
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode()
        parts.append(head + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def api_call(
    token: str,
    method: str,
    params: dict[str, object] | None = None,
    files: dict[str, Path] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> object:
    """Call a Bot API method and return the `result` field.

    Args:
        token: Bot token from @BotFather.
        method: Method name, e.g. "sendMessage".
        params: Method parameters. Non-string values are JSON-encoded.
        files: Optional file uploads (field name -> local path), e.g. {"photo": Path(...)}.
        timeout: Socket timeout in seconds (add the long-poll time for getUpdates).

    Returns:
        The parsed `result` of the response.

    Raises:
        TelegramError: On ok=false, HTTP errors and network failures (token is redacted).
    """
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    base = os.environ.get("TELEGRAM_API_URL", API_URL).rstrip("/")  # override is used by the mock-server test
    url = f"{base}/bot{token}/{method}"
    if files:
        # Multipart form fields are plain strings: nested values are JSON-encoded.
        fields = {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for k, v in clean.items()}
        body, content_type = _encode_multipart(fields, files)
    else:
        body = json.dumps(clean, ensure_ascii=False).encode()
        content_type = "application/json"
    request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.load(exc).get("description", "")
        except (ValueError, OSError):
            pass
        raise TelegramError(redact(f"{method}: HTTP {exc.code} {detail}".strip(), token)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TelegramError(redact(f"{method}: network error: {exc}", token)) from None
    if not isinstance(payload, dict) or not payload.get("ok"):
        description = payload.get("description") if isinstance(payload, dict) else "bad response"
        raise TelegramError(redact(f"{method}: {description}", token))
    return payload.get("result")


def setup_logging(level: int = logging.INFO) -> None:
    """Configure console logging once."""
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
