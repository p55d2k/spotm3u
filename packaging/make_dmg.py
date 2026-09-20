"""Create a macOS application image (DMG) from the packaged ``SpotM3U.app``.

Note: a DMG is a convenience ``drag-to-Applications`` distribution. It does NOT
clear the macOS quarantine problem by itself -- macOS 26 additionally re-stamps
``com.apple.quarantine`` onto files copied out of a quarantined image, so a
browser-downloaded DMG still delivers a quarantined, hanging unsigned app. The
reliable install path is ``packaging/make_pkg.py`` (installed files are written
fresh by the installer and are never quarantined). This script keeps shipping a
DMG for users who explicitly clear quarantine; ``hdiutil`` is the only tool
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
