"""Native desktop application shell.

``run_desktop()`` starts the existing Flask application on a free loopback port
and presents it inside a native ``SpotM3U`` WebView window (pywebview) instead
of an external browser. Flask keeps serving the UI and handling application
logic; this module only supplies the window around it, waits for the server
before loading the page, and shuts the server down when the window closes.

The WebView is a production shell only. Developers use ``uv run dev``, which
starts the same Flask app in a normal browser, and never need the native window
or a packaged executable. ``SPOTM3U_NO_WEBVIEW=1`` (equivalently
``open_window=False``) disables the native window and simply serves; that is how
the release smoke test drives a packaged build over HTTP on a headless runner.

pywebview is imported lazily inside :func:`show_window`, so importing this
module never requires a desktop GUI stack.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from werkzeug.serving import make_server

from .app import create_app
from .launcher import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    READINESS_TIMEOUT,
    configure_config_path_for_bundle,
    select_port,
    wait_for_server,
)
from .log import PACKAGE_LOGGER
from .runtime import bundle_roots, is_frozen

_LOGGER = logging.getLogger(PACKAGE_LOGGER)

WINDOW_TITLE = "SpotM3U"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800
WINDOW_MIN_SIZE = (800, 560)
NO_WEBVIEW_ENV = "SPOTM3U_NO_WEBVIEW"
_ICON_RELATIVE = Path("assets") / "icon.png"


def webview_icon_path() -> Path | None:
    """The canonical SpotM3U icon for the WebView window, when available.

    The same ``assets/icon.png`` is used in development and by the packaged
    build. Only the Linux GTK backend applies it as the window icon (see
    :func:`webview_start_kwargs`); Windows and macOS take their Dock/taskbar
    identity from the packaged .app/.exe instead.
    """
    if is_frozen():
        for candidate in bundle_roots():
            bundled = candidate / "icon.png"
            if bundled.is_file():
                return bundled
        return None
    root = Path(__file__).resolve().parents[2]
    icon = root / _ICON_RELATIVE
    return icon if icon.is_file() else None


def webview_url(host: str, port: int) -> str:
    """The loopback URL the WebView loads for the given server port."""
    return f"http://{host}:{port}/"


def webview_enabled(explicit: bool | None = None) -> bool:
    """Whether the native window should open on startup.

    An explicit choice always wins; otherwise ``SPOTM3U_NO_WEBVIEW`` disables
    the window for headless environments and the release smoke test.
    """
    if explicit is not None:
        return explicit
    return not os.environ.get(NO_WEBVIEW_ENV)


def start_server(app, host: str, port: int) -> tuple[object, threading.Thread]:
    """Serve ``app`` from a daemon thread until ``server.shutdown()`` is called."""
    server = make_server(host, port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, name="spotm3u-flask", daemon=True)
    thread.start()
    return server, thread


def webview_start_kwargs() -> dict[str, str]:
    """Keyword arguments for ``webview.start`` selecting the window icon.

    pywebview only applies ``icon`` on its GTK/QT backends. Its Windows
    (winforms/WebView2) backend passes the path straight to
    ``System.Drawing.Icon``, which accepts only ``.ico`` files; handing it the
    bundled PNG aborts window creation with an unhandled .NET exception that no
    Python handler can catch, so the windowed process dies before the launcher
    can report anything. Returning no icon on Windows and macOS falls back to
    the icon already embedded in the executable or ``.app`` bundle.
    """
    icon = webview_icon_path()
    if icon is None or not sys.platform.startswith("linux"):
        return {}
    return {"icon": str(icon)}


def show_window(url: str) -> None:
    """Show ``url`` in the native SpotM3U window and block until it closes.

    pywebview is imported lazily so tests and the headless server path never
    touch the desktop GUI stack. Only the windowing capability is used: no
    JavaScript bridge or extra API is exposed to the page, and the localhost
    URL stays hidden from normal users.
    """
    import webview

    webview.create_window(
        WINDOW_TITLE,
        url,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=WINDOW_MIN_SIZE,
    )
    webview.start(**webview_start_kwargs())


def run_desktop(*, open_window: bool | None = None) -> None:
    """Run the production desktop application until the window closes.

    Starts Flask on a free loopback port in a background thread, waits for the
    server to accept connections, then blocks inside the native window. When
    the window closes the server is shut down and the function returns. With
    the window disabled it serves until interrupted instead.
    """
    configure_config_path_for_bundle()
    app = create_app()
    host = DEFAULT_HOST
    preferred_port = int(app.config.get("PORT", DEFAULT_PORT))
    port = select_port(preferred_port, host)
    if port != preferred_port:
        app.logger.info("port %d is in use; listening on port %d instead", preferred_port, port)
    server, server_thread = start_server(app, host, port)
    url = webview_url(host, port)
    app.logger.info("SpotM3U listening on %s", url)
    try:
        if not wait_for_server(host, port, timeout=READINESS_TIMEOUT):
            raise RuntimeError("server did not become ready")
        if webview_enabled(open_window):
            show_window(url)
        else:
            server_thread.join()
    finally:
        server.shutdown()
        server_thread.join(timeout=5)
