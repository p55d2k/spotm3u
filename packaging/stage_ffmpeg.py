"""Copy the platform's FFmpeg/ffprobe binaries into a bundle staging directory.

The PyInstaller spec bundles FFmpeg by copying ``SPOTM3U_FFMPEG_DIR`` into the
output as an ``ffmpeg`` directory. This helper snapshots real (un-symlinked)
binaries from ``PATH`` so CI and local builds get a self-contained staging
directory regardless of how FFmpeg was installed.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

BIN_NAMES = ("ffmpeg", "ffprobe")


def stage_ffmpeg(out_dir: str | Path) -> list[Path]:
    """Copy ``ffmpeg``/``ffprobe`` from ``PATH`` into ``out_dir``.

    Binary contents are copied, never symlinked, so a Homebrew-style install
    yields real files usable by the frozen app. Raises ``SystemExit`` when a
    required binary is missing.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    for name in BIN_NAMES:
        source = shutil.which(name)
        if not source:
            raise SystemExit(f"{name}: not found on PATH; install FFmpeg first")
        target = out / Path(source).name
        shutil.copyfile(source, target)
        staged.append(target)
    return staged


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "ffmpeg-stage"
    targets = stage_ffmpeg(out_dir)
    print(f"staged {len(targets)} binaries into {Path(out_dir).resolve()}")
