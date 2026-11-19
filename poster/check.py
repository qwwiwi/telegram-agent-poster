"""Pre-publish check for a Telegram HTML post: length, forbidden secrets, HTML validity.

Pure functions, no network. `post.py` runs `check_post` before every send; an agent can also run
`python3 poster/check.py draft.html [--photo]` on its own.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_TEXT = 4096  # visible characters in a text message
MAX_CAPTION = 1024  # visible characters in a photo caption

# Tags accepted by Telegram's HTML parse mode.
ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre",
    "tg-spoiler", "span", "blockquote", "tg-emoji",
}
# Tags that never contain other formatting.
VERBATIM_TAGS = {"code", "pre"}
TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)((?:\s+[^<>]*?)?)\s*(/?)>")
ATTR_RE = re.compile(r'([A-Za-z_:-]+)\s*(?:=\s*("[^"]*"|\'[^\']*\'|[^\s"\'>]+))?')
ENTITY_RE = re.compile(r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);")
HINTS = {
    "br": "use a real line break, Telegram HTML has no <br>",
    "p": "no <p>: separate paragraphs with an empty line",
    "div": "no <div>: use plain text and line breaks",
    "h1": "no headings: use <b>bold</b>",
    "h2": "no headings: use <b>bold</b>",
    "h3": "no headings: use <b>bold</b>",
    "ul": "no HTML lists: write «• item» lines",
    "ol": "no HTML lists: write «1. item» lines",
    "li": "no HTML lists: write «• item» lines",
    "img": "images are sent as a photo, not as a tag",
}

# name -> regex. Built from parts so this file itself stays clean under a secrets scan.
SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "Telegram bot token": re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{30,}"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    "credential assignment": re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[=:]\s*[A-Za-z0-9_./+=-]{8,}"
    ),
    "private chat id": re.compile(r"(?<![\w-])-100\d{8,}\b"),
    "private IP address": re.compile(
        r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"
    ),
    "local file path": re.compile(r"(?:/(?:Users|home)/[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\)"),
}


@dataclass(frozen=True)
class Issue:
    """One finding of the pre-publish check.

    Attributes:
        level: "error" blocks publishing, "warning" is shown but allowed.
        code: Short machine-readable id.
        message: Human-readable explanation.
    """

    level: str
    code: str
    message: str


def visible_text(text: str) -> str:
    """Text as Telegram counts it: tags removed, entities decoded."""
    return html.unescape(TAG_RE.sub("", text))


def visible_length(text: str) -> int:
    """Number of characters Telegram counts against the limits (UTF-16 units are not used here)."""
    return len(visible_text(text))


def check_secrets(text: str) -> list[Issue]:
    """Find strings that look like secrets or private data. Never echoes the match itself."""
    issues = []
    for label, pattern in SECRET_PATTERNS.items():
        match = pattern.search(text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            issues.append(Issue("error", "secret", f"looks like a {label} (line {line}); remove it"))
    return issues


def check_html(text: str) -> list[Issue]:
    """Validate tag names, nesting, attributes and unescaped characters."""
    issues: list[Issue] = []
    stack: list[str] = []
    in_blockquote = False
    pos = 0
    for match in TAG_RE.finditer(text):
        issues += _check_plain(text[pos:match.start()])
        pos = match.end()
        closing, name, attrs, selfclose = match.group(1) == "/", match.group(2).lower(), match.group(3), match.group(4)
        if name not in ALLOWED_TAGS:
            hint = HINTS.get(name, "see FORMATTING.md for supported tags")
            issues.append(Issue("error", "bad-tag", f"<{name}> is not supported by Telegram: {hint}"))
            continue
        if selfclose:
            issues.append(Issue("error", "self-closing", f"<{name}/> is not allowed, close the tag normally"))
            continue
        if closing:
            if not stack or stack[-1] != name:
                expected = f"</{stack[-1]}>" if stack else "nothing"
                issues.append(Issue("error", "nesting", f"unexpected </{name}>, expected {expected}"))
                if name in stack:
                    while stack and stack.pop() != name:
                        pass
            else:
                stack.pop()
            if name == "blockquote":
                in_blockquote = False
            continue
        if stack and stack[-1] in VERBATIM_TAGS and not (stack[-1] == "pre" and name == "code"):
            issues.append(Issue("error", "nesting", f"<{name}> inside <{stack[-1]}> is not allowed"))
        if name == "blockquote":
            if in_blockquote:
                issues.append(Issue("error", "nesting", "nested <blockquote> is not allowed"))
            in_blockquote = True
        issues += _check_attrs(name, attrs)
        stack.append(name)
    issues += _check_plain(text[pos:])
    for name in reversed(stack):
        issues.append(Issue("error", "unclosed", f"<{name}> is never closed"))
    return issues


def _check_plain(chunk: str) -> list[Issue]:
    """Plain text between tags must not contain raw < and unescaped &."""
    issues = []
    if "<" in chunk:
        issues.append(Issue("error", "raw-lt", "a raw '<' in the text: write &lt; (or fix the broken tag)"))
    bare = [m for m in re.finditer(r"&", chunk) if not ENTITY_RE.match(chunk, m.start())]
    if bare:
        issues.append(Issue("warning", "raw-amp", "a bare '&': write &amp; to be safe"))
    return issues


def _check_attrs(tag: str, attrs: str) -> list[Issue]:
    found = {m.group(1).lower(): (m.group(2) or "").strip("\"'") for m in ATTR_RE.finditer(attrs)}
    issues = []
    if tag == "a":
        href = found.get("href", "")
        if not href:
            issues.append(Issue("error", "link", "<a> needs href=\"https://...\""))
        elif not re.match(r"(?i)(https?://|tg://|mailto:)", href):
            issues.append(Issue("error", "link", f"link href must start with https://, got «{href[:30]}»"))
        elif href.lower().startswith("http://"):
            issues.append(Issue("warning", "link", "http:// link: prefer https://"))
    if tag == "span" and found.get("class") != "tg-spoiler":
        issues.append(Issue("error", "bad-attr", "<span> is only allowed as <span class=\"tg-spoiler\">"))
    return issues


def check_post(text: str, has_photo: bool = False) -> list[Issue]:
    """Run all checks on the post text.

    Args:
        text: Post body in Telegram HTML.
        has_photo: True if the post goes with a photo (caption limit 1024 applies first).

    Returns:
        List of Issues. The post may be published when no issue has level "error".
    """
    issues: list[Issue] = []
    if not text.strip():
        return [Issue("error", "empty", "the post text is empty")]
    length = visible_length(text)
    if length > MAX_TEXT:
        issues.append(Issue("error", "too-long", f"{length} visible characters, Telegram allows {MAX_TEXT}"))
    elif has_photo and length > MAX_CAPTION:
        issues.append(Issue(
            "warning", "caption-too-long",
            f"{length} characters do not fit a caption ({MAX_CAPTION}): the photo goes first, the text follows as a message",
        ))
    elif length > 1500:
        issues.append(Issue("warning", "long", f"{length} characters: long posts are read less, consider cutting"))
    issues += check_secrets(text)
    issues += check_html(text)
    return issues


def has_errors(issues: list[Issue]) -> bool:
    """True if any issue blocks publishing."""
    return any(i.level == "error" for i in issues)


def format_issues(issues: list[Issue]) -> str:
    """Render issues for the console."""
    return "\n".join(f"  [{i.level}] {i.code}: {i.message}" for i in issues) or "  no problems found"


def main(argv: list[str] | None = None) -> int:
    """CLI: check a draft file. Exit code 1 if it has errors."""
    parser = argparse.ArgumentParser(description="Pre-publish check for a Telegram HTML post")
    parser.add_argument("file", type=Path)
    parser.add_argument("--photo", action="store_true", help="the post goes with a photo (caption limit)")
    args = parser.parse_args(argv)
    issues = check_post(args.file.read_text(encoding="utf-8"), has_photo=args.photo)
    print(format_issues(issues))
    return 1 if has_errors(issues) else 0


if __name__ == "__main__":
    sys.exit(main())
