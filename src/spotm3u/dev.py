"""Development entry point behind ``uv run dev``.

Starts both halves of the development workflow -- the Flask application with
the debug reloader and the Vite development server for the React frontend --
and supervises them together:

* one command starts both, so no second terminal is needed;
* each child keeps its own output stream, labelled with its name;
* Ctrl+C stops both;
* when either child exits on its own, the other is stopped too, so a failed
  Vite start never leaves Flask listening behind.

The browser only ever talks to Vite: ``frontend/vite.config.ts`` forwards
``/api/*`` to Flask. Both ports are therefore selected here and handed to the
children, which keeps the proxy target and the port Flask binds in sync. The
UI is used in a normal browser with full developer tools; the native WebView
window is a production-only shell and is never started here.

``python -m spotm3u.dev --backend`` is the internal child entry point that runs
Flask alone. It is an implementation detail of the supervisor rather than part
of the documented workflow.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from typing import IO, NamedTuple

from . import frontend
from .app import create_app
from .config import load_user_config
from .launcher import DEFAULT_HOST, DEFAULT_PORT, select_port

# Vite's conventional port. The developer opens this address; it serves the
# React app and proxies API calls to Flask.
DEFAULT_FRONTEND_PORT = 5173

# The address the frontend proxy forwards ``/api/*`` to, read by
# ``frontend/vite.config.ts``. Passing it per run means the frontend never has
# to guess (or hardcode) where Flask ended up.
BACKEND_URL_ENV = "SPOTM3U_DEV_BACKEND"

# The Flask port handed to the backend child. The supervisor has already
# resolved it, so the child binds exactly this port instead of choosing again
# and drifting away from the proxy target.
BACKEND_PORT_ENV = "SPOTM3U_DEV_PORT"

BACKEND_FLAG = "--backend"

FRONTEND_DIR = frontend.FRONTEND_DIR

# How long a child gets to shut down before it is killed outright.
SHUTDOWN_GRACE = 5.0

# How often the supervisor checks whether a child has died.
POLL_INTERVAL = 0.2


class DevError(RuntimeError):
    """A development server cannot be started with this environment."""


class _Child(NamedTuple):
    """One supervised process, named after the half of the app it runs."""

    name: str
    process: subprocess.Popen[str]


def _select_backend_port() -> int:
    """Prefer the configured ``web.port``, falling back to a free port."""
    preferred = int(load_user_config().to_app_config().get("PORT", DEFAULT_PORT))
    return select_port(preferred, DEFAULT_HOST)


def _select_frontend_port() -> int:
    """Prefer Vite's conventional port, falling back to a free port."""
    return select_port(DEFAULT_FRONTEND_PORT, DEFAULT_HOST)


def _backend_command() -> list[str]:
    """The command that runs the Flask half, in this interpreter's venv."""
    return [sys.executable, "-m", "spotm3u.dev", BACKEND_FLAG]


def _frontend_command(port: int) -> list[str]:
    """The command that runs Vite on ``port``, with npm's own checks intact."""
    frontend.require_dependencies()
    return frontend.dev_command(port)


def _backend_environment(port: int) -> dict[str, str]:
    """Environment for the Flask child, bound to ``port``."""
    return {
        **os.environ,
        BACKEND_PORT_ENV: str(port),
        # The child writes to a pipe rather than a terminal, so keep its log
        # lines arriving one by one instead of in delayed blocks.
        "PYTHONUNBUFFERED": "1",
        "FORCE_COLOR": "1",
    }


def _frontend_environment(backend_port: int) -> dict[str, str]:
    """Environment for the Vite child, proxying ``/api`` to ``backend_port``."""
    return {
        **os.environ,
        BACKEND_URL_ENV: f"http://{DEFAULT_HOST}:{backend_port}",
        # Keeps npm's and Vite's colors, which both drop on a pipe.
        "FORCE_COLOR": "1",
    }


def _spawn(name: str, command: list[str], *, env: dict[str, str], cwd: str | None = None) -> _Child:
    """Start one child whose whole process group belongs to this supervisor.

    Flask inherits the working directory so it keeps finding ``./config.toml``;
    npm is pointed at ``frontend/``.
    """
    kwargs: dict[str, object] = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
    }
    if os.name == "nt":
        # No console Ctrl+C of its own; the supervisor decides when it ends.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # A session of its own keeps the child's own children (Werkzeug's
        # reloader, the Vite process npm starts) inside the group that is
        # terminated below instead of surviving it.
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    _forward_output_async(name, process.stdout)
    return _Child(name, process)


def _forward_output(name: str, stream: IO[str]) -> None:
    """Print a child's output, labelling every line with which child it is."""
    prefix = f"{name:>8} | "
    for line in stream:
        print(f"{prefix}{line}", end="", flush=True)


def _forward_output_async(name: str, stream: IO[str] | None) -> None:
    if stream is None:
        return
    threading.Thread(target=_forward_output, args=(name, stream), daemon=True).start()


def _stop(child: _Child) -> None:
    """Terminate a child and everything it started."""
    if child.process.poll() is not None:
        return
    if os.name == "nt":
        _kill(child)
        return
    try:
        os.killpg(os.getpgid(child.process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        child.process.terminate()
    try:
        child.process.wait(timeout=SHUTDOWN_GRACE)
    except subprocess.TimeoutExpired:
        _kill(child)


def _kill(child: _Child) -> None:
    """End a child that did not stop when asked."""
    if os.name == "nt":
        # Windows has no signals to forward, so the tree is ended by force.
        subprocess.run(
            ["taskkill", "/PID", str(child.process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(child.process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            child.process.kill()
    try:
        child.process.wait(timeout=SHUTDOWN_GRACE)
    except subprocess.TimeoutExpired:  # pragma: no cover - unkillable child
        pass


def _install_signal_handlers(stop: threading.Event) -> None:
    """Route Ctrl+C and termination requests to the supervisor's shutdown."""

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def _supervise() -> int:
    """Run both servers until one exits or the developer stops them.

    Returns the exit code for the whole run: zero after an orderly Ctrl+C, and
    the failed child's code when a server dies on its own.
    """
    backend_port = _select_backend_port()
    frontend_port = _select_frontend_port()
    # Resolved before anything starts so a missing npm or an uninstalled
    # frontend fails outright instead of half-starting a server.
    frontend_command = _frontend_command(frontend_port)

    print(
        "SpotM3U development servers\n"
        f"  frontend  http://{DEFAULT_HOST}:{frontend_port}/  (proxies /api)\n"
        f"  backend   http://{DEFAULT_HOST}:{backend_port}/\n"
        "Press Ctrl+C to stop both.",
        flush=True,
    )

    children = [
        _spawn("backend", _backend_command(), env=_backend_environment(backend_port)),
        _spawn(
            "frontend",
            frontend_command,
            cwd=str(FRONTEND_DIR),
            env=_frontend_environment(backend_port),
        ),
    ]

    stop = threading.Event()
    _install_signal_handlers(stop)
    try:
        while not stop.is_set():
            for child in children:
                code = child.process.poll()
                if code is not None:
                    print(
                        f"\n{child.name} exited with code {code}; stopping the other process.",
                        file=sys.stderr,
                        flush=True,
                    )
                    return code or 1
            stop.wait(POLL_INTERVAL)
        return 0
    finally:
        for child in children:
            _stop(child)


def _serve_backend() -> None:
    """Run the Flask development server alone, as the supervisor's child."""
    app = create_app()
    port = int(os.environ.get(BACKEND_PORT_ENV) or app.config.get("PORT", DEFAULT_PORT))
    app.logger.info("SpotM3U dev server: http://%s:%d/", DEFAULT_HOST, port)
    app.run(host=DEFAULT_HOST, port=port, debug=True, use_reloader=True)


def main(argv: list[str] | None = None) -> None:
    """Start both development servers, or serve Flask alone for the supervisor."""
    arguments = sys.argv[1:] if argv is None else argv
    if BACKEND_FLAG in arguments:
        _serve_backend()
        return
    try:
        exit_code = _supervise()
    except (DevError, frontend.FrontendError) as error:
        print(f"error: {error}", file=sys.stderr)
        exit_code = 1
    except KeyboardInterrupt:  # pragma: no cover - handler races the interrupt
        exit_code = 0
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
