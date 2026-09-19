"""Zip a PyInstaller build output directory for release upload.

Archives `spotm3u/<exe> + _internal/...` so the unpacked ZIP is the standalone
bundle root, matching the layout users are told to place next to executable.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def build_archive(source_dir: str | Path, dest_file: str | Path) -> Path:
    """Create a ZIP of ``source_dir`` rooted at its parent directory."""
    src = Path(source_dir)
    if not src.is_dir():
        raise SystemExit(f"source directory does not exist: {src}")
    dest = Path(dest_file)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in src.rglob("*") if p.is_file()):
            archive.write(path, path.relative_to(src.parent))
    return dest


if __name__ == "__main__":
    source_dir = sys.argv[1]
    dest_file = sys.argv[2]
    created = build_archive(source_dir, dest_file)
    print(f"archived {source_dir} -> {created}")
