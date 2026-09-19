"""Tests for the production launcher entry point."""

import io
import logging
import os
import socket
import sys
from contextlib import closing
from pathlib import Path

import pytest

from spotm3u import launcher, runtime


class _FakeApp:
    """Attribute-based stand-in for the Flask app used by ``serve``."""

    def __init__(self, *, fail: BaseException | None = None) -> None:
        self.config = {"PORT": 5001}
        self.logger = logging.getLogger("test-launcher")
        self.run_calls: dict[str, object] = {}
        self._fail = fail

    def run(self, **kwargs) -> None:
        self.run_calls.update(kwargs)
        if self._fail is not None:
            raise self._fail


def _free_port() -> int:
    """Return a loopback port that was free a moment ago."""
    with closing(socket.socket()) as probe:
        probe.bind((launcher.DEFAULT_HOST, 0))
        return int(probe.getsockname()[1])


def _launch(monkeypatch, *, app, port=5123, browser=False) -> list[dict[str, object]]:
    """Run the launcher against a fake app without binding or opening anything."""
    opened: list[dict[str, object]] = []
    monkeypatch.setattr(launcher, "create_app", lambda: app)
    monkeypatch.setattr(launcher, "select_port", lambda preferred, host: port)
    monkeypatch.setattr(
        launcher,
        "start_browser_opener",
        lambda url, *, host, port, timeout=launcher.READINESS_TIMEOUT: opened.append(
            {"url": url, "host": host, "port": port}
        ),
    )
    launcher.main(open_browser=browser)
    return opened


def test_main_serves_flask_app_without_debug(monkeypatch) -> None:
    # Don't actually bind a socket.
    fake_app = _FakeApp()

    _launch(monkeypatch, app=fake_app)

    assert fake_app.run_calls["host"] == "127.0.0.1"
    assert fake_app.run_calls["port"] == 5123
    assert fake_app.run_calls["debug"] is False
    assert fake_app.run_calls["use_reloader"] is False
    assert fake_app.run_calls["threaded"] is True


def test_main_serves_on_a_dynamically_selected_port(monkeypatch) -> None:
    fake_app = _FakeApp()

    _launch(monkeypatch, app=fake_app, port=5399)

    assert fake_app.run_calls["port"] == 5399


def test_main_opens_the_browser_once_on_the_served_port(monkeypatch) -> None:
    fake_app = _FakeApp()

    opened = _launch(monkeypatch, app=fake_app, port=5123, browser=True)

    assert opened == [{"url": "http://127.0.0.1:5123/", "host": "127.0.0.1", "port": 5123}]
    assert fake_app.run_calls["port"] == 5123


def test_main_does_not_open_the_browser_when_disabled(monkeypatch) -> None:
    fake_app = _FakeApp()

    opened = _launch(monkeypatch, app=fake_app, browser=False)

    assert opened == []


def test_browser_env_var_disables_the_automatic_tab(monkeypatch) -> None:
    monkeypatch.delenv(launcher.BROWSER_ENV, raising=False)
    assert launcher.browser_enabled() is True

    monkeypatch.setenv(launcher.BROWSER_ENV, "1")
    assert launcher.browser_enabled() is False
    # An explicit choice always wins over the environment.
    assert launcher.browser_enabled(True) is True


def test_select_port_keeps_a_free_preferred_port() -> None:
    port = _free_port()

    assert launcher.select_port(port) == port


def test_select_port_falls_back_when_the_preferred_port_is_taken() -> None:
    with closing(socket.socket()) as busy:
        busy.bind((launcher.DEFAULT_HOST, 0))
        busy.listen(1)
        taken = int(busy.getsockname()[1])

        selected = launcher.select_port(taken)

    assert selected != taken
    assert 1 <= selected <= 65535


@pytest.mark.parametrize("preferred", [0, -1, 70000])
def test_select_port_ignores_an_invalid_preferred_port(preferred: int) -> None:
    selected = launcher.select_port(preferred)

    assert launcher.MIN_PORT <= selected <= launcher.MAX_PORT


def test_wait_for_server_sees_a_listening_socket() -> None:
    with closing(socket.socket()) as server:
        server.bind((launcher.DEFAULT_HOST, 0))
        server.listen(1)
        port = int(server.getsockname()[1])

        assert launcher.wait_for_server(launcher.DEFAULT_HOST, port, timeout=5) is True


def test_wait_for_server_times_out_when_nothing_listens() -> None:
    assert launcher.wait_for_server(launcher.DEFAULT_HOST, _free_port(), timeout=0.2) is False


def test_browser_opens_only_after_the_server_is_ready(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url) or True)

    with closing(socket.socket()) as server:
        server.bind((launcher.DEFAULT_HOST, 0))
        server.listen(1)
        port = int(server.getsockname()[1])
        thread = launcher.start_browser_opener(
            f"http://127.0.0.1:{port}/", host=launcher.DEFAULT_HOST, port=port
        )
        thread.join(timeout=10)

    assert opened == [f"http://127.0.0.1:{port}/"]


def test_browser_stays_closed_when_the_server_never_starts(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url) or True)
    port = _free_port()

    thread = launcher.start_browser_opener(
        f"http://127.0.0.1:{port}/", host=launcher.DEFAULT_HOST, port=port, timeout=0.2
    )
    thread.join(timeout=10)

    assert opened == []
    assert not thread.is_alive()


def test_report_startup_error_shows_a_dialog_without_a_console(monkeypatch) -> None:
    dialogs: list[str] = []
    monkeypatch.setattr(launcher, "has_console", lambda: False)
    monkeypatch.setattr(
        launcher, "show_message_box", lambda message, **kwargs: dialogs.append(message) or True
    )

    launcher.report_startup_error("spotm3u could not start.")

    assert dialogs == ["spotm3u could not start."]


def test_report_startup_error_logs_for_the_console(monkeypatch, caplog) -> None:
    monkeypatch.setattr(launcher, "has_console", lambda: True)
    monkeypatch.setattr(
        launcher, "show_message_box", lambda message, **kwargs: pytest.fail("unexpected dialog")
    )

    with caplog.at_level(logging.ERROR, logger=launcher.PACKAGE_LOGGER):
        launcher.report_startup_error("spotm3u could not start.")

    assert "spotm3u could not start." in caplog.text


def test_main_reports_a_bind_failure_instead_of_failing_silently(monkeypatch) -> None:
    messages: list[str] = []
    fake_app = _FakeApp(fail=SystemExit(1))
    monkeypatch.setattr(
        launcher, "report_startup_error", lambda message, **kwargs: messages.append(message)
    )

    with pytest.raises(SystemExit) as exit_info:
        _launch(monkeypatch, app=fake_app)

    assert exit_info.value.code == 1
    assert len(messages) == 1
    assert "could not start" in messages[0]


def test_main_reports_an_unexpected_startup_error(monkeypatch) -> None:
    reported: list[dict[str, object]] = []
    fake_app = _FakeApp(fail=RuntimeError("no sockets left"))
    monkeypatch.setattr(
        launcher,
        "report_startup_error",
        lambda message, **kwargs: reported.append({"message": message, **kwargs}),
    )

    with pytest.raises(SystemExit) as exit_info:
        _launch(monkeypatch, app=fake_app)

    assert exit_info.value.code == 1
    assert reported == [{"message": "spotm3u could not start: no sockets left", "exception": True}]


def test_has_console_is_false_without_standard_streams(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    assert runtime.has_console() is False


def test_has_console_accepts_either_stream(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    assert runtime.has_console() is True


def test_bundle_root_falls_back_to_executable_directory(monkeypatch) -> None:
    monkeypatch.delenv("_MEIPASS", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert launcher.bundle_roots()[-1] == Path(sys.executable).resolve().parent


def test_bundle_config_points_at_bundled_config_toml(monkeypatch, tmp_path) -> None:
    bundled_config = tmp_path / "config.toml"
    bundled_config.write_text("", encoding="utf-8")
    monkeypatch.setattr(launcher, "is_frozen", lambda: True)
    monkeypatch.setattr(launcher, "bundle_roots", lambda: (tmp_path,))
    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)

    launcher.configure_config_path_for_bundle()

    assert os.environ.get("SPOTM3U_CONFIG") == str(bundled_config)


def test_bundle_config_respects_explicit_config_env(monkeypatch, tmp_path) -> None:
    tomldir = tmp_path / "bin"
    tomldir.mkdir()
    explicit = tomldir / "explicit.toml"
    explicit.write_text("", encoding="utf-8")
    monkeypatch.setattr(launcher, "is_frozen", lambda: True)
    monkeypatch.setattr(launcher, "bundle_roots", lambda: (tmp_path,))
    monkeypatch.setenv("SPOTM3U_CONFIG", str(explicit))

    launcher.configure_config_path_for_bundle()

    assert os.environ["SPOTM3U_CONFIG"] == str(explicit)


def test_bundle_config_prefers_executable_dir_over_internal(monkeypatch, tmp_path) -> None:
    exe_dir = tmp_path / "exe"
    internal = tmp_path / "_internal"
    exe_dir.mkdir()
    internal.mkdir()
    user_config = exe_dir / "config.toml"
    user_config.write_text("", encoding="utf-8")
    (internal / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(launcher, "is_frozen", lambda: True)
    monkeypatch.setattr(launcher, "bundle_roots", lambda: (internal, exe_dir))
    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)

    launcher.configure_config_path_for_bundle()

    assert os.environ["SPOTM3U_CONFIG"] == str(user_config)
