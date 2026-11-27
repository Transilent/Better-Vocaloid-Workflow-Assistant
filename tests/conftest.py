"""Pytest bootstrap for external contributors (compatible with VWA.ps1 test / run_tests.py)."""
from __future__ import annotations

import os
from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = APP_ROOT / "src"

os.environ.setdefault("VWA_TESTING", "1")
os.environ.setdefault("BVWA_TESTING", "1")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

for entry in (str(APP_ROOT), str(SRC_DIR), str(APP_ROOT / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)
