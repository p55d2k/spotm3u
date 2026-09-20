"""Console entry point behind ``uv run build``.

Produces the distributable application through the project's ``build``
dependency group (see ``pyproject.toml``) without requiring PyInstaller to be
installed in the default environment. The exact PyInstaller invocation and
``SPOTM3U_FFMPEG_DIR`` handling match ``docs/packaging.md``.

The GitHub Actions release workflow runs the same command, so a local
``uv run build`` produces the same kind of application as a release build. It
always builds for the current platform; cross-compilation is not supported.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / "packaging" / "spotm3u.spec"
_FFMPEG_STAGE = _ROOT / "ffmpeg-stage"
_ICON_GENERATOR = _ROOT / "packaging" / "generate_icons.py"
_ICON_PNG = _ROOT / "assets" / "icon.png"


def build_command(argv: list[str] | None = None) -> list[str]:
    """The PyInstaller command the project builds the application with.

    The command lives here so ``uv run build`` and GitHub Actions share a
    single source of truth instead of duplicating packaging flags.
    """
    uv = shutil.which("uv")
    if uv is None:
        print("uv is required to build spotm3u", file=sys.stderr)
        raise SystemExit(1)
    return [
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


def artifact_paths() -> list[Path]:
    """The distributable path(s) PyInstaller writes into ``dist/``."""
    paths = [_ROOT / "dist" / "spotm3u"]
    if sys.platform == "darwin":
        paths.append(_ROOT / "dist" / "spotm3u.app")
    return paths


def _display(path: Path) -> str:
    """The user-facing location of an artifact, relative to the repo root."""
    try:
        return str(path.relative_to(_ROOT))
    except ValueError:
        return str(path)


def report_success() -> None:
    """Print a concise pointer to the produced artifact."""
    built = [path for path in artifact_paths() if path.exists()]
    print("Build complete.")
    print()
    print("Artifact:")
    if built:
        for path in built:
            print(_display(path))
    else:
        print(_display(_ROOT / "dist"))


def generate_icons() -> None:
    """Regenerate platform icon formats from ``assets/icon.png``.

    `assets/icon.png` is the canonical icon; Windows (.ico) and macOS (.icns)
    packaging formats are derived from it before PyInstaller runs so the spec
    never needs a manually maintained icon source. The generator is pure
    standard library, so it behaves identically locally and in GitHub Actions.
    """
    if not _ICON_PNG.is_file():
        raise SystemExit(f"missing application icon: {_ICON_PNG}")
    status = subprocess.call([sys.executable, str(_ICON_GENERATOR)])
    if status != 0:
        print(f"Icon generation failed (exit status {status}).", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    """Run the PyInstaller build, forwarding any extra arguments to it."""
    env = dict(os.environ)
    if _FFMPEG_STAGE.is_dir():
        env.setdefault("SPOTM3U_FFMPEG_DIR", str(_FFMPEG_STAGE))
    generate_icons()
    status = subprocess.call(build_command(argv), cwd=str(_ROOT), env=env)
    if status != 0:
        print(f"Build failed (exit status {status}).", file=sys.stderr)
        raise SystemExit(status)
    report_success()
