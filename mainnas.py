#!/usr/bin/env python3
"""
PhotoSort — NAS/system-Python CLI entry point.

    python mainnas.py <src> <dst> [--dry-run] [--format ...] [--priority ...]

Assumes all dependencies (Pillow, hachoir) are already installed.
No GUI, no venv bootstrap.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).parent / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Always require explicit YES confirmation on NAS — safety net before moving files.
if "--confirm" not in sys.argv:
    sys.argv.append("--confirm")

from photosort.cli import main
main()
