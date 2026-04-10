#!/usr/bin/env python3
"""
PhotoSort — NAS/system-Python entry point.

    python mainnas.py           → launch GUI
    python mainnas.py <src> <dst> [--dry-run] [--format ...] [--priority ...]  → CLI

Assumes all dependencies (Pillow, customtkinter, hachoir) are already installed.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).parent / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if len(sys.argv) > 1:
    from photosort.cli import main
    main()
else:
    from photosort.gui.app import main
    main()
