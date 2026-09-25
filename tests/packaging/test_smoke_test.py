"""Tests for the packaged-application smoke test helper."""

import contextlib
import http.server
import importlib.util
import json
import socket
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import REPO_ROOT


def _load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke_test = _load("smoke_test")


def test_find_executable_win_prefers_exe(tmp_path) -> None:
    (tmp_path / "SpotM3U.exe").write_bytes(b"exe")
    (tmp_path / "SpotM3U").write_bytes(b"exe")

    assert smoke_test.find_executable(tmp_path) == tmp_path / "SpotM3U.exe"


def test_find_executable_missing_raises(tmp_path) -> None:
    with pytest.raises(SystemExit, match="SpotM3U"):
        smoke_test.find_executable(tmp_path)


def test_check_bundled_ffmpeg_accepts_platform_binaries(tmp_path) -> None:
    ffmpeg_dir = tmp_path / "_internal" / "ffmpeg"
    ffmpeg_dir.mkdir(parents=True)
    (ffmpeg_dir / "ffmpeg.exe").write_bytes(b"a")
    (ffmpeg_dir / "ffprobe.exe").write_bytes(b"b")

    smoke_test.check_bundled_ffmpeg(tmp_path)


def test_check_bundled_ffmpeg_rejects_missing_dir(tmp_path) -> None:
    with pytest.raises(SystemExit, match="ffmpeg"):
        smoke_test.check_bundled_ffmpeg(tmp_path)


def _fake_app(tmp_path) -> Path:
    app = tmp_path / "SpotM3U.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "SpotM3U").write_bytes(b"exe")
    ffmpeg_dir = app / "Contents" / "Frameworks" / "ffmpeg"
    ffmpeg_dir.mkdir(parents=True)
    (ffmpeg_dir / "ffmpeg").write_bytes(b"a")
    (ffmpeg_dir / "ffprobe").write_bytes(b"b")
    return app


def test_find_executable_inside_app_bundle(tmp_path) -> None:
    app = _fake_app(tmp_path)

    assert smoke_test.is_app_bundle(app)
    assert smoke_test.find_executable(app) == app / "Contents" / "MacOS" / "SpotM3U"


def test_check_bundled_ffmpeg_accepts_app_bundle(tmp_path) -> None:
    smoke_test.check_bundled_ffmpeg(_fake_app(tmp_path))


def test_bundle_root_detects_app_directory(tmp_path) -> None:
    app = _fake_app(tmp_path)

    assert smoke_test._bundle_root(app) == app


def test_bundle_root_finds_app_in_extraction_directory(tmp_path) -> None:
    app = _fake_app(tmp_path)
    container = tmp_path / "extracted"
    container.mkdir()
    app.rename(container / "SpotM3U.app")

    assert smoke_test._bundle_root(container) == container / "SpotM3U.app"


def test_bundle_root_finds_onedir_in_extraction_directory(tmp_path) -> None:
    # Mirrors the Linux verify flow: a release ZIP already extracted into a
    # container directory, with the bundle root one level down.
    container = tmp_path / "SpotM3U-test-extracted"
    bundle = container / "SpotM3U"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    (bundle / "SpotM3U").write_bytes(b"exe")
    # PyInstaller ships its own zip; it must not be mistaken for the archive.
    (internal / "base_library.zip").write_bytes(b"not the release archive")

    assert smoke_test._bundle_root(container) == bundle


def test_bundle_root_rejects_multiple_nested_bundles(tmp_path) -> None:
    container = tmp_path / "extracted"
    for name in ("one", "two"):
        bundle = container / name
        bundle.mkdir(parents=True)
        (bundle / "SpotM3U").write_bytes(b"exe")

    with pytest.raises(SystemExit, match="multiple bundles"):
        smoke_test._bundle_root(container)


def test_bundle_root_unpacks_single_zip(tmp_path) -> None:
    bundle = tmp_path / "SpotM3U"
    bundle.mkdir(parents=True)
    (bundle / "SpotM3U").write_bytes(b"exe")
    archive = tmp_path / "SpotM3U-test.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(bundle / "SpotM3U", "SpotM3U/SpotM3U")
    wrapper = tmp_path / "artifact"
    wrapper.mkdir()
    nested = wrapper / "SpotM3U-test"
    nested.mkdir()
    (nested / archive.name).write_bytes(archive.read_bytes())

    root = smoke_test._bundle_root(wrapper)

    assert (root / "SpotM3U").is_file()


class _FakeProc:
    """Stand-in for ``subprocess.Popen`` that reports an exit status."""

    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


class _HomeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - name required by http.server
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Keep the test output free of request logs."""


@contextlib.contextmanager
def _home_server() -> Iterator[int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HomeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _UpdateCheckHandler(http.server.BaseHTTPRequestHandler):
    """Serves the ``/api/update`` payload of an app built from version 1.2.3."""

    def do_GET(self) -> None:  # noqa: N802 - name required by http.server
        body = json.dumps(
            {"update_available": False, "latest_version": None, "current_version": "1.2.3"}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Keep the test output free of request logs."""


@contextlib.contextmanager
def _update_check_server() -> Iterator[int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _UpdateCheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_check_reported_version_accepts_the_release_version() -> None:
    with _update_check_server() as port:
        smoke_test.check_reported_version(port, "1.2.3")


def test_check_reported_version_rejects_a_bundle_with_a_stale_version() -> None:
    # A bundle that never got the tag stamped in would keep offering an update
    # it already has, so verification has to fail loudly.
    with _update_check_server() as port:
        with pytest.raises(AssertionError, match="reports version '1.2.3', expected '2.0.0'"):
            smoke_test.check_reported_version(port, "2.0.0")


def test_main_requires_a_bundle_path() -> None:
    with pytest.raises(SystemExit, match="usage"):
        smoke_test.main(["a", "b"])
    with pytest.raises(SystemExit, match="usage"):
        smoke_test.main(["--expect-version"])


def test_wait_for_home_uses_the_port_reported_in_the_log(tmp_path) -> None:
    log = tmp_path / "server.log"

    with _home_server() as port:
        log.write_text(f"spotm3u listening on http://127.0.0.1:{port}\n", encoding="utf-8")

        found = smoke_test.wait_for_home(_FakeProc(), log, preferred_port=_free_port(), timeout=15)

    assert found == (port, 200, "ok")


def test_wait_for_home_probes_the_preferred_port_without_a_log(tmp_path) -> None:
    # A windowed Windows build has no standard streams, so there is nothing to
    # parse: readiness has to come from the HTTP response on the port the
    # application was configured to use.
    log = tmp_path / "server.log"

    with _home_server() as port:
        found = smoke_test.wait_for_home(_FakeProc(), log, preferred_port=port, timeout=15)

    assert found == (port, 200, "ok")


def test_wait_for_home_fails_when_the_app_exits(tmp_path) -> None:
    log = tmp_path / "server.log"
    log.write_text("boom", encoding="utf-8")

    with pytest.raises(SystemExit, match="exited with status 3"):
        smoke_test.wait_for_home(_FakeProc(3), log, preferred_port=_free_port(), timeout=15)


def test_wait_for_home_times_out_with_the_captured_log(tmp_path) -> None:
    log = tmp_path / "server.log"
    log.write_text("nothing to see", encoding="utf-8")

    with pytest.raises(SystemExit, match="nothing to see"):
        smoke_test.wait_for_home(_FakeProc(), log, preferred_port=_free_port(), timeout=0.2)


def _dist_bundle() -> Path | None:
    candidate = REPO_ROOT / "dist" / "SpotM3U"
    return candidate if candidate.is_dir() else None


@pytest.mark.skipif(_dist_bundle() is None, reason="no local PyInstaller build")
def test_smoke_test_passes_on_local_build() -> None:
    smoke_test.smoke_test(_dist_bundle())
