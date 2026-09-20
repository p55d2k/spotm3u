"""Create a macOS application image (DMG) from the packaged ``SpotM3U.app``.

A ZIP downloaded from the browser carries the ``com.apple.quarantine``
attribute, which is copied onto the extracted ``SpotM3U.app``. On recent macOS
versions a quarantined, unsigned (ad-hoc signed) PyInstaller app then hangs in
``dyld`` on first launch instead of opening: the process stays alive with no
window and no Flask port, invisible in the UI except in Activity Monitor.

The quarry of this script avoids that without signing or notarization: an app
inside a DMG has no quarantine attribute, so dragging it out of the mounted
image produces a copy that launches normally. ``hdiutil`` is the only tool
used, so local and GitHub Actions builds behave identically.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DMG_FORMAT = "UDZO"
VOLUME_NAME = "SpotM3U"


def dmg_command(source_app: Path, dest_dmg: Path) -> list[str]:
    """The ``hdiutil`` invocation that produces ``dest_dmg`` from ``source_app``."""
    return [
        "hdiutil",
        "create",
        "-volname",
        VOLUME_NAME,
        "-srcfolder",
        str(source_app),
        "-ov",
        "-format",
        DMG_FORMAT,
        str(dest_dmg),
    ]


def build_dmg(source_app: str | Path, dest_dmg: str | Path) -> Path:
    """Create ``dest_dmg`` from ``source_app`` and return its path."""
    src = Path(source_app)
    dest = Path(dest_dmg)
    if not src.is_dir():
        raise SystemExit(f"source application does not exist: {src}")
    if src.suffix != ".app":
        raise SystemExit(f"source must be a .app bundle, got: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        dmg_command(src, dest),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"hdiutil failed (exit {result.returncode}): {detail}")
    return dest


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} <SpotM3U.app> <out.dmg>")
    created = build_dmg(sys.argv[1], sys.argv[2])
    print(f"archived {sys.argv[1]} -> {created}")
