"""Native desktop application shell.

``run_desktop()`` starts the existing Flask application on a free loopback port
and presents it inside a native ``SpotM3U`` WebView window (pywebview) instead
of an external browser. Flask keeps serving the UI and handling application
logic; this module only supplies the window around it, waits for the server
before loading the page, and shuts the server down when the window closes.

The window is frameless. Windows and Linux draw their own title bar (see
``templates/_titlebar.html`` and the title bar rules in ``static/style.css``);
macOS instead re-enables the native traffic lights on the frameless NSWindow
(see ``desktop_macos.py``) and keeps only a transparent drag strip in the page.
:class:`WindowControls` is exposed to the page as ``pywebview.api`` so the HTML
custom title-bar buttons can drive the native window on Windows/Linux, the ZIP
to import can be picked in the OS file dialog, and the M3U download can be
saved through the OS save panel. Only that API is exposed to the page, and the
localhost URL stays hidden from normal users. The window also gets a persistent
WebView storage location, so the choices the pages keep (the theme, a dismissed
update notice) survive a restart as they would in any desktop application.

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
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import requests
from flask import Flask
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
from .m3u import check_playlist
from .normalization import sanitize_filename_component
from .runtime import bundle_roots, is_frozen

_LOGGER = logging.getLogger(PACKAGE_LOGGER)

WINDOW_TITLE = "SpotM3U"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800
WINDOW_MIN_SIZE = (800, 560)
NO_WEBVIEW_ENV = "SPOTM3U_NO_WEBVIEW"
WEBVIEW_STORAGE_ENV = "SPOTM3U_WEBVIEW_STORAGE"
_ICON_RELATIVE = Path("assets") / "icon.png"
# How long close() waits for the native window to actually go away before it
# gives up and lets pywebview resolve the JS API call. See WindowControls.close.
_CLOSE_TIMEOUT_SECONDS = 5.0


def _fullscreen_maximize() -> bool:
    """Whether the window's maximize control should toggle native full screen.

    macOS runs full screen as a new Space, and that is what the green window
    control does in every other Mac app. pywebview's ``maximize`` only resizes
    the window to the screen size, so on macOS the control uses
    ``toggle_fullscreen`` instead. Windows and Linux keep maximize/restore.
    """
    return sys.platform == "darwin"


def _downloads_root() -> Path:
    """The folder generated playlists land in.

    ``$SPOTM3U_DOWNLOADS`` (also honoured by the desktop launcher) wins;
    otherwise the platform's Downloads folder. Created on demand so tests can
    point it at a temporary directory.
    """
    override = os.environ.get("SPOTM3U_DOWNLOADS")
    if override:
        candidate = Path(override)
        return (candidate if candidate.is_absolute() else Path.home() / override).resolve()
    return (Path.home() / "Downloads").resolve()


def _playlist_filename(job) -> str:
    """The filename a playlist is offered under, derived from its name."""
    base = sanitize_filename_component(job.playlist_name) or job.playlist_id
    return f"{base}.m3u"


def _file_dialog_kinds() -> dict[str, int]:
    """pywebview's file-dialog kinds, imported only when a dialog is opened.

    pywebview is imported lazily throughout this module so importing it never
    requires a desktop GUI stack (see the module docstring).
    """
    from webview import FileDialog

    return {"open": int(FileDialog.OPEN), "save": int(FileDialog.SAVE)}


class WindowControls:
    """JavaScript bridge backing the custom HTML title bar.

    pywebview exposes every public method of this object on
    ``window.pywebview.api``, so the page can call ``minimize``,
    ``toggle_maximize`` and ``close`` to drive the frameless native window.
    The :class:`webview.Window` reference is attached by :func:`show_window`
    after ``create_window`` returns; the bridge must exist before the window,
    but it can only control the window once the window exists.
    """

    def __init__(self, app: Flask | None = None) -> None:
        self.app = app
        self.window = None
        self._maximized = False
        # The archive picked in the native open dialog, held here until the
        # import route collects it (see choose_zip).
        self._pending_import: Path | None = None
        self._pending_lock = threading.Lock()

    def attach(self, window: object | None) -> None:
        """Bind the native window and mirror its maximize/restore state to the page.

        pywebview raises ``maximized`` and ``restored`` whenever the window's
        state changes, including OS-driven changes such as window snapping or
        a native maximize gesture, not only our own button. Subscribing here
        keeps the HTML title bar icon correct no matter who changed the state.
        """
        self.window = window
        if window is None:
            return
        window.events.maximized += self._on_maximized
        window.events.restored += self._on_restored

    def _on_maximized(self, *args: object) -> None:
        self._apply_state(True)

    def _on_restored(self, *args: object) -> None:
        self._apply_state(False)

    def _apply_state(self, maximized: bool) -> None:
        """Record the state and push it to the title bar's JS hook."""
        self._maximized = maximized
        if self.window is None:
            return
        try:
            self.window.evaluate_js(
                "window.spotm3uTitlebar && "
                f"window.spotm3uTitlebar.setMaximized({str(maximized).lower()})"
            )
        except Exception:  # pragma: no cover - defensive: the page may not be ready yet
            _LOGGER.debug("could not sync the title bar maximize state", exc_info=True)

    def minimize(self) -> None:
        """Minimize the native window."""
        if self.window is not None:
            self.window.minimize()

    def toggle_maximize(self) -> bool:
        """Maximize, full-screen or restore the window, returning the new state.

        The optimistic state is set before calling the native method so a
        synchronous ``maximized``/``restored`` event agrees with the return
        value instead of being flipped afterwards. macOS toggles native full
        screen; other platforms maximize/restore within the current desktop.
        """
        if self.window is None:
            return self._maximized
        self._maximized = not self._maximized
        if _fullscreen_maximize():
            self.window.toggle_fullscreen()
        elif self._maximized:
            self.window.maximize()
        else:
            self.window.restore()
        return self._maximized

    def is_maximized(self) -> bool:
        """Whether the window is maximized or full screen, for the page's icon."""
        return self._maximized

    def close(self) -> None:
        """Close the native window, which shuts the server down through the caller.

        The call blocks until the window has actually closed. pywebview resolves
        this JS API call by evaluating JavaScript in the window right after the
        method returns, and on macOS that evaluation runs against a webview that
        is already being torn down and blocks forever; the bridge thread is not
        a daemon, so the whole process then hangs with a closed window. Waiting
        for ``closed`` means the follow-up evaluation finds no window and is a
        harmless no-op.
        """
        if self.window is None:
            return
        window = self.window
        window.destroy()
        window.events.closed.wait(timeout=_CLOSE_TIMEOUT_SECONDS)

    def choose_zip(self) -> dict[str, object]:
        """Open the OS file picker for the Exportify ZIP and remember the choice.

        The desktop shell imports a file the same way any desktop application
        does, rather than through the browser's file control. The chosen path
        is kept on this side and later collected by the import route (see
        :meth:`take_pending_import`), so a path never travels through the page
        and the server can only ever read a file the user picked here.

        Returns the file name for the page to show, or ``cancelled`` when the
        dialog was dismissed.
        """
        if self.window is None:
            return {"error": "The native window is not available."}
        kinds = _file_dialog_kinds()
        try:
            picked = self.window.create_file_dialog(
                kinds["open"],
                allow_multiple=False,
                file_types=("Exportify export (*.zip)",),
            )
        except Exception:  # pragma: no cover - the native dialog is not testable here
            _LOGGER.warning("the file picker could not be opened", exc_info=True)
            return {"error": "The file picker could not be opened."}
        # pywebview returns a sequence for an open dialog, or None if cancelled.
        if not picked:
            return {"cancelled": True}
        path = Path(picked[0])
        if path.suffix.lower() != ".zip" or not path.is_file():
            return {"error": "Choose the ZIP file downloaded from Exportify."}
        with self._pending_lock:
            self._pending_import = path
        return {"name": path.name}

    def take_pending_import(self) -> Path | None:
        """Collect the file ``choose_zip`` picked, clearing it as it is read.

        Single-use on purpose: a second import has to go back through the
        native dialog, so nothing can re-import a path that was chosen once.
        """
        with self._pending_lock:
            path, self._pending_import = self._pending_import, None
        return path

    def save_m3u(self, job_id: str, playlist_id: str, confirm: bool = False) -> dict[str, object]:
        """Save a generated playlist where the user chooses.

        pywebview cannot deliver Flask's ``Content-Disposition: attachment``
        download the way a browser does, so the page hands the download to
        this bridge instead of following the link (see ``_save_m3u.html``).
        The bridge asks the OS for a save panel, offered with the playlist's
        name in the Downloads folder, and copies the server-owned output file
        there. The returned ``path`` lets the page show a toast with an "Open
        folder" action; a dismissed panel returns ``cancelled`` and nothing is
        written.

        A playlist whose referenced files were deleted by hand is reported
        back with ``confirm_required`` instead of opening the panel, so the
        page can confirm before writing a playlist that would skip those
        tracks. Passing ``confirm`` (the dialog's "Download anyway" action)
        saves it regardless.
        """
        job = self._find_job(job_id, playlist_id)
        if job is None or job.m3u_path is None:
            return {"error": "That playlist is not ready to download."}
        source = Path(job.m3u_path)
        if not source.is_file():
            return {"error": "That playlist is not ready to download."}

        check = check_playlist(source)
        if not check.complete and not confirm:
            return {
                "confirm_required": True,
                "missing": check.missing_count,
                "total": check.total,
                "names": list(check.missing_names()),
            }

        destination = self._ask_save_target(job)
        if destination is None:
            return {"cancelled": True}
        try:
            shutil.copyfile(source, destination)
        except OSError as error:
            _LOGGER.warning("could not save the M3U to %s: %s", destination, error)
            return {"error": "The playlist could not be saved."}
        return {"saved": True, "path": str(destination), "name": destination.name}

    def _ask_save_target(self, job) -> Path | None:
        """Where to write the playlist, from the OS save panel.

        Offered as the playlist's own name in the Downloads folder, which is
        where generated playlists live; the user can rename it or pick another
        location. ``None`` means the panel was dismissed.
        """
        if self.window is None:
            return None
        kinds = _file_dialog_kinds()
        root = _downloads_root()
        try:
            chosen = self.window.create_file_dialog(
                kinds["save"],
                directory=str(root),
                save_filename=_playlist_filename(job),
                file_types=("M3U playlist (*.m3u)",),
            )
        except Exception:  # pragma: no cover - the native dialog is not testable here
            _LOGGER.warning("the save panel could not be opened", exc_info=True)
            return None
        # A save dialog returns the chosen path as a single-entry sequence.
        if not chosen:
            return None
        target = Path(chosen[0] if isinstance(chosen, (list, tuple)) else chosen)
        # A name typed without an extension keeps the playlist's own suffix.
        return target if target.suffix else target.with_suffix(".m3u")

    def open_at(self, path: str) -> dict[str, str | bool]:
        """Reveal a saved playlist in the OS file manager.

        Powers the "Open folder" button on the download toast: reveals the file
        (Finder/Explorer) or the parent folder (xdg-open) so the user does not
        have to go hunting for the freshly saved M3U.
        """
        if self.window is None:
            return {"error": "The native window is not available."}
        target = Path(path)
        if not target.is_file():
            return {"error": "That file could not be found."}
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(target)])
            elif sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target.parent)])
        except OSError as error:
            _LOGGER.warning("could not reveal %s in the file manager: %s", target, error)
            return {"error": "The file manager could not be opened."}
        return {"opened": True}

    def open_url(self, url: str) -> dict[str, str | bool]:
        """Open a web page in the user's default browser.

        Powers the toast's "Release notes" action. The WebView has no browser
        of its own — ``window.open`` from the page does nothing there — so the
        page hands the URL to this bridge and the OS opens it. Only http(s)
        pages are accepted, so the page can never ask the shell to run
        anything else.
        """
        target = str(url or "").strip()
        if not target.startswith(("http://", "https://")):
            return {"error": "That link cannot be opened."}
        try:
            opened = webbrowser.open(target, new=2)
        except (webbrowser.Error, OSError) as error:
            _LOGGER.warning("could not open %s in a browser: %s", target, error)
            return {"error": "The page could not be opened in a browser."}
        if not opened:
            return {"error": "The page could not be opened in a browser."}
        return {"opened": True}

    def _unique_download_target(self, name: str) -> Path:
        """A collision-free path under ``_downloads_root()`` for ``name``.

        Appends `` (2)``, `` (3)`` … until the name is free so repeated saves
        never silently overwrite an older copy.
        """
        root = _downloads_root()
        root.mkdir(parents=True, exist_ok=True)
        candidate = root / name
        stem = candidate.stem
        suffix = candidate.suffix
        counter = 2
        while candidate.exists():
            candidate = root / f"{stem} ({counter}){suffix}"
            counter += 1
        return candidate

    def download_update(self, asset_url: str, filename: str) -> dict[str, str | bool | Path]:
        """Download a release asset (e.g. the new installer) into Downloads.

        The desktop shell cannot rely on a browser download, so the update
        banner hands the asset URL to this bridge instead. The file is
        streamed to ``_downloads_root()`` and returned with its path so the
        page can show a toast with an "Open folder" action; SpotM3U never
        launches or extracts the downloaded update itself.
        """
        name = Path(filename).name
        if not asset_url.startswith("https://") or not name:
            return {"error": "The update download is not available."}
        target = self._unique_download_target(name)
        try:
            _LOGGER.info("downloading update %s", name)
            with requests.get(asset_url, stream=True, timeout=120) as response:
                response.raise_for_status()
                with target.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            handle.write(chunk)
        except requests.RequestException as error:
            _LOGGER.warning("could not download update %s: %s", name, error)
            return {
                "error": "The update could not be downloaded. Check your connection and try again."
            }
        except OSError as error:
            _LOGGER.warning("could not save update %s: %s", name, error)
            return {"error": "The update could not be saved."}
        return {"saved": True, "path": str(target), "name": target.name}

    def _find_job(self, job_id: str, playlist_id: str):
        if self.app is None:
            return None
        manager = self.app.config.get("JOB_MANAGER")
        if manager is None:
            return None
        return manager.get(job_id, playlist_id)


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


def webview_storage_path() -> Path:
    """Where the WebView keeps cookies and local storage between launches.

    A browser tab keeps its storage for as long as the tab lives and never has
    to say where it is; the desktop shell owns that decision. pywebview's
    default is private mode, which *deletes* the WebView's data store on every
    launch (``clear_user_data`` on Windows, ``removeDataOfTypes_`` on macOS), so
    the theme chosen in the sidebar and the dismissed update notice would come
    back reset on every single run - behaviour no desktop application has.

    The store lives in the platform's application-data directory:
    ``%LOCALAPPDATA%\\SpotM3U`` on Windows,
    ``~/Library/Application Support/SpotM3U`` on macOS, and ``$XDG_DATA_HOME``
    (or ``~/.local/share``) on Linux. ``SPOTM3U_WEBVIEW_STORAGE`` overrides it.
    """
    override = os.environ.get(WEBVIEW_STORAGE_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "SpotM3U" / "webview"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SpotM3U" / "webview"
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "spotm3u" / "webview"


def webview_persistence_kwargs() -> dict[str, object]:
    """``webview.start`` arguments that let the shell remember its own settings.

    pywebview creates the storage directory itself and refuses to start when it
    cannot, which would cost the user the whole window over a preference file.
    A storage location that cannot be prepared is therefore reported and
    skipped: the window opens, in private mode, exactly as it did before.
    """
    path = webview_storage_path()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        _LOGGER.warning("could not prepare the WebView storage directory at %s", path)
        return {}
    return {"private_mode": False, "storage_path": str(path)}


def show_window(url: str, app: Flask | None = None) -> None:
    """Show ``url`` in the native SpotM3U window and block until it closes.

    pywebview is imported lazily so tests and the headless server path never
    touch the desktop GUI stack. The window is frameless; on macOS its native
    traffic lights are restored so the chrome stays AppKit-native, while
    Windows and Linux keep the page-drawn title bar.
    :class:`WindowControls` is exposed as ``pywebview.api`` so the HTML
    title-bar buttons can drive the native window, the ZIP to import can be
    picked in the OS file dialog, and the M3U download can be saved through the
    OS save panel. Only that API is exposed to the page, and the localhost URL
    stays hidden from normal users. The controls are also registered in the
    Flask config so the import route can collect the picked file (see
    ``WindowControls.take_pending_import``). The window is given a persistent
    WebView storage location (see :func:`webview_storage_path`) so the small
    choices the pages keep - the theme, a dismissed update notice - survive a
    restart the way a desktop application's settings do.
    """
    import webview

    # The default (ALLOW_DOWNLOADS=False) makes WebKit silently cancel any
    # navigation to the octet-stream M3U URL, so a download that slips past the
    # page's JS bridge would do nothing. Allow downloads so it falls back to a
    # native save dialog instead.
    settings = getattr(webview, "settings", None)
    if settings is not None:
        settings["ALLOW_DOWNLOADS"] = True

    controls = WindowControls(app=app)
    if app is not None:
        # Lets the page's import route collect the file the native picker
        # returned; the path itself never leaves this process.
        app.config["WINDOW_CONTROLS"] = controls
    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=WINDOW_MIN_SIZE,
        frameless=True,
        easy_drag=False,
        js_api=controls,
    )
    controls.attach(window)
    # On macOS the native NSWindow only exists once pywebview's GUI loop has
    # started, so restore the traffic lights from a start() callback that waits
    # for the window to appear; every other platform keeps the page-drawn bar.
    from .desktop_macos import configure_native_chrome

    start_kwargs: dict[str, object] = {
        **webview_start_kwargs(),
        **webview_persistence_kwargs(),
    }
    if sys.platform == "darwin":
        start_kwargs["func"] = lambda: configure_native_chrome(window)
    webview.start(**start_kwargs)


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
            show_window(url, app=app)
        else:
            server_thread.join()
    finally:
        server.shutdown()
        server_thread.join(timeout=5)
