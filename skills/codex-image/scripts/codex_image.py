#!/usr/bin/env python3
"""
codex_image.py - generate images through the Codex (ChatGPT) subscription,
                 WITHOUT an OPENAI_API_KEY and without per-image API billing.

The image is rendered by gpt-image-2. Access is the OAuth login of the Codex CLI
(the same backend the Codex console agent uses), not a paid API key.

SETUP (once)
  1) npm install -g @openai/codex
  2) codex login            (server without a browser: codex login --device-auth)
     The token is saved to ~/.codex/auth.json - that file is the "subscription access".
  3) pip install --upgrade openai     (scripts/run.sh does this in its own venv)

USAGE
    python3 codex_image.py "prompt in English" [quality] [aspect] [ref1 ... ref5]

    quality : low (~45s) | medium (default, ~1-2 min) | high (~2-3 min)
    aspect  : landscape (1536x1024) | square (1024x1024) | portrait (1024x1536)
    refN    : up to 5 reference images (png/jpg/webp) - style, character or logo,
              sent to the model as input_image data URLs together with the prompt

The script prints the path of the finished PNG (default: ~/.codex/cache/images/).

NOTES (verified in practice, otherwise you get errors)
  * The OAuth token is data["tokens"]["access_token"] in ~/.codex/auth.json.
  * `input` is a LIST of message objects, not a bare string.
  * stream=True is mandatory (HTTP 400 otherwise), so the SSE stream is read via the openai SDK.
  * store=False is mandatory (HTTP 400 from the Codex backend otherwise).
  * The host model (default below, override with env CODEX_IMAGE_HOST_MODEL) calls the
    image_generation tool.
  * References work: up to 5 images as content blocks
    {"type":"input_image","image_url":"data:<mime>;base64,...","detail":"auto"}
    next to input_text. tool_choice={"type":"image_generation"} makes the host model draw at once.
    Technique from ningzimu/codex-gpt-image.
  * Do NOT call openai.images.generate() / client.images: that is the API-key path and the
    OAuth subscription rejects it. Only Responses API + the image_generation tool.
  * The quota is shared with the subscription (not unlimited, but enough for a content flow).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
HOST_MODEL = os.environ.get("CODEX_IMAGE_HOST_MODEL", "gpt-6.1-sol")  # latest frontier GPT model that calls the image tool
IMAGE_MODEL = "gpt-image-2"
INSTRUCTIONS = (
    "You are an assistant that must fulfill image generation "
    "requests by using the image_generation tool when provided."
)
SIZES = {
    "landscape": "1536x1024",
    "square": "1024x1024",
    "portrait": "1024x1536",
}
AUTH_FILE = Path.home() / ".codex" / "auth.json"
CACHE_DIR = Path.home() / ".codex" / "cache" / "images"


def _load_auth() -> tuple[str, str]:
    """Read the subscription OAuth token from ~/.codex/auth.json (created by `codex login`)."""
    if not AUTH_FILE.exists():
        raise RuntimeError(f"Codex auth не найден: {AUTH_FILE}. Запусти `codex login`.")
    data = json.loads(AUTH_FILE.read_text())
    tokens = data.get("tokens") or {}
    token = tokens.get("access_token")
    account_id = tokens.get("account_id", "")
    if not token:
        raise RuntimeError("Нет tokens.access_token в ~/.codex/auth.json")
    return token, account_id


def _client(token: str, account_id: str) -> OpenAI:
    """openai client pointed at the Codex backend, authorized with the subscription OAuth token."""
    headers = {
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return OpenAI(
        base_url=CODEX_BASE_URL,
        api_key=token,            # the OAuth access_token goes here, NOT an sk-... key
        default_headers=headers,
    )


def _extract_b64(obj) -> Optional[str]:
    """Walk an event payload recursively and return the longest base64 image string."""
    best: Optional[str] = None

    def visit(node):
        nonlocal best
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("result", "partial_image_b64", "b64_json", "image") and isinstance(v, str) and len(v) > 100:
                    if best is None or len(v) > len(best):
                        best = v
                else:
                    visit(v)
        elif isinstance(node, list):
            for it in node:
                visit(it)

    visit(obj)
    return best


MAX_REFS = 5
MAX_REF_BYTES = 15 * 1024 * 1024        # per file, pre-base64 (data URL adds ~33%)
MAX_REFS_TOTAL_BYTES = 40 * 1024 * 1024  # all refs combined
ALLOWED_REF_MIME = {"image/png", "image/jpeg", "image/webp"}


def _ref_block(path: Path) -> dict:
    """Build an input_image content block from a local image file (data URL)."""
    if not path.is_file():
        raise RuntimeError(f"Референс не найден или не файл: {path}")
    mime = mimetypes.guess_type(str(path))[0]
    if mime not in ALLOWED_REF_MIME:
        raise RuntimeError(
            f"Референс {path.name}: тип {mime or 'неизвестен'} не поддерживается "
            f"(только png/jpg/webp)")
    size = path.stat().st_size
    if size > MAX_REF_BYTES:
        raise RuntimeError(
            f"Референс {path.name}: {size // 1024 // 1024} MB > лимита "
            f"{MAX_REF_BYTES // 1024 // 1024} MB — ужми картинку")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "input_image",
            "image_url": f"data:{mime};base64,{b64}",
            "detail": "auto"}


def generate(prompt: str, quality: str = "medium", aspect: str = "landscape",
             refs: list[Path] | None = None) -> Path:
    token, account_id = _load_auth()
    client = _client(token, account_id)
    size = SIZES.get(aspect, SIZES["landscape"])

    refs = refs or []
    if len(refs) > MAX_REFS:
        raise RuntimeError(f"Максимум {MAX_REFS} референсов, передано {len(refs)}")
    total = sum(p.stat().st_size for p in refs if p.is_file())
    if total > MAX_REFS_TOTAL_BYTES:
        raise RuntimeError(
            f"Референсы суммарно {total // 1024 // 1024} MB > лимита "
            f"{MAX_REFS_TOTAL_BYTES // 1024 // 1024} MB")
    content = [{"type": "input_text", "text": prompt}]
    content += [_ref_block(p) for p in refs]

    stream = client.responses.create(
        model=HOST_MODEL,
        instructions=INSTRUCTIONS,
        input=[{
            "type": "message",
            "role": "user",
            "content": content,
        }],
        tools=[{
            "type": "image_generation",
            "model": IMAGE_MODEL,
            "quality": quality,
            "size": size,
        }],
        tool_choice={"type": "image_generation"},  # draw immediately, no reasoning step
        store=False,    # mandatory
        stream=True,    # mandatory
    )

    # The image arrives in chunks in the event stream: keep the fullest frame.
    image_b64: Optional[str] = None
    for event in stream:
        try:
            payload = event.model_dump()
        except Exception:
            payload = getattr(event, "__dict__", {})
        found = _extract_b64(payload)
        if found and (image_b64 is None or len(found) > len(image_b64)):
            image_b64 = found

    if not image_b64:
        raise RuntimeError("В потоке не пришло изображение")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CACHE_DIR / f"img_{int(time.time())}.png"
    out_path.write_bytes(base64.b64decode(image_b64))
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: codex_image.py 'prompt' [quality:low|medium|high] "
              "[aspect:landscape|square|portrait] [ref1.png ... ref5.png]")
        sys.exit(2)
    p = sys.argv[1]
    rest = sys.argv[2:]
    # Foot-gun guard: quality/aspect are optional positionals, so a ref passed
    # as the 2nd arg must not be eaten as "quality". Anything that exists as a
    # file is a ref; the first two non-file args are quality and aspect.
    opts = [x for x in rest if not Path(x).is_file()]
    refs = [Path(x) for x in rest if Path(x).is_file()]
    if len(opts) > 2:
        # A third non-file arg is almost always a TYPO'D ref path — refuse
        # loudly instead of silently generating without the reference.
        print(f"Ошибка: лишние аргументы {opts[2:]} — это опечатка в пути "
              f"референса? Файл(ы) не найдены на диске", file=sys.stderr)
        sys.exit(2)
    q = opts[0] if len(opts) > 0 else "medium"
    a = opts[1] if len(opts) > 1 else "landscape"
    if q not in ("low", "medium", "high"):
        print(f"Ошибка: quality '{q}' не из low|medium|high "
              f"(если это путь референса — файла нет на диске)", file=sys.stderr)
        sys.exit(2)
    if a not in SIZES:
        print(f"Ошибка: aspect '{a}' не из {'|'.join(SIZES)}", file=sys.stderr)
        sys.exit(2)
    try:
        print(generate(p, q, a, refs=refs))
    except RuntimeError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
