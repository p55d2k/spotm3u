"""Console entry point behind ``uv run build``.

Produces the distributable application through the project's ``build``
dependency group (see ``pyproject.toml``) without requiring PyInstaller to be
installed in the default environment. The exact PyInstaller invocation and
``SPOTM3U_FFMPEG_DIR`` handling match ``docs/packaging.md``.

``uv run build`` is the single command for a complete distributable: it
regenerates the platform icons, builds the React frontend (Vite), and only then
runs PyInstaller, which packs that frontend build into the bundle. Node is a
build-time tool only; the packaged application never needs it.

The GitHub Actions release workflow runs the same command, so a local
``uv run build`` produces the same kind of application as a release build. It
always builds for the current platform; cross-compilation is not supported.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import frontend

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / "packaging" / "spotm3u.spec"
_FFMPEG_STAGE = _ROOT / "ffmpeg-stage"
_ICON_GENERATOR = _ROOT / "packaging" / "generate_icons.py"
_ICON_PNG = _ROOT / "assets" / "icon.png"
_MAKE_PKG = _ROOT / "packaging" / "make_pkg.py"
# Mirrors the SPOTM3U_APP_VERSION default in packaging/spotm3u.spec.
_VERSION_DEFAULT = "0.0.0"

# Packaging from the existing frontend build without Node. Combined with
# ``frontend/dist`` this is how the spec is iterated on without rebuilding the
# UI every time; a release never sets it.
SKIP_FRONTEND_ENV = "SPOTM3U_SKIP_FRONTEND"


def build_command(argv: list[str] | None = None) -> list[str]:
    """The PyInstaller command the project builds the application with.

    The command lives here so ``uv run build`` and GitHub Actions share a
    single source of truth instead of duplicating packaging flags.
    """
    uv = shutil.which("uv")
    if uv is None:
        print("uv is required to build SpotM3U", file=sys.stderr)
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
    paths = [_ROOT / "dist" / "SpotM3U"]
    if sys.platform == "darwin":
        paths.append(_ROOT / "dist" / "SpotM3U.app")
    return paths


def _package_macos(version: str) -> list[Path]:
    """Wrap the fresh ``dist/SpotM3U.app`` in an installer package.

    Runs the same packaging script the release workflow uses, so a local
    ``uv run build`` on macOS produces a release-identical install artifact.
    It is written next to the app in ``dist/``.
    """
    app = _ROOT / "dist" / "SpotM3U.app"
    if not app.is_dir():
        return []
    if sys.platform != "darwin":
        return []
    stem = _ROOT / "dist" / f"SpotM3U-v{version}-macos-{platform.machine()}"
    dest = stem.with_suffix(".pkg")
    status = subprocess.call(
        [sys.executable, str(_MAKE_PKG), str(app), str(dest), "--version", version]
    )
    if status != 0:
        print(f"Packaging failed (exit status {status}) for {_MAKE_PKG.name}.", file=sys.stderr)
        raise SystemExit(status)
    return [dest]


def _display(path: Path) -> str:
    """The user-facing location of an artifact, relative to the repo root."""
    try:
        return str(path.relative_to(_ROOT))
    except ValueError:
        return str(path)


def report_success(extra: list[Path] | None = None) -> None:
    """Print a concise pointer to the produced artifacts."""
    built = [path for path in artifact_paths() if path.exists()] + (extra or [])
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


def build_frontend() -> None:
    """Build the React frontend so the bundle carries the current UI.

    The dependencies are installed from the committed lockfile first, so the
    build is reproducible instead of depending on whatever happens to be in
    ``frontend/node_modules``, and Vite runs through the project's own script
    (``npm run build``), which type-checks before it bundles. Set
    ``SPOTM3U_SKIP_FRONTEND=1`` to package the existing ``frontend/dist``
    without Node; the spec refuses to build a bundle without a frontend build.
    """
    if os.environ.get(SKIP_FRONTEND_ENV) == "1":
        print(f"Keeping the existing frontend build ({SKIP_FRONTEND_ENV}=1).")
        return
    try:
        install = frontend.install_command()
        build = frontend.build_command()
    except frontend.FrontendError as error:
        print(
            f"{error} Set {SKIP_FRONTEND_ENV}=1 to package without rebuilding the frontend.",
            file=sys.stderr,
        )
        raise SystemExit(1) from error
    for command, description in (
        (install, "Frontend dependency install"),
        (build, "Frontend build"),
    ):
        status = subprocess.call(command, cwd=str(frontend.FRONTEND_DIR))
        if status != 0:
            print(f"{description} failed (exit status {status}).", file=sys.stderr)
            raise SystemExit(status)
    index = frontend.FRONTEND_DIR / frontend.DIST_DIRNAME / frontend.INDEX
    referenced_assets = re.findall(r'(?:src|href)="([^"]+)"', index.read_text(encoding="utf-8"))
    missing = [
        asset
        for asset in referenced_assets
        if asset.startswith("/") and not (index.parent / asset.lstrip("/")).is_file()
    ]
    if missing:
        raise SystemExit(f"frontend build references missing assets: {', '.join(missing)}")


def main(argv: list[str] | None = None) -> None:
    """Run the PyInstaller build, forwarding any extra arguments to it."""
    env = dict(os.environ)
    if _FFMPEG_STAGE.is_dir():
        env.setdefault("SPOTM3U_FFMPEG_DIR", str(_FFMPEG_STAGE))
    generate_icons()
    build_frontend()
    status = subprocess.call(build_command(argv), cwd=str(_ROOT), env=env)
    if status != 0:
        print(f"Build failed (exit status {status}).", file=sys.stderr)
        raise SystemExit(status)
    packaged = _package_macos(os.environ.get("SPOTM3U_APP_VERSION", _VERSION_DEFAULT))
    report_success(packaged)
