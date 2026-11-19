#!/usr/bin/env python3
"""Run every offline test of the project: python3 tests/run_all.py"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    """Discover tests/ and skills/codex-image/scripts/ and run them."""
    sys.path.insert(0, str(ROOT / "tests"))
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for directory in (ROOT / "tests", ROOT / "skills" / "codex-image" / "scripts"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
        suite.addTests(loader.discover(str(directory), pattern="test_*.py", top_level_dir=str(directory)))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
