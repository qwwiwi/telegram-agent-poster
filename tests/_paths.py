"""Put lib/, tracker/ and poster/ on sys.path so tests can import the scripts as modules."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("lib", "tracker", "poster"):
    path = str(ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)
