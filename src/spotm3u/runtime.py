"""Runtime environment helpers shared by the launcher and packaging helpers.

Distinguishes a PyInstaller bundle from a normal interpreter, reports whether
standard streams exist, and returns the directories that hold bundled resources
and sibling binaries.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def has_console() -> bool:
    """True when the process can report errors on a standard stream.

    A windowed (no-console) PyInstaller build on Windows leaves ``sys.stdout``
    and ``sys.stderr`` at ``None``, so messages there would be lost and a
    graphical error mechanism is needed instead.
    """
    return sys.stdout is not None or sys.stderr is not None


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
    candidates.append(root / "_internal")
    if root.name == "MacOS" and root.parent.name == "Contents":
        candidates.append(root.parent / "Resources")
    candidates.append(root)
    return tuple(dict.fromkeys(candidates))
