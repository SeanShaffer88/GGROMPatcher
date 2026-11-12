"""Desktop GUI package entry points with graceful handling when tkinter is unavailable."""

from __future__ import annotations

import importlib.util
import sys


def is_tk_available() -> bool:
    """Return True if Python's `tkinter` module is installed."""
    return importlib.util.find_spec("tkinter") is not None


def launch_gui(initial_rom: str | None = None) -> int:
    """Launch the desktop GUI, or print a helpful install message if tkinter is missing."""
    if not is_tk_available():
        print(
            "Error: Python's 'tkinter' module is not installed in this environment.\n"
            "  - Debian/Ubuntu Linux: sudo apt-get install python3-tk\n"
            "  - Fedora/RHEL:         sudo dnf install python3-tkinter\n"
            "  - macOS (Homebrew):    brew install python-tk\n"
            "The CLI remains fully functional: run 'ggrompatcher --help'.",
            file=sys.stderr,
        )
        return 1

    from ggrompatcher.gui.app import run_app

    return run_app(initial_rom=initial_rom)


def main() -> int:
    initial_rom = sys.argv[1] if len(sys.argv) > 1 else None
    return launch_gui(initial_rom=initial_rom)
