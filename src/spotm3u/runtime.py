"""Runtime environment helpers shared by the launcher and packaging helpers.

Distinguishes a PyInstaller bundle from a normal interpreter and returns the
directories that hold bundled resources and sibling binaries.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def bundle_root() -> Path:
    """Directory holding the packaged application and its sibling binaries.

    ``sys._MEIPASS`` is where PyInstaller extracts a one-file bundle; a
    one-folder bundle keeps the executable here. Falling back to the
    executable directory keeps bundled lookups stable in both layouts.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(sys.executable).resolve().parent


def bundle_roots() -> tuple[Path, ...]:
    """Every runtime directory that may hold bundled resources.

    Collected data files land under ``sys._MEIPASS`` in a one-file bundle and
    under ``<exe>/_internal`` in a one-folder bundle; user-facing files (like a
    hand-placed ``config.toml`` or FFmpeg directory) sit next to the
    executable. Returns the unique candidates in preference order.
    """
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))
    root = Path(sys.executable).resolve().parent
    candidates.extend((root / "_internal", root))
    return tuple(dict.fromkeys(candidates))
