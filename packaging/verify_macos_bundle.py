"""Validate the structure of a packaged macOS ``SpotM3U.app`` release artifact.

Catches malformed or flattened macOS bundles -- loose PyInstaller output, a
missing executable, missing FFmpeg, missing application resources, or icon
metadata that macOS would ignore -- without downgrading the release to unsigned
internals. The project intentionally distributes an unsigned, un-notarized
application, so signature and Gatekeeper status are never checked here; only
structural problems fail the build.

The icon checks exist because a bundle can look complete and still be presented
with the generic placeholder icon: an ``Info.plist`` whose ``CFBundleIconFile``
names a file that is not in the bundle, an ``.icns`` that is not a real icon
container, ``LSBackgroundOnly`` -- which makes macOS treat the app as a
background-only process with no Dock tile or Cmd-Tab icon at all -- or a missing
``NSHighResolutionCapable``, which makes macOS scale the icon instead of drawing
its native-resolution representations.

Accepts a release ZIP, an extracted ``SpotM3U.app``, or a directory containing
either.
"""

from __future__ import annotations

import plistlib
import struct
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
    # The built React application Flask serves at ``/``; a bundle without it
    # would show an empty window.
    "frontend/index.html",
)
# The .icns derived from assets/icon.png; PyInstaller copies it into
# Contents/Resources and references it from Info.plist.
_REQUIRED_ICON = "icon.icns"
_FFMPEG_BINARIES = ("ffmpeg", "ffprobe")
ICNS_MAGIC = b"icns"


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"invalid macOS bundle: {message}")


def _validate_icon_metadata(info_plist: dict, has_icon) -> str:
    """Reject icon metadata macOS would not use to present the application.

    Returns the declared icon resource name. ``has_icon`` reports whether a
    bundle resource name is present; it lets the same checks run against an
    extracted bundle and a release archive.
    """
    icon_name = info_plist.get("CFBundleIconFile")
    if not icon_name:
        _fail("Info.plist does not declare CFBundleIconFile")
    if not has_icon(icon_name):
        _fail(f"Info.plist CFBundleIconFile points at a missing icon: {icon_name}")
    if info_plist.get("LSBackgroundOnly"):
        _fail(
            "Info.plist sets LSBackgroundOnly, so macOS would present the app "
            "as a background-only process without its icon"
        )
    if not info_plist.get("NSHighResolutionCapable"):
        _fail(
            "Info.plist does not set NSHighResolutionCapable, so macOS would "
            "scale the icon instead of using its native-resolution representations"
        )
    return str(icon_name)


def _validate_icns(payload: bytes) -> None:
    """Check that ``payload`` is a well-formed .icns container with elements."""
    if len(payload) < 16 or payload[:4] != ICNS_MAGIC:
        _fail(f"bundled {_REQUIRED_ICON} is not an .icns icon container")
    (declared,) = struct.unpack_from(">I", payload, 4)
    if declared != len(payload):
        _fail(
            f"bundled {_REQUIRED_ICON} is truncated: header says {declared} bytes, "
            f"file has {len(payload)}"
        )
    elements = 0
    offset = 8
    while offset + 8 <= len(payload):
        (length,) = struct.unpack_from(">I", payload, offset + 4)
        if length < 8 or offset + length > len(payload):
            _fail(f"bundled {_REQUIRED_ICON} has an invalid element at offset {offset}")
        elements += 1
        offset += length
    if elements == 0:
        _fail(f"bundled {_REQUIRED_ICON} contains no image representations")


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


def validate_app(app: Path) -> str:
    """Validate an extracted ``SpotM3U.app`` application bundle.

    Returns the name of the icon resource the bundle presents.
    """
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
    try:
        metadata = plistlib.loads(info_plist.read_bytes())
    except Exception as error:  # plistlib raises a mix of parse errors
        _fail(f"Info.plist is unreadable: {error}")

    if _find_ffmpeg_directory(app) is None:
        trees = "/".join(_CONTENT_TREES)
        _fail(f"bundled ffmpeg/ffprobe missing from Contents/{{{trees}}}/ffmpeg")

    icon = _find_resource(app, _REQUIRED_ICON)
    if icon is None:
        _fail(f"bundled application icon missing: {_REQUIRED_ICON}")
    _validate_icns(icon.read_bytes())
    icon_name = _validate_icon_metadata(
        metadata, lambda name: _find_resource(app, name) is not None
    )

    for relative in _REQUIRED_RESOURCES:
        if _find_resource(app, relative) is None:
            _fail(f"bundled resource missing: {relative}")
    return icon_name


def _validate_archive(archive: Path) -> None:
    if archive.suffix != ".zip":
        _fail(f"expected a .zip release archive, got {archive}")
    with zipfile.ZipFile(archive) as zf:
        corrupt = zf.testzip()
        if corrupt is not None:
            _fail(f"corrupt archive member: {corrupt}")
        names = set(zf.namelist())
        plist_bytes = zf.read(INFO_PLIST) if INFO_PLIST in names else None
        icons = [name for name in names if Path(name).name == _REQUIRED_ICON]
        icon_bytes = zf.read(icons[0]) if icons else None

    if EXECUTABLE not in names:
        _fail(f"archive does not contain {EXECUTABLE}")
    if plist_bytes is None:
        _fail(f"archive does not contain {INFO_PLIST}")
    if icon_bytes is None:
        _fail(f"archive is missing bundled application icon: {_REQUIRED_ICON}")
    try:
        metadata = plistlib.loads(plist_bytes)
    except Exception as error:  # plistlib raises a mix of parse errors
        _fail(f"archive {INFO_PLIST} is unreadable: {error}")
    _validate_icns(icon_bytes)
    _validate_icon_metadata(
        metadata, lambda name: any(Path(member).name == name for member in names)
    )

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
    icon_name = validate_app(app)
    print(f"macOS bundle validated: {app} (icon: {icon_name})")
