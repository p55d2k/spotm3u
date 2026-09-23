"""Tests for the native desktop application shell."""

import logging
import socket
import sys
import urllib.request
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from flask import Flask

from spotm3u import desktop, launcher

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _minimal_app() -> Flask:
    app = Flask(__name__)
    app.config["PORT"] = launcher.DEFAULT_PORT
    app.logger = logging.getLogger("test-desktop")

    @app.get("/")
    def index():
        return "ok"

    return app


def _fetch(url: str) -> str:
    with _OPENER.open(url, timeout=10) as resp:
        return resp.read().decode("utf-8")


def _ephemeral_port() -> int:
    with closing(socket.socket()) as probe:
        probe.bind((launcher.DEFAULT_HOST, 0))
        return int(probe.getsockname()[1])


def test_webview_url_uses_localhost_and_selected_port() -> None:
    assert desktop.webview_url("127.0.0.1", 5123) == "http://127.0.0.1:5123/"


def test_webview_icon_path_points_at_the_canonical_icon_in_development(monkeypatch) -> None:
    monkeypatch.setattr(desktop, "is_frozen", lambda: False)

    icon = desktop.webview_icon_path()

    assert icon is not None
    assert icon.is_file()
    assert icon.name == "icon.png"
    assert "assets" in icon.parts


def test_webview_icon_path_checks_bundled_icon_when_frozen(monkeypatch, tmp_path) -> None:
    bundled = tmp_path / "_internal"
    bundled.mkdir()
    (bundled / "icon.png").write_bytes(b"png")
    monkeypatch.setattr(desktop, "is_frozen", lambda: True)
    monkeypatch.setattr(desktop, "bundle_roots", lambda: (bundled,))

    assert desktop.webview_icon_path() == bundled / "icon.png"


def test_webview_icon_path_is_none_when_no_bundled_icon(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(desktop, "is_frozen", lambda: True)
    monkeypatch.setattr(desktop, "bundle_roots", lambda: (tmp_path / "missing",))

    assert desktop.webview_icon_path() is None


@pytest.mark.parametrize(
    ("platform_name", "icon", "expected"),
    [
        # pywebview's winforms backend hands the path to System.Drawing.Icon,
        # which rejects the bundled PNG and kills the windowed process with an
        # unhandled .NET exception. Windows falls back to the executable's icon.
        pytest.param("win32", Path("icon.png"), {}, id="windows-uses-its-embedded-icon"),
        pytest.param(
            "linux",
            Path("icon.png"),
            {"icon": str(Path("icon.png"))},
            id="linux-passes-the-png-icon",
        ),
        pytest.param("linux", None, {}, id="no-icon-to-pass"),
    ],
)
def test_webview_start_kwargs(monkeypatch, platform_name, icon, expected) -> None:
    monkeypatch.setattr(desktop, "webview_icon_path", lambda: icon)
    monkeypatch.setattr(desktop.sys, "platform", platform_name)

    assert desktop.webview_start_kwargs() == expected


def test_webview_enabled_defaults_to_true(monkeypatch) -> None:
    monkeypatch.delenv(desktop.NO_WEBVIEW_ENV, raising=False)

    assert desktop.webview_enabled() is True


def test_webview_env_var_disables_the_native_window(monkeypatch) -> None:
    monkeypatch.setenv(desktop.NO_WEBVIEW_ENV, "1")

    assert desktop.webview_enabled() is False
    assert desktop.webview_enabled(True) is True


class _FakeEvent:
    """Minimal stand-in for a pywebview Event supporting ``+=``, firing and waiting."""

    def __init__(self) -> None:
        self.handlers: list[Any] = []
        self.waited = 0

    def __iadd__(self, handler: Any):
        self.handlers.append(handler)
        return self

    def fire(self, *args: Any) -> None:
        for handler in self.handlers:
            handler(*args)

    def wait(self, timeout: float | None = None) -> bool:
        self.waited += 1
        return True


class _FakeWindow:
    """Records native calls and evaluate_js scripts for assertions."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.scripts: list[str] = []
        self.events = SimpleNamespace(
            maximized=_FakeEvent(), restored=_FakeEvent(), closed=_FakeEvent()
        )

    def minimize(self) -> None:
        self.calls.append("minimize")

    def maximize(self) -> None:
        self.calls.append("maximize")

    def restore(self) -> None:
        self.calls.append("restore")

    def toggle_fullscreen(self) -> None:
        self.calls.append("toggle_fullscreen")

    def destroy(self) -> None:
        self.calls.append("destroy")

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)


def test_window_controls_drive_the_native_window(monkeypatch) -> None:
    monkeypatch.setattr(desktop, "_fullscreen_maximize", lambda: False)
    controls = desktop.WindowControls()
    window = _FakeWindow()
    controls.attach(window)

    controls.minimize()
    assert controls.toggle_maximize() is True
    assert controls.is_maximized() is True
    assert controls.toggle_maximize() is False
    controls.close()

    assert window.calls == ["minimize", "maximize", "restore", "destroy"]
    # close() must wait for the window to finish closing so pywebview's follow-up
    # JS evaluation does not run against a torn-down webview and deadlock.
    assert window.events.closed.waited == 1


def test_window_controls_use_native_fullscreen_on_macos(monkeypatch) -> None:
    # pywebview's macOS maximize() only resizes; the green control should open a
    # full-screen Space instead, matching other Mac apps.
    monkeypatch.setattr(desktop, "_fullscreen_maximize", lambda: True)
    controls = desktop.WindowControls()
    window = _FakeWindow()
    controls.attach(window)

    assert controls.toggle_maximize() is True
    assert controls.toggle_maximize() is False

    assert window.calls == ["toggle_fullscreen", "toggle_fullscreen"]


def test_window_controls_follow_os_maximize_and_restore_events() -> None:
    controls = desktop.WindowControls()
    window = _FakeWindow()
    controls.attach(window)

    window.events.maximized.fire()

    assert controls.is_maximized() is True
    assert window.scripts[-1].endswith("setMaximized(true)")

    window.events.restored.fire()

    assert controls.is_maximized() is False
    assert window.scripts[-1].endswith("setMaximized(false)")

    # Restoring an already-restored window is idempotent and still syncs the page.
    window.events.restored.fire()
    assert controls.is_maximized() is False


def test_window_controls_attach_tolerates_a_missing_window() -> None:
    controls = desktop.WindowControls()

    controls.attach(None)

    assert controls.window is None


def test_window_controls_are_safe_before_a_window_exists() -> None:
    controls = desktop.WindowControls()

    controls.minimize()
    controls.close()

    assert controls.toggle_maximize() is False
    assert controls.is_maximized() is False


def _controls_client(tmp_path) -> tuple[desktop.WindowControls, _FakeWindow, Flask, object]:
    from spotm3u import jobs

    job = jobs.ProcessingJob(
        job_id="job",
        playlist_id="0",
        playlist_name="Hits",
        tracks=[],
        output_dir=tmp_path,
        resolver_factory=lambda: None,
        m3u_filename="playlist.m3u",
    )
    job._m3u_path = tmp_path / "playlist.m3u"
    manager = SimpleNamespace(get=lambda job_id, playlist_id: job if playlist_id == "0" else None)
    app = Flask(__name__)
    app.config["JOB_MANAGER"] = manager
    controls = desktop.WindowControls(app=app)
    window = _FakeWindow()
    controls.attach(window)
    return controls, window, app, job


def test_save_m3u_copies_the_playlist_into_downloads(tmp_path, monkeypatch) -> None:
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(desktop, "_downloads_root", lambda: downloads)
    controls, _window, _app, job = _controls_client(tmp_path)
    job.m3u_path.write_text("#EXTM3U\n", encoding="utf-8")

    result = controls.save_m3u("job", "0")

    assert result["saved"] is True
    assert Path(result["path"]) == downloads / "Hits.m3u"
    assert result["name"] == "Hits.m3u"
    assert (downloads / "Hits.m3u").read_text(encoding="utf-8") == "#EXTM3U\n"


def test_save_m3u_uses_a_unique_name_when_collisions_exist(tmp_path, monkeypatch) -> None:
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(desktop, "_downloads_root", lambda: downloads)
    controls, _window, _app, job = _controls_client(tmp_path)
    downloads.mkdir()
    (downloads / "Hits.m3u").write_text("older copy", encoding="utf-8")
    job.m3u_path.write_text("#EXTM3U\n", encoding="utf-8")

    result = controls.save_m3u("job", "0")

    assert result["saved"] is True
    assert result["name"] == "Hits (2).m3u"
    assert result["path"] == str(downloads / "Hits (2).m3u")


def test_save_m3u_asks_for_confirmation_when_files_are_missing(tmp_path, monkeypatch) -> None:
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(desktop, "_downloads_root", lambda: downloads)
    controls, _window, _app, job = _controls_client(tmp_path)
    job.m3u_path.write_text("#EXTM3U\nArtist - Song.mp3\n", encoding="utf-8")

    result = controls.save_m3u("job", "0")

    assert result["confirm_required"] is True
    assert result["missing"] == 1
    assert result["total"] == 1
    assert result["names"] == ["Artist - Song.mp3"]
    assert not downloads.exists()


def test_save_m3u_saves_confirmed_playlists_with_missing_files(tmp_path, monkeypatch) -> None:
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(desktop, "_downloads_root", lambda: downloads)
    controls, _window, _app, job = _controls_client(tmp_path)
    job.m3u_path.write_text("#EXTM3U\nArtist - Song.mp3\n", encoding="utf-8")

    result = controls.save_m3u("job", "0", True)

    assert result["saved"] is True
    assert (downloads / "Hits.m3u").is_file()


def test_save_m3u_needs_no_confirmation_when_every_file_is_present(tmp_path, monkeypatch) -> None:
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(desktop, "_downloads_root", lambda: downloads)
    controls, _window, _app, job = _controls_client(tmp_path)
    (tmp_path / "Artist - Song.mp3").write_bytes(b"audio")
    job.m3u_path.write_text("#EXTM3U\nArtist - Song.mp3\n", encoding="utf-8")

    result = controls.save_m3u("job", "0")

    assert result["saved"] is True
    assert "confirm_required" not in result


def test_save_m3u_reports_jobs_that_are_not_ready(tmp_path) -> None:
    controls, _window, _app, _job = _controls_client(tmp_path)

    assert controls.save_m3u("job", "0") == {"error": "That playlist is not ready to download."}
    assert controls.save_m3u("job", "99") == {"error": "That playlist is not ready to download."}


def test_save_m3u_without_an_app_reports_not_ready(tmp_path) -> None:
    _controls, _window, _app, job = _controls_client(tmp_path)
    job.m3u_path.write_text("#EXTM3U\n", encoding="utf-8")
    controls = desktop.WindowControls()

    assert "error" in controls.save_m3u("job", "0")


def test_download_update_streams_the_asset_into_downloads(tmp_path, monkeypatch) -> None:
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(desktop, "_downloads_root", lambda: downloads)
    chunks = iter([b"PK", b"\x03\x04payload"])

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size):
            assert chunk_size > 0
            return chunks

    downloaded = {}

    def fake_get(url, **kwargs):
        downloaded["url"] = url
        assert kwargs["stream"] is True
        return _FakeResponse()

    monkeypatch.setattr(desktop.requests, "get", fake_get)
    controls = desktop.WindowControls()

    result = controls.download_update(
        "https://example.com/SpotM3U-v2.0.0-macos-arm64.pkg", "SpotM3U-v2.0.0-macos-arm64.pkg"
    )

    assert downloaded == {"url": "https://example.com/SpotM3U-v2.0.0-macos-arm64.pkg"}
    assert result["saved"] is True
    assert result["name"] == "SpotM3U-v2.0.0-macos-arm64.pkg"
    assert Path(result["path"]).read_bytes() == b"PK\x03\x04payload"


def test_download_update_uses_a_unique_name_when_collisions_exist(tmp_path, monkeypatch) -> None:
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(desktop, "_downloads_root", lambda: downloads)
    downloads.mkdir()
    (downloads / "SpotM3U.pkg").write_bytes(b"old")
    controls = desktop.WindowControls()
    monkeypatch.setattr(
        desktop.requests,
        "get",
        lambda *args, **kwargs: _chunked_response([b"new"]),
    )

    result = controls.download_update("https://example.com/SpotM3U.pkg", "SpotM3U.pkg")

    assert result["name"] == "SpotM3U (2).pkg"
    assert Path(result["path"]).read_bytes() == b"new"


def test_download_update_rejects_non_https_urls(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(desktop, "_downloads_root", lambda: tmp_path / "Downloads")
    monkeypatch.setattr(desktop.requests, "get", lambda *a, **k: _chunked_response([b"x"]))
    controls = desktop.WindowControls()

    result = controls.download_update("http://example.com/a.pkg", "a.pkg")

    assert "error" in result
    assert (tmp_path / "Downloads" / "a.pkg").exists() is False


def test_download_update_reports_request_failures(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(desktop, "_downloads_root", lambda: tmp_path / "Downloads")
    controls = desktop.WindowControls()

    def fail(*_args, **_kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(desktop.requests, "get", fail)

    result = controls.download_update("https://example.com/a.pkg", "a.pkg")

    assert "error" in result
    assert (tmp_path / "Downloads" / "a.pkg").exists() is False


def _chunked_response(chunks):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size):
            return iter(chunks)

    return _Response()


@pytest.mark.parametrize(
    ("platform_name", "expected_command"),
    [
        pytest.param(
            "darwin", lambda target: ["open", "-R", str(target)], id="darwin-reveals-in-finder"
        ),
        pytest.param(
            "linux",
            lambda target: ["xdg-open", str(target.parent)],
            id="linux-opens-parent-folder",
        ),
    ],
)
def test_open_at_uses_the_platform_file_manager(
    tmp_path, monkeypatch, platform_name, expected_command
) -> None:
    target = tmp_path / "playlist.m3u"
    target.write_text("#EXTM3U\n", encoding="utf-8")
    spawned: list[list[str]] = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args, **kwargs: spawned.append(args))
    monkeypatch.setattr(desktop.sys, "platform", platform_name)
    controls, _window, _app, _job = _controls_client(tmp_path)

    result = controls.open_at(str(target))

    assert result == {"opened": True}
    assert spawned == [expected_command(target)]


def test_open_at_without_a_window_returns_an_error(tmp_path) -> None:
    controls, _window, _app, job = _controls_client(tmp_path)
    job.m3u_path.write_text("#EXTM3U\n", encoding="utf-8")
    controls.attach(None)

    assert controls.open_at(str(job.m3u_path)) == {"error": "The native window is not available."}


def test_open_at_reports_missing_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    controls, _window, _app, _job = _controls_client(tmp_path)

    err = {"error": "That file could not be found."}
    assert controls.open_at(str(tmp_path / "missing.m3u")) == err


def test_show_window_creates_a_frameless_window_with_the_controls_bridge(monkeypatch) -> None:
    created: dict[str, Any] = {}

    fake_window = _FakeWindow()

    class FakeWebview:
        settings: dict[str, Any] = {}

        @staticmethod
        def create_window(title, url, **kwargs):
            created["title"] = title
            created["url"] = url
            created["kwargs"] = kwargs
            return fake_window

        @staticmethod
        def start(**kwargs) -> None:
            created["start"] = kwargs

    monkeypatch.setitem(sys.modules, "webview", FakeWebview)
    monkeypatch.setattr(desktop, "webview_start_kwargs", lambda: {})

    desktop.show_window("http://127.0.0.1:5000/")

    kwargs = created["kwargs"]
    controls = kwargs["js_api"]
    assert kwargs["frameless"] is True
    assert kwargs["easy_drag"] is False
    assert controls.window is fake_window
    assert FakeWebview.settings["ALLOW_DOWNLOADS"] is True

    # show_window must have wired the OS maximize/restore events to the bridge.
    fake_window.events.maximized.fire()
    assert controls.is_maximized() is True


def test_start_server_serves_until_shutdown() -> None:
    server, thread = desktop.start_server(_minimal_app(), launcher.DEFAULT_HOST, 0)
    port = int(server.server_address[1])
    try:
        assert _fetch(f"http://127.0.0.1:{port}/") == "ok"
    finally:
        server.shutdown()
        thread.join(timeout=10)


def test_start_server_releases_the_port_after_shutdown() -> None:
    server, thread = desktop.start_server(_minimal_app(), launcher.DEFAULT_HOST, 0)
    port = int(server.server_address[1])
    server.shutdown()
    thread.join(timeout=10)

    with closing(socket.socket()) as probe:
        probe.bind((launcher.DEFAULT_HOST, port))


def test_run_desktop_opens_window_then_releases_the_port(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(desktop, "create_app", lambda: _minimal_app())
    monkeypatch.setattr(desktop, "show_window", lambda url, **kwargs: opened.append(url))
    monkeypatch.delenv(desktop.NO_WEBVIEW_ENV, raising=False)

    desktop.run_desktop()

    assert len(opened) == 1
    port = int(opened[0].rstrip("/").rsplit(":", 1)[1])
    assert opened[0] == f"http://127.0.0.1:{port}/"
    with closing(socket.socket()) as probe:
        probe.bind((launcher.DEFAULT_HOST, port))


def test_run_desktop_uses_a_dynamic_port_when_preferred_is_taken(monkeypatch) -> None:
    with closing(socket.socket()) as busy:
        busy.bind((launcher.DEFAULT_HOST, 0))
        busy.listen(1)
        taken = int(busy.getsockname()[1])

        app = _minimal_app()
        app.config["PORT"] = taken
        opened: list[str] = []
        monkeypatch.setattr(desktop, "create_app", lambda: app)
        monkeypatch.setattr(desktop, "show_window", lambda url, **kwargs: opened.append(url))
        monkeypatch.delenv(desktop.NO_WEBVIEW_ENV, raising=False)

        desktop.run_desktop()

    port = int(opened[0].rstrip("/").rsplit(":", 1)[1])
    assert port != taken


def test_run_desktop_does_not_open_a_window_when_server_never_ready(monkeypatch) -> None:
    chosen: list[int] = []
    monkeypatch.setattr(desktop, "create_app", lambda: _minimal_app())
    monkeypatch.setattr(
        desktop,
        "select_port",
        lambda preferred, host: chosen.append(_ephemeral_port()) or chosen[-1],
    )
    monkeypatch.setattr(desktop, "wait_for_server", lambda *args, **kwargs: False)
    monkeypatch.setattr(desktop, "show_window", lambda url: pytest.fail("the window must not open"))

    with pytest.raises(RuntimeError, match="did not become ready"):
        desktop.run_desktop()

    assert chosen
    with closing(socket.socket()) as probe:
        probe.bind((launcher.DEFAULT_HOST, chosen[0]))
