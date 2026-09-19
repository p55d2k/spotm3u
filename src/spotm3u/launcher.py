"""Production entry point for the spotm3u Flask application.

``main()`` is the console-script and PyInstaller target. It starts the same
Flask app served by the development entry point but without the debug reloader,
binds to the loopback interface, and adapts paths when the application runs
from a packaged bundle instead of a virtualenv.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .app import create_app

DEFAULT_HOST = "127.0.0.1"


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def bundle_root() -> Path:
    """Directory holding the packaged application and its sibling binaries.

    ``sys._MEIPASS`` is where PyInstaller extracts a one-file bundle; a
    one-folder bundle keeps files next to the executable. Falling back to the
    executable directory keeps the bundled ``ffmpeg`` lookup stable in both
    layouts.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(sys.executable).resolve().parent


def prepend_bundled_tools_to_path() -> None:
    """Make a packaged ``ffmpeg`` directory discoverable on ``PATH``.

    yt-dlp locates the ffmpeg/ffprobe it needs for audio extraction through the
    process ``PATH``. When a PyInstaller bundle ships an ``ffmpeg`` directory
    next to the executable (or under its extraction directory), prepend it so
    no system install is required. No-op in development.
    """
    if not is_frozen():
        return
    for directory in (bundle_root() / "ffmpeg",):
        if not directory.is_dir():
            continue
        existing = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
        if str(directory) in existing:
            return
        os.environ["PATH"] = os.pathsep.join([str(directory), *existing])
        return


def configure_config_path_for_bundle() -> None:
    """Point config discovery at a ``config.toml`` bundled with the app.

    In development the working directory is the checkout, so ``config.toml``
    is found there. A packaged executable runs from an arbitrary directory, so
    honor an explicit ``SPOTM3U_CONFIG`` first and otherwise fall back to a
    ``config.toml`` next to the executable. No-op in development.
    """
    if not is_frozen():
        return
    if os.environ.get("SPOTM3U_CONFIG"):
        return
    bundled = bundle_root() / "config.toml"
    if bundled.is_file():
        os.environ["SPOTM3U_CONFIG"] = str(bundled)


def main() -> None:
    """Run the application server until interrupted."""
    prepend_bundled_tools_to_path()
    configure_config_path_for_bundle()
    app = create_app()
    host = DEFAULT_HOST
    port = int(app.config.get("PORT", 5001))
    app.logger.info("spotm3u listening on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
