# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the spotm3u standalone application.

Build a one-folder (onedir) bundle so the executable starts quickly and the
bundled FFmpeg directory can sit next to it. Everything the Flask app, yt-dlp,
and the bgutil PO token plugin need is collected here; generated output lands
in the git-ignored ``build/`` and ``dist/`` directories at the repository
root.

Usage (from the repository root):

    SPOTM3U_FFMPEG_DIR=<dir containing ffmpeg[.exe] and ffprobe[.exe]> \
    uv run --group build pyinstaller --noconfirm --clean packaging/spotm3u.spec

``SPOTM3U_FFMPEG_DIR`` is optional; when set, the directory's contents are
copied into ``dist/spotm3u/ffmpeg/`` so the packaged app provides its own
FFmpeg. When unset the build succeeds but FFmpeg must come from the system.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH) if "SPECPATH" in globals() else Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src"

# Let hook helpers locate the ``spotm3u`` package during spec evaluation.
sys.path.insert(0, str(SRC))

datas = []
binaries = []
hiddenimports = []

# spotm3u package data: templates and static assets live inside the package.
datas += collect_data_files("spotm3u")
hiddenimports += collect_submodules("spotm3u")

# yt-dlp loads its extractors and runtime data dynamically.
hiddenimports += collect_submodules("yt_dlp")
datas += collect_data_files("yt_dlp")

# bgutil PO token plugin installs into the yt_dlp_plugins namespace; the
# extractor modules there are loaded from source files at runtime.
hiddenimports += collect_submodules("yt_dlp_plugins")
datas += collect_data_files("yt_dlp_plugins", include_py_files=True)

# zhconv ships its simplified/traditional conversion dictionary.
datas += collect_data_files("zhconv")

# Bundled FFmpeg lives in an ``ffmpeg`` directory in the output bundle.
ffmpeg_source = os.environ.get("SPOTM3U_FFMPEG_DIR")
if ffmpeg_source:
    ffmpeg_dir = Path(ffmpeg_source).expanduser().resolve()
    if not ffmpeg_dir.is_dir():
        raise SystemExit(
            f"SPOTM3U_FFMPEG_DIR is not a directory: {ffmpeg_dir}"
        )
    datas.append((str(ffmpeg_dir), "ffmpeg"))

a = Analysis(
    [str(HERE / "run_app.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "pytest",
        "_pytest",
        "setuptools",
        "pip",
        "uv",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="spotm3u",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="spotm3u",
)