"""Tests for the production launcher entry point."""

import io
import logging
import os
import socket
import sys
import types
from contextlib import closing
from pathlib import Path

import pytest

from spotm3u import desktop, launcher, runtime


def _stub_desktop(monkeypatch, *, fail: BaseException | None = None) -> list[dict[str, object]]:
    """Replace the desktop shell so ``launcher.main`` is exercised in isolation."""

    def fake_run_desktop(**kwargs) -> None:
        calls.append(kwargs)
        if fail is not None:
            raise fail

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(desktop, "run_desktop", fake_run_desktop)
    return calls


def test_main_dispatches_to_the_desktop_shell(monkeypatch) -> None:
    calls = _stub_desktop(monkeypatch)

    launcher.main()

    assert calls == [{"open_window": None}]


def test_main_passes_the_window_flag_through(monkeypatch) -> None:
    calls = _stub_desktop(monkeypatch)

    launcher.main(open_window=True)

    assert calls == [{"open_window": True}]


def test_main_ignores_keyboard_interrupt(monkeypatch) -> None:
    _stub_desktop(monkeypatch, fail=KeyboardInterrupt())

    launcher.main()


def test_main_reports_an_import_time_startup_failure(monkeypatch) -> None:
    # ``app.py`` builds the Flask app at module import, so a bad configuration
    # file raises while ``launcher.main`` is importing the desktop shell. That
    # must be reported, not escape as an unhandled traceback.
    reported: list[dict[str, object]] = []
    monkeypatch.setattr(
        launcher,
        "report_startup_error",
        lambda message, **kwargs: reported.append({"message": message, **kwargs}),
    )
    monkeypatch.setitem(sys.modules, "spotm3u.desktop", types.ModuleType("spotm3u.desktop"))

    with pytest.raises(SystemExit) as exit_info:
        launcher.main()

    assert exit_info.value.code == 1
    assert len(reported) == 1
    assert "could not start" in reported[0]["message"]
    assert reported[0]["exception"] is True


def test_main_reports_a_bind_failure_instead_of_failing_silently(monkeypatch) -> None:
    messages: list[str] = []
    _stub_desktop(monkeypatch, fail=SystemExit(1))
    monkeypatch.setattr(
        launcher, "report_startup_error", lambda message, **kwargs: messages.append(message)
    )

    with pytest.raises(SystemExit) as exit_info:
        launcher.main()

    assert exit_info.value.code == 1
    assert len(messages) == 1
    assert "could not start" in messages[0]


def test_main_reports_an_unexpected_startup_error(monkeypatch) -> None:
    reported: list[dict[str, object]] = []
    _stub_desktop(monkeypatch, fail=RuntimeError("webview unavailable"))
    monkeypatch.setattr(
        launcher,
        "report_startup_error",
        lambda message, **kwargs: reported.append({"message": message, **kwargs}),
    )

    with pytest.raises(SystemExit) as exit_info:
        launcher.main()

    assert exit_info.value.code == 1
    assert reported == [
        {"message": "SpotM3U could not start: webview unavailable", "exception": True}
    ]


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


def test_report_startup_error_shows_a_dialog_without_a_console(monkeypatch) -> None:
    dialogs: list[str] = []
    monkeypatch.setattr(launcher, "has_console", lambda: False)
    monkeypatch.setattr(launcher, "write_error_log", lambda *args, **kwargs: Path("error.log"))
    monkeypatch.setattr(
        launcher, "show_message_box", lambda message, **kwargs: dialogs.append(message) or True
    )

    launcher.report_startup_error("spotm3u could not start.")

    assert dialogs == ["spotm3u could not start."]


def test_report_startup_error_writes_a_log_for_packaged_builds(monkeypatch) -> None:
    written: list[dict[str, object]] = []
    monkeypatch.setattr(launcher, "is_frozen", lambda: True)
    monkeypatch.setattr(launcher, "has_console", lambda: True)
    monkeypatch.setattr(
        launcher,
        "write_error_log",
        lambda message, **kwargs: written.append({"message": message, **kwargs}),
    )
    monkeypatch.setattr(
        launcher, "show_message_box", lambda *args, **kwargs: pytest.fail("unexpected dialog")
    )

    launcher.report_startup_error("spotm3u could not start.")

    assert written == [{"message": "spotm3u could not start.", "exception": False}]


def _reset_startup_log() -> None:
    """Detach any file handler the launcher or the process file log attached."""
    logger = logging.getLogger(launcher.PACKAGE_LOGGER)
    for handler in list(logger.handlers):
        if getattr(handler, "_spotm3u_startup", False):
            logger.removeHandler(handler)
            handler.close()
    for attribute in ("_spotm3u_startup_log_configured", "_spotm3u_startup_log_path"):
        if hasattr(logger, attribute):
            delattr(logger, attribute)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_spotm3u_file_log", False):
            root.removeHandler(handler)
            handler.close()
    if hasattr(root, "_spotm3u_file_log_path"):
        delattr(root, "_spotm3u_file_log_path")


def test_configure_startup_log_is_skipped_outside_a_bundle(monkeypatch) -> None:
    monkeypatch.setattr(launcher, "is_frozen", lambda: False)

    assert launcher.configure_startup_log() is None


def test_configure_startup_log_records_messages_to_the_log_file(monkeypatch, tmp_path) -> None:
    _reset_startup_log()
    monkeypatch.setattr(launcher, "is_frozen", lambda: True)
    target = tmp_path / "logs" / "spotm3u.log"
    try:
        configured = launcher.configure_startup_log(target)

        logging.getLogger(launcher.PACKAGE_LOGGER).info("startup reason: boom")

        assert configured == target
        assert "startup reason: boom" in target.read_text(encoding="utf-8")
    finally:
        _reset_startup_log()


def test_configure_startup_log_is_idempotent(monkeypatch, tmp_path) -> None:
    _reset_startup_log()
    monkeypatch.setattr(launcher, "is_frozen", lambda: True)
    target = tmp_path / "spotm3u.log"
    logger = logging.getLogger(launcher.PACKAGE_LOGGER)
    try:
        first = launcher.configure_startup_log(target)
        handlers_after_first = len(logger.handlers)

        second = launcher.configure_startup_log(target)

        assert first == second == target
        assert len(logger.handlers) == handlers_after_first
    finally:
        _reset_startup_log()


def test_packaged_startup_failure_reaches_the_startup_log(monkeypatch, tmp_path) -> None:
    _reset_startup_log()
    monkeypatch.setattr(launcher, "is_frozen", lambda: True)
    monkeypatch.setattr(launcher, "write_error_log", lambda *args, **kwargs: tmp_path / "error.log")
    monkeypatch.setattr(launcher, "show_message_box", lambda *args, **kwargs: True)
    target = tmp_path / "spotm3u.log"
    try:
        launcher.configure_startup_log(target)

        launcher.report_startup_error("the real reason")

        assert "the real reason" in target.read_text(encoding="utf-8")
    finally:
        _reset_startup_log()


def test_write_error_log_appends_startup_details(tmp_path) -> None:
    log_path = tmp_path / "spotm3u-error.log"

    result = launcher.write_error_log("spotm3u could not start.", path=log_path)

    assert result == log_path
    contents = log_path.read_text(encoding="utf-8")
    assert "spotm3u could not start." in contents
    assert contents.endswith("\n")


def test_report_startup_error_logs_for_the_console(monkeypatch, caplog) -> None:
    monkeypatch.setattr(launcher, "has_console", lambda: True)
    monkeypatch.setattr(
        launcher, "show_message_box", lambda message, **kwargs: pytest.fail("unexpected dialog")
    )

    with caplog.at_level(logging.ERROR, logger=launcher.PACKAGE_LOGGER):
        launcher.report_startup_error("spotm3u could not start.")

    assert "spotm3u could not start." in caplog.text


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


def _free_port() -> int:
    """Return a loopback port that was free a moment ago."""
    with closing(socket.socket()) as probe:
        probe.bind((launcher.DEFAULT_HOST, 0))
        return int(probe.getsockname()[1])
