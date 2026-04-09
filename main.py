#!/usr/bin/env python3
"""
PhotoSort — run directly without any manual setup.

    python main.py           → launch GUI
    python main.py <src> <dst> [--dry-run] [--format ...] [--priority ...]  → CLI

A local .venv is created and dependencies are installed automatically on first run.
"""

import os
import subprocess
import sys
from pathlib import Path

_HERE    = Path(__file__).parent
_VENV    = _HERE / ".venv"
_MANAGED = _HERE / ".venv" / ".photosort-ready"  # sentinel file

# ── Bootstrap: create/update venv and re-exec if needed ──────────────────────

def _venv_python() -> Path:
    if sys.platform == "win32":
        return _VENV / "Scripts" / "python.exe"
    return _VENV / "bin" / "python"


def _already_in_managed_venv() -> bool:
    """True when we're already running inside our own managed venv."""
    return _MANAGED.exists() and Path(sys.prefix).resolve() == _VENV.resolve()


def _bootstrap():
    """Create venv + install deps, then re-exec this script inside it."""
    if not _VENV.exists():
        print("Creating virtual environment…")
        subprocess.check_call([sys.executable, "-m", "venv", str(_VENV)])

    venv_py = _venv_python()

    if not _MANAGED.exists():
        print("Installing dependencies (one-time setup)…")
        subprocess.check_call([
            str(venv_py), "-m", "pip", "install", "--quiet",
            "Pillow", "customtkinter", "hachoir",
        ])
        _MANAGED.touch()
        print("Ready.\n")

    # Re-exec this script using the venv Python.
    # os.execv is not available on Windows, so use subprocess + sys.exit instead.
    result = subprocess.run(
        [str(venv_py), str(_HERE / "main.py")] + sys.argv[1:]
    )
    sys.exit(result.returncode)


if not _already_in_managed_venv():
    _bootstrap()
    sys.exit(0)  # unreachable (execv replaces the process), but keeps linters happy

# ── We are now running inside the managed venv ────────────────────────────────

_SRC = _HERE / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if len(sys.argv) > 1:
    from photosort.cli import main
    main()
else:
    try:
        import tkinter  # noqa: F401
    except ModuleNotFoundError:
        import platform
        print("ERROR: Tk is not available in this Python installation.")
        if sys.platform == "darwin":
            ver = platform.python_version_tuple()
            print(f"  Fix: brew install python-tk@{ver[0]}.{ver[1]}")
        elif sys.platform == "win32":
            print("  Fix: reinstall Python from python.org (check 'tcl/tk and IDLE')")
        else:
            print("  Fix: sudo apt install python3-tk  (or equivalent for your distro)")
        sys.exit(1)

    from photosort.gui.app import main
    main()
