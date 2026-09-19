"""Production entry point for the spotm3u Flask application.

``main()`` is the console-script and PyInstaller target. It starts the same
Flask app served by the development entry point but without the debug reloader,
binds to the loopback interface, picks a free local port, and opens the UI in
the default browser once the server answers. A packaged Windows executable is
built windowed (no console), so startup failures are reported through a native
message box rather than a traceback on a standard stream that does not exist.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import webbrowser

from .app import create_app
from .log import PACKAGE_LOGGER
from .runtime import bundle_roots, has_console, is_frozen

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001
BROWSER_ENV = "SPOTM3U_NO_BROWSER"
MIN_PORT = 1
MAX_PORT = 65535
READINESS_TIMEOUT = 30.0
READINESS_INTERVAL = 0.1

_LOGGER = logging.getLogger(PACKAGE_LOGGER)


def configure_config_path_for_bundle() -> None:
    """Point config discovery at a ``config.toml`` bundled with the app.

    In development the working directory is the checkout, so ``config.toml``
    is found there. A packaged executable runs from an arbitrary directory, so
    honor an explicit ``SPOTM3U_CONFIG`` first and otherwise look for a
    ``config.toml`` in the bundle, preferring one next to the executable over
    collected data locations. No-op in development.
    """
    if not is_frozen():
        return
    if os.environ.get("SPOTM3U_CONFIG"):
        return
    for candidate in reversed(bundle_roots()):
        bundled = candidate / "config.toml"
        if bundled.is_file():
            os.environ["SPOTM3U_CONFIG"] = str(bundled)
            return


def port_is_free(host: str, port: int) -> bool:
    """True when a loopback socket can bind ``host:port`` right now."""
    if not MIN_PORT <= port <= MAX_PORT:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def select_port(preferred: int, host: str = DEFAULT_HOST) -> int:
    """Return a free loopback port, preferring the configured one.

    The configured port is a convention rather than a guarantee: another
    program may already be listening there, so fall back to whatever free port
    the operating system assigns. The returned port is the only port the
    server is started on and the only one the browser is sent to.
    """
    if port_is_free(host, preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def wait_for_server(host: str, port: int, *, timeout: float = READINESS_TIMEOUT) -> bool:
    """Wait until ``host:port`` accepts connections, giving up after ``timeout``.

    Connecting proves the server socket is listening, which is a far better
    readiness signal than sleeping for an arbitrary fixed time before opening
    the browser.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(READINESS_INTERVAL)


def browser_enabled(explicit: bool | None = None) -> bool:
    """Whether startup should open the UI in the default browser.

    An explicit choice always wins; otherwise ``SPOTM3U_NO_BROWSER`` disables
    the automatic tab for development, tests, and headless environments.
    """
    if explicit is not None:
        return explicit
    return not os.environ.get(BROWSER_ENV)


def start_browser_opener(
    url: str, *, host: str, port: int, timeout: float = READINESS_TIMEOUT
) -> threading.Thread:
    """Open ``url`` exactly once, as soon as the server is ready.

    The wait runs on a daemon thread because the server owns the main thread.
    Starting this helper once per launch, together with the disabled debug
    reloader, is what keeps the browser from opening twice.
    """

    def _open_when_ready() -> None:
        if not wait_for_server(host, port, timeout=timeout):
            _LOGGER.warning("server was not ready; not opening the browser")
            return
        try:
            webbrowser.open(url)
        except (OSError, webbrowser.Error):
            _LOGGER.warning("could not open the default browser for %s", url)

    thread = threading.Thread(target=_open_when_ready, name="spotm3u-browser", daemon=True)
    thread.start()
    return thread


def show_message_box(message: str, *, title: str = "spotm3u") -> bool:
    """Show a native error dialog and report whether it was available.

    Used for a packaged, windowed application where printing a traceback would
    reach nobody. Platforms without the dialog return ``False`` so the caller
    can fall back to its standard streams.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        # MB_ICONERROR: the process is about to exit, so the dialog only
        # informs the user instead of offering a choice.
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except (AttributeError, OSError):  # pragma: no cover - environment dependent
        return False
    return True


def report_startup_error(message: str, *, exception: bool = False) -> None:
    """Report a fatal startup failure where the user can actually see it.

    Logging serves the development workflow; a windowed Windows build has no
    standard streams, so the same message is shown in a native dialog instead
    of disappearing. The message stays free of technical detail because normal
    users are the audience for the dialog.
    """
    if exception:
        _LOGGER.exception(message)
    else:
        _LOGGER.error(message)
    if has_console():
        return
    show_message_box(message)


def serve(*, open_browser: bool | None = None) -> None:
    """Start the web server on a free loopback port and serve until stopped."""
    configure_config_path_for_bundle()
    app = create_app()
    host = DEFAULT_HOST
    preferred_port = int(app.config.get("PORT", DEFAULT_PORT))
    port = select_port(preferred_port, host)
    if port != preferred_port:
        app.logger.info("port %d is in use; listening on port %d instead", preferred_port, port)
    if browser_enabled(open_browser):
        start_browser_opener(f"http://{host}:{port}/", host=host, port=port)
    app.logger.info("spotm3u listening on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


def main(*, open_browser: bool | None = None) -> None:
    """Run the application server until interrupted.

    A startup failure never fails silently: it is logged, shown in a native
    dialog when no console exists, and exits non-zero without leaving a
    half-started server process behind.
    """
    try:
        serve(open_browser=open_browser)
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        return
    except SystemExit as error:
        if not error.code:
            raise
        report_startup_error(
            "spotm3u could not start. Close other programs using its port and try again."
        )
        raise SystemExit(1) from error
    except Exception as error:
        report_startup_error(f"spotm3u could not start: {error}", exception=True)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
