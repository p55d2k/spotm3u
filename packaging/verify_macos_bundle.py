"""Validate the structure of a packaged macOS ``SpotM3U.app`` release artifact.

Catches malformed or flattened macOS bundles -- loose PyInstaller output, a
missing executable, missing FFmpeg, or missing application resources -- without
downgrading the release to unsigned internals. The project intentionally
distributes an unsigned, un-notarized application, so signature and Gatekeeper
status are never checked here; only structural problems fail the build.

Accepts a release ZIP, an extracted ``SpotM3U.app``, or a directory containing
either.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from typing import NoReturn

APP_NAME = "SpotM3U.app"
_SYMLINK_MODE = 0o120777
CONTENTS = f"{APP_NAME}/Contents"
EXECUTABLE = f"{CONTENTS}/MacOS/SpotM3U"
INFO_PLIST = f"{CONTENTS}/Info.plist"
# PyInstaller relocates collected files between Contents/Frameworks and
# Contents/Resources while cross-linking the other side, so a required path may
# resolve in either tree (or, for user-facing files, in Contents/MacOS).
_CONTENT_TREES = ("Frameworks", "Resources", "MacOS")
_REQUIRED_RESOURCES = (
    "spotm3u/templates/index.html",
    "spotm3u/static/style.css",
)
# The .icns derived from assets/icon.png; PyInstaller copies it into
# Contents/Resources and references it from Info.plist.
_REQUIRED_ICON = "icon.icns"
_FFMPEG_BINARIES = ("ffmpeg", "ffprobe")


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"invalid macOS bundle: {message}")


def _find_resource(app: Path, relative: str) -> Path | None:
    for tree in _CONTENT_TREES:
        candidate = app / "Contents" / tree / relative
        if candidate.exists():
            return candidate
    return None


def _find_ffmpeg_directory(app: Path) -> Path | None:
    for tree in _CONTENT_TREES:
        directory = app / "Contents" / tree / "ffmpeg"
        if all((directory / name).is_file() for name in _FFMPEG_BINARIES):
            return directory
    return None


def validate_app(app: Path) -> None:
    """Validate an extracted ``SpotM3U.app`` application bundle."""
    app = app.expanduser().resolve()
    if not app.is_dir() or app.name != APP_NAME:
        _fail(f"expected an extracted {APP_NAME} directory, got {app}")

    executable = app / "Contents" / "MacOS" / "SpotM3U"
    if not executable.is_file():
        _fail(f"application executable missing: {executable}")
    if not executable.stat().st_mode & 0o111:
        _fail(f"application executable is not executable: {executable}")

    info_plist = app / "Contents" / "Info.plist"
    if not info_plist.is_file():
        _fail(f"Info.plist missing: {info_plist}")

    if _find_ffmpeg_directory(app) is None:
        trees = "/".join(_CONTENT_TREES)
        _fail(f"bundled ffmpeg/ffprobe missing from Contents/{{{trees}}}/ffmpeg")

    if _find_resource(app, _REQUIRED_ICON) is None:
        _fail(f"bundled application icon missing: {_REQUIRED_ICON}")

    for relative in _REQUIRED_RESOURCES:
        if _find_resource(app, relative) is None:
            _fail(f"bundled resource missing: {relative}")


def _validate_archive(archive: Path) -> None:
    if archive.suffix != ".zip":
        _fail(f"expected a .zip release archive, got {archive}")
    with zipfile.ZipFile(archive) as zf:
        corrupt = zf.testzip()
        if corrupt is not None:
            _fail(f"corrupt archive member: {corrupt}")
        names = set(zf.namelist())

    if EXECUTABLE not in names:
        _fail(f"archive does not contain {EXECUTABLE}")
    if INFO_PLIST not in names:
        _fail(f"archive does not contain {INFO_PLIST}")

    def has_suffix(suffix: str) -> bool:
        return any(name.endswith(suffix) for name in names)

    for tree in _CONTENT_TREES:
        prefix = f"{CONTENTS}/{tree}/ffmpeg/"
        if any(name.startswith(prefix) for name in names):
            for binary in _FFMPEG_BINARIES:
                if not has_suffix(f"/ffmpeg/{binary}"):
                    _fail(f"archive ffmpeg directory is missing {binary}")
            break
    else:
        _fail("archive does not contain a bundled ffmpeg directory")

    for relative in _REQUIRED_RESOURCES:
        if not has_suffix(f"/{relative}"):
            _fail(f"archive is missing bundled resource: {relative}")
    if not has_suffix(f"/{_REQUIRED_ICON}"):
        _fail(f"archive is missing bundled application icon: {_REQUIRED_ICON}")


def _find_app(target: Path) -> Path:
    target = target.expanduser().resolve()
    if target.is_file():
        _validate_archive(target)
        extract_dir = target.parent / f"{target.stem}-verified"
        _extract(target, extract_dir)
        return extract_dir / APP_NAME
    if not target.is_dir():
        _fail(f"not a file or directory: {target}")
    if target.name == APP_NAME and target.is_dir():
        return target
    apps = sorted(path for path in target.rglob(APP_NAME) if path.is_dir())
    if not apps:
        _fail(f"no {APP_NAME} found under {target}")
    if len(apps) > 1:
        _fail(f"multiple {APP_NAME} directories found under {target}")
    return apps[0]


def _extract(archive: Path, into: Path) -> None:
    """Extract a ZIP, restoring Unix permissions and symlink entries."""
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            zf.extract(info, into)
            mode = info.external_attr >> 16
            if mode == _SYMLINK_MODE:
                target = into / info.filename
                link = target.read_text(encoding="utf-8")
                target.unlink()
                target.symlink_to(link)
                continue
            if mode & 0o777:
                (into / info.filename).chmod(mode & 0o777)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <release.zip | {APP_NAME} | dir>")
    app = _find_app(Path(sys.argv[1]))
    validate_app(app)
    print(f"macOS bundle validated: {app}")
