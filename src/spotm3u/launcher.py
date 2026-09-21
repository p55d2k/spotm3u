"""Production entry point for the SpotM3U desktop application.

``main()`` is the console-script and PyInstaller target behind ``uv run app``
and the packaged executable. It delegates to :mod:`spotm3u.desktop`, which
starts the same Flask app served by the development workflow, picks a free
loopback port, waits for the server, and presents the UI inside a native
WebView window instead of an external browser. Closing the window shuts Flask
down and the process exits.

Developers never need the native window: ``uv run dev`` (``spotm3u.dev``)
serves the same app in a normal browser with the debug reloader.

The helpers here -- free-port selection, server readiness polling, bundle
config discovery, and native error reporting -- are shared with the desktop
shell and the packaging helpers. A packaged Windows executable is built
windowed (no console), so startup failures are reported through a native
message box rather than a traceback on a standard stream that does not exist.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

from .log import PACKAGE_LOGGER
from .runtime import bundle_roots, has_console, is_frozen

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001
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
    server is started on and the only one the WebView is sent to.
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
    the browser (or, in production, the WebView).
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


def show_message_box(message: str, *, title: str = "SpotM3U") -> bool:
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


def error_log_path() -> Path:
    """The default startup-error log location for a packaged launch.

    ``spotm3u-error.log`` sits beside the executable so the folder the user is
    working from shows the file after a failed launch.
    """
    return Path(sys.executable).resolve().parent / "spotm3u-error.log"


def write_error_log(message: str, *, exception: bool = False, path: Path | None = None) -> Path:
    """Append a timestamped startup failure to a file and return its path.

    The ``path`` (default :func:`error_log_path`) is tried first; when it
    cannot be opened the per-user temporary directory is used instead so the
    failure is never lost to unwritable locations.
    """
    target = path or error_log_path()
    lines = [f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}"]
    if exception:
        lines.append(traceback.format_exc().rstrip())
    if target.is_dir():
        target = target / "spotm3u-error.log"
    try:
        with target.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        target = Path(tempfile.gettempdir()) / "spotm3u-error.log"
        with target.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return target


def report_startup_error(message: str, *, exception: bool = False) -> None:
    """Report a fatal startup failure where the user can actually see it.

    Logging serves the development workflow; a windowed Windows build has no
    standard streams, so the same message is shown in a native dialog and, as a
    safety net, appended to a startup-error log file. A packaged build always
    writes that file -- even when a console or redirected stream is present --
    so a failed launch leaves a diagnostic artifact behind no matter how the
    process was started. The dialog message stays free of technical detail
    because normal users are the audience; the log file carries the diagnostic
    traceback.
    """
    if exception:
        _LOGGER.exception(message)
    else:
        _LOGGER.error(message)
    if is_frozen():
        write_error_log(message, exception=exception)
    if has_console():
        return
    show_message_box(message)


def main(*, open_window: bool | None = None) -> None:
    """Run the production desktop application until its window closes.

    A startup failure never fails silently: it is logged, shown in a native
    dialog when no console exists, and exits non-zero without leaving a
    half-started server process behind. The desktop import lives inside the
    guard because importing the Flask app builds it at module load, so a bad
    configuration file raises here before any window exists.
    """
    try:
        from .desktop import run_desktop

        run_desktop(open_window=open_window)
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        return
    except SystemExit as error:
        if not error.code:
            raise
        report_startup_error("SpotM3U could not start. Please try launching the application again.")
        raise SystemExit(1) from error
    except Exception as error:
        report_startup_error(f"SpotM3U could not start: {error}", exception=True)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
