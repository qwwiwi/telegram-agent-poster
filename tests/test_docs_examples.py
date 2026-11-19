"""The example posts in FORMATTING.md must pass the project's own pre-publish check."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths  # noqa: E402,F401

from check import check_post, has_errors  # noqa: E402

DOC = Path(__file__).resolve().parent.parent / "poster" / "FORMATTING.md"


class DocExamples(unittest.TestCase):
    def test_every_html_example_passes_the_check(self) -> None:
        examples = re.findall(r"```html\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
        self.assertGreaterEqual(len(examples), 3)
        for n, text in enumerate(examples, 1):
            with self.subTest(example=n):
                issues = check_post(text)
                self.assertFalse(has_errors(issues), issues)


    def test_example_drafts_pass_the_check(self) -> None:
        drafts = sorted((DOC.parent / "examples").glob("*.html"))
        self.assertTrue(drafts, "no example drafts found")
        for path in drafts:
            with self.subTest(draft=path.name):
                self.assertFalse(has_errors(check_post(path.read_text(encoding="utf-8"))))


if __name__ == "__main__":
    unittest.main()
