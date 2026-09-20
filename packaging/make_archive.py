"""Zip a PyInstaller build output for release upload.

Archives `SpotM3U/<exe> + _internal/...` so the unpacked ZIP contains the
standalone bundle root, and archives `SpotM3U.app/...` on macOS so the unpacked
ZIP contains the application bundle itself (never its loose internal files).

PyInstaller's macOS .app and POSIX onedir builds use symbolic links to share
collected files between directories. ``zipfile.write`` follows symlinks and
would either duplicate or drop them, so symlinks are stored as real ZIP symlink
entries here; macOS's Archive Utility and Info-ZIP's ``unzip`` restore them on
extraction.
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

_SYMLINK_MODE = 0o120777


def _entries(src: Path) -> list[tuple[Path, bool]]:
    """Return every file and symlink under ``src`` in a deterministic order."""
    entries: list[tuple[Path, bool]] = []
    for dirpath, dirnames, filenames in os.walk(src):
        base = Path(dirpath)
        for name in sorted(dirnames):
            path = base / name
            if path.is_symlink():
                entries.append((path, True))
                dirnames.remove(name)
        for name in sorted(filenames):
            path = base / name
            entries.append((path, path.is_symlink()))
    return entries


def _write_symlink(archive: zipfile.ZipFile, path: Path, arcname: Path) -> None:
    info = zipfile.ZipInfo(str(arcname))
    info.create_system = 3  # Unix, so extraction preserves the permissions.
    info.external_attr = _SYMLINK_MODE << 16
    info.compress_type = zipfile.ZIP_STORED
    archive.writestr(info, os.readlink(path))


def build_archive(source_dir: str | Path, dest_file: str | Path) -> Path:
    """Create a ZIP of ``source_dir`` rooted at its parent directory."""
    src = Path(source_dir)
    if not src.is_dir():
        raise SystemExit(f"source directory does not exist: {src}")
    dest = Path(dest_file)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, is_link in _entries(src):
            arcname = path.relative_to(src.parent)
            if is_link:
                _write_symlink(archive, path, arcname)
            elif path.is_file():
                archive.write(path, arcname)
    return dest


if __name__ == "__main__":
    source_dir = sys.argv[1]
    dest_file = sys.argv[2]
    created = build_archive(source_dir, dest_file)
    print(f"archived {source_dir} -> {created}")
