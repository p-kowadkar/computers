"""Shared test helpers (synthetic tiles, fake servers) live in tests/unit; make them importable everywhere."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "unit"))
