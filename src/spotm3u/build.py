"""Console entry point behind ``uv run build``.

Produces the distributable application through the project's ``build``
dependency group (see ``pyproject.toml``) without requiring PyInstaller to be
installed in the default environment. The exact PyInstaller invocation and
``SPOTM3U_FFMPEG_DIR`` handling match ``docs/packaging.md``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / "packaging" / "spotm3u.spec"


def main(argv: list[str] | None = None) -> None:
    """Run the PyInstaller build, forwarding any extra arguments to it."""
    env = dict(os.environ)
    ffmpeg_stage = _ROOT / "ffmpeg-stage"
    if ffmpeg_stage.is_dir():
        env.setdefault("SPOTM3U_FFMPEG_DIR", str(ffmpeg_stage))
    uv = shutil.which("uv")
    if uv is None:
        print("uv is required to build spotm3u", file=sys.stderr)
        raise SystemExit(1)
    command = [
        uv,
        "run",
        "--group",
        "build",
        "--",
        "pyinstaller",
        "--noconfirm",
        "--clean",
        str(_SPEC),
        *(argv or []),
    ]
    raise SystemExit(subprocess.call(command, cwd=str(_ROOT), env=env))
