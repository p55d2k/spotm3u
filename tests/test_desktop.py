"""Tests for the native desktop application shell."""

import logging
import socket
import urllib.request
from contextlib import closing

import pytest
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


def test_webview_enabled_defaults_to_true(monkeypatch) -> None:
    monkeypatch.delenv(desktop.NO_WEBVIEW_ENV, raising=False)

    assert desktop.webview_enabled() is True


def test_webview_env_var_disables_the_native_window(monkeypatch) -> None:
    monkeypatch.setenv(desktop.NO_WEBVIEW_ENV, "1")

    assert desktop.webview_enabled() is False
    assert desktop.webview_enabled(True) is True


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
    monkeypatch.setattr(desktop, "show_window", lambda url: opened.append(url))
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
        monkeypatch.setattr(desktop, "show_window", lambda url: opened.append(url))
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


def test_run_desktop_serves_when_the_window_is_disabled() -> None:
    app = _minimal_app()
    server, thread = desktop.start_server(app, launcher.DEFAULT_HOST, 0)
    port = int(server.server_address[1])
    try:
        assert _fetch(f"http://127.0.0.1:{port}/") == "ok"
    finally:
        server.shutdown()
        thread.join(timeout=10)
