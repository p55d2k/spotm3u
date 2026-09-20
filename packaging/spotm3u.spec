# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the spotm3u standalone application.

Build a one-folder (onedir) bundle so the executable starts quickly and the
bundled FFmpeg directory can sit next to it. Everything the Flask app, yt-dlp,
and the bgutil PO token plugin need is collected here; generated output lands
in the git-ignored ``build/`` and ``dist/`` directories at the repository
root.

On macOS the COLLECT output is additionally wrapped into a normal
``dist/spotm3u.app`` application bundle so users can launch it through
Finder/Gatekeeper's one-time Right-click -> Open flow. The same onedir layout
is kept on every platform; only macOS gains the ``.app`` wrapper.

The Windows executable is built windowed (no console) so double-clicking
``spotm3u.exe`` never flashes a terminal; the launcher opens the native spotm3u
WebView window and reports startup failures through a native message box. Other
platforms keep their console so developers and release verification can read
the logs.

Usage (from the repository root):

    SPOTM3U_FFMPEG_DIR=<dir containing ffmpeg[.exe] and ffprobe[.exe]> \
    uv run --group build pyinstaller --noconfirm --clean packaging/spotm3u.spec

or, equivalently, the shortcut provided in ``pyproject.toml``:

    uv run build

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
ICON_PNG = ROOT / "assets" / "icon.png"
ICON_ICO = ROOT / "assets" / "generated" / "icon.ico"
ICON_ICNS = ROOT / "assets" / "generated" / "icon.icns"


def _require_icon(path: Path, purpose: str) -> Path:
    """Fail the build with a clear hint instead of a PyInstaller traceback."""
    if not path.is_file():
        raise SystemExit(
            f"missing {purpose} icon at {path}; run `uv run build` or "
            "`python packaging/generate_icons.py` first"
        )
    return path


# Let hook helpers locate the ``spotm3u`` package during spec evaluation.
sys.path.insert(0, str(SRC))

datas = []
binaries = []
hiddenimports = []

# The canonical PNG is bundled so the desktop window can load it at runtime
# (pywebview's GTK backend applies it as the window icon on Linux). On Windows
# and macOS the shell icon comes from the embedded .ico/.icns instead.
if ICON_PNG.is_file():
    datas.append((str(ICON_PNG), "."))

# spotm3u package data: templates and static assets live inside the package.
datas += collect_data_files("spotm3u")
hiddenimports += collect_submodules("spotm3u")

# pywebview (the desktop WebView shell) loads its platform backend dynamically;
# scouting every backend keeps the native window working in a frozen bundle.
# PyInstaller's own hooks supply the platform GUI stacks they depend on
# (pyobjc on macOS, pythonnet on Windows, gi on Linux).
hiddenimports += collect_submodules("webview")

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
    # The window and taskbar icon on Windows; ignored on other platforms,
    # where the macOS bundle (.icns) or the collected icon.png supply it.
    icon=str(_require_icon(ICON_ICO, "Windows .ico")) if sys.platform == "win32" else None,
    # Windowed on Windows only: a double-clicked ``spotm3u.exe`` must not open a
    # console window. Other platforms keep the console for logs, and a
    # windowed build has no standard streams to log to.
    console=sys.platform != "win32",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="spotm3u",
)

# macOS ships a real application bundle. BUNDLE relocates the collected files
# into Contents/Frameworks (with data cross-linked through Contents/Resources)
# and puts the executable in Contents/MacOS, which is what lets the frozen
# bootloader find ``sys._MEIPASS``. It is a no-op on other platforms, but is
# only declared on macOS so non-macOS builds keep their existing layout.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="spotm3u.app",
        icon=str(_require_icon(ICON_ICNS, "macOS .icns")),
        bundle_identifier="com.github.p55d2k.spotm3u",
        version=os.environ.get("SPOTM3U_APP_VERSION", "0.0.0"),
    )