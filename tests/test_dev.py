"""Tests for the ``uv run dev`` supervisor that starts Flask and Vite."""

import io
import signal
import socket
import subprocess
from contextlib import closing
from types import SimpleNamespace

import pytest

from spotm3u import dev
from spotm3u.launcher import DEFAULT_HOST


class _FakeProcess:
    """Stand-in for a child process, so no real server is started."""

    def __init__(self, *, exit_code: int | None = None, wait_timeouts: int = 0) -> None:
        self.pid = 4242
        self.stdout = io.StringIO("")
        self.exit_code = exit_code
        self.wait_timeouts = wait_timeouts
        self.waits = 0

    def poll(self) -> int | None:
        return self.exit_code

    def wait(self, timeout: float | None = None) -> int | None:
        self.waits += 1
        if self.waits <= self.wait_timeouts:
            raise subprocess.TimeoutExpired("fake", timeout or 0)
        return self.exit_code


def _free_port() -> int:
    with closing(socket.socket()) as probe:
        probe.bind((DEFAULT_HOST, 0))
        return int(probe.getsockname()[1])


def _config_stub(port: int) -> SimpleNamespace:
    config = SimpleNamespace(port=port)
    return SimpleNamespace(to_app_config=lambda: {"PORT": config.port})


def test_backend_port_prefers_the_configured_port(monkeypatch) -> None:
    preferred = _free_port()
    monkeypatch.setattr(dev, "load_user_config", lambda: _config_stub(preferred))

    assert dev._select_backend_port() == preferred


def test_backend_port_falls_back_when_the_configured_port_is_taken(monkeypatch) -> None:
    with closing(socket.socket()) as taken:
        taken.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        taken.bind((DEFAULT_HOST, 0))
        taken.listen(1)
        taken_port = taken.getsockname()[1]
        monkeypatch.setattr(dev, "load_user_config", lambda: _config_stub(taken_port))

        port = dev._select_backend_port()

    assert port != taken_port


def test_frontend_port_falls_back_when_vite_port_is_taken(monkeypatch) -> None:
    with closing(socket.socket()) as taken:
        taken.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        taken.bind((DEFAULT_HOST, 0))
        taken.listen(1)
        taken_port = taken.getsockname()[1]
        monkeypatch.setattr(dev, "DEFAULT_FRONTEND_PORT", taken_port)

        port = dev._select_frontend_port()

    assert port != taken_port


def test_backend_command_runs_this_interpreter_alone(monkeypatch) -> None:
    monkeypatch.setattr(dev.sys, "executable", "/venv/bin/python")

    assert dev._backend_command() == ["/venv/bin/python", "-m", "spotm3u.dev", "--backend"]


def test_frontend_command_runs_vite_on_the_selected_port(monkeypatch, tmp_path) -> None:
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(dev, "FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(dev.shutil, "which", lambda name: "/usr/bin/npm")

    command = dev._frontend_command(5199)

    assert command == [
        "/usr/bin/npm",
        "run",
        "dev",
        "--",
        "--host",
        DEFAULT_HOST,
        "--port",
        "5199",
        "--strictPort",
    ]


def test_frontend_command_requires_npm(monkeypatch, tmp_path) -> None:
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(dev, "FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(dev.shutil, "which", lambda name: None)

    with pytest.raises(dev.DevError, match="npm was not found"):
        dev._frontend_command(5199)


def test_frontend_command_requires_installed_dependencies(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dev, "FRONTEND_DIR", tmp_path)

    with pytest.raises(dev.DevError, match="npm install"):
        dev._frontend_command(5199)


def test_backend_environment_hands_over_the_selected_port(monkeypatch) -> None:
    monkeypatch.setenv("SPOTM3U_EXISTING", "kept")

    environment = dev._backend_environment(5123)

    assert environment[dev.BACKEND_PORT_ENV] == "5123"
    # Log lines must arrive one at a time even though the child writes to a pipe.
    assert environment["PYTHONUNBUFFERED"] == "1"
    assert environment["SPOTM3U_EXISTING"] == "kept"


def test_frontend_environment_points_the_proxy_at_the_backend() -> None:
    environment = dev._frontend_environment(5123)

    assert environment[dev.BACKEND_URL_ENV] == "http://127.0.0.1:5123"


def test_serve_backend_binds_the_port_the_supervisor_chose(monkeypatch) -> None:
    runs: list[dict] = []
    app = SimpleNamespace(
        config={"PORT": 5001},
        logger=SimpleNamespace(info=lambda *args: None),
        run=lambda **kwargs: runs.append(kwargs),
    )
    monkeypatch.setattr(dev, "create_app", lambda: app)
    monkeypatch.setenv(dev.BACKEND_PORT_ENV, "5123")

    dev._serve_backend()

    assert runs == [{"host": DEFAULT_HOST, "port": 5123, "debug": True, "use_reloader": True}]


def test_serve_backend_uses_the_configured_port_without_the_supervisor(monkeypatch) -> None:
    runs: list[dict] = []
    app = SimpleNamespace(
        config={"PORT": 5055},
        logger=SimpleNamespace(info=lambda *args: None),
        run=lambda **kwargs: runs.append(kwargs),
    )
    monkeypatch.setattr(dev, "create_app", lambda: app)
    monkeypatch.delenv(dev.BACKEND_PORT_ENV, raising=False)

    dev._serve_backend()

    assert runs[0]["port"] == 5055


def _supervisor_stubs(monkeypatch, *, stopped: list[str]) -> list[_FakeProcess]:
    """Point the supervisor at fake children and record how they are stopped."""
    started: list[_FakeProcess] = []

    def spawn(name: str, command: list[str], *, env: dict[str, str], cwd=None) -> dev._Child:
        process = _FakeProcess()
        started.append(process)
        return dev._Child(name, process)

    monkeypatch.setattr(dev, "_spawn", spawn)
    monkeypatch.setattr(dev, "_stop", lambda child: stopped.append(child.name))
    monkeypatch.setattr(dev, "_backend_command", lambda: ["backend"])
    monkeypatch.setattr(dev, "_frontend_command", lambda port: ["frontend"])
    monkeypatch.setattr(dev, "_select_backend_port", lambda: 5001)
    monkeypatch.setattr(dev, "_select_frontend_port", lambda: 5173)
    return started


def test_supervisor_starts_both_servers_and_prints_their_addresses(monkeypatch, capsys) -> None:
    _supervisor_stubs(monkeypatch, stopped=[])

    def interrupt(stop) -> None:
        stop.set()

    monkeypatch.setattr(dev, "_install_signal_handlers", interrupt)

    with pytest.raises(SystemExit) as exit_info:
        dev.main([])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "http://127.0.0.1:5173/" in output
    assert "http://127.0.0.1:5001/" in output


def test_ctrl_c_stops_both_servers(monkeypatch) -> None:
    stopped: list[str] = []
    _supervisor_stubs(monkeypatch, stopped=stopped)

    def interrupt(stop) -> None:
        stop.set()

    monkeypatch.setattr(dev, "_install_signal_handlers", interrupt)

    with pytest.raises(SystemExit) as exit_info:
        dev.main([])

    assert exit_info.value.code == 0
    assert stopped == ["backend", "frontend"]


def test_a_dead_server_stops_the_other_and_reports_its_code(monkeypatch, capsys) -> None:
    stopping: list[str] = []

    def spawn(name: str, command: list[str], *, env: dict[str, str], cwd=None) -> dev._Child:
        # The frontend dies; the backend is still running when it does.
        return dev._Child(name, _FakeProcess(exit_code=3 if name == "frontend" else None))

    monkeypatch.setattr(dev, "_spawn", spawn)
    monkeypatch.setattr(dev, "_stop", lambda child: stopping.append(child.name))
    monkeypatch.setattr(dev, "_backend_command", lambda: ["backend"])
    monkeypatch.setattr(dev, "_frontend_command", lambda port: ["frontend"])
    monkeypatch.setattr(dev, "_select_backend_port", lambda: 5001)
    monkeypatch.setattr(dev, "_select_frontend_port", lambda: 5173)

    with pytest.raises(SystemExit) as exit_info:
        dev.main([])

    assert exit_info.value.code == 3
    assert "frontend exited with code 3" in capsys.readouterr().err
    assert stopping == ["backend", "frontend"]


def test_a_missing_frontend_stops_before_anything_starts(monkeypatch, capsys) -> None:
    started: list[str] = []

    def spawn(name: str, command: list[str], *, env: dict[str, str], cwd=None) -> dev._Child:
        started.append(name)
        return dev._Child(name, _FakeProcess())

    def missing(port: int) -> list[str]:
        raise dev.DevError("The frontend dependencies are missing. Run 'npm install'.")

    monkeypatch.setattr(dev, "_spawn", spawn)
    monkeypatch.setattr(dev, "_frontend_command", missing)
    monkeypatch.setattr(dev, "_select_backend_port", lambda: 5001)
    monkeypatch.setattr(dev, "_select_frontend_port", lambda: 5173)

    with pytest.raises(SystemExit) as exit_info:
        dev.main([])

    assert exit_info.value.code == 1
    assert started == []
    assert "npm install" in capsys.readouterr().err


def test_main_with_the_backend_flag_serves_flask_alone(monkeypatch) -> None:
    served: list[bool] = []
    monkeypatch.setattr(dev, "_serve_backend", lambda: served.append(True))
    monkeypatch.setattr(dev, "_supervise", lambda: pytest.fail("the supervisor must not run"))

    # The Flask child returns once its server stops, so it exits zero by falling
    # off the end of ``main`` instead of raising.
    dev.main(["--backend"])

    assert served == [True]


def test_stop_terminates_the_whole_process_group(monkeypatch) -> None:
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(dev.os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(dev.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    dev._stop(dev._Child("backend", _FakeProcess()))

    assert signals == [(4242, signal.SIGTERM)]


def test_stop_kills_a_child_that_ignores_the_termination(monkeypatch) -> None:
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(dev.os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(dev.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    dev._stop(dev._Child("backend", _FakeProcess(wait_timeouts=1)))

    assert signals == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]


def test_stop_leaves_an_already_exited_child_alone(monkeypatch) -> None:
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(dev.os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(dev.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    dev._stop(dev._Child("backend", _FakeProcess(exit_code=0)))

    assert signals == []


def test_output_is_labelled_per_child(capsys) -> None:
    dev._forward_output("frontend", io.StringIO("VITE v8 ready\nLocal: http://127.0.0.1:5173/\n"))

    output = capsys.readouterr().out
    assert "frontend | VITE v8 ready\n" in output
    assert "frontend | Local: http://127.0.0.1:5173/\n" in output
