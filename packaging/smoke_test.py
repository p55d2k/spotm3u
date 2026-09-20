"""End-to-end smoke test for a packaged spotm3u bundle or release archive.

Starts the bundled executable directly -- no system Python, uv, or FFmpeg is
used -- then exercises the web server, a rendered template, static assets, and
the bundled FFmpeg binaries. Understands both the onedir layout (executable and
``_internal`` beside each other) and the macOS ``spotm3u.app`` bundle layout.
Exits non-zero on any failure so CI treats the run as a failed build.

The application opens its UI in a native WebView on start; that is disabled
here because a CI runner has no display and the test drives the server over
HTTP itself. The Windows build is windowed, so the port is probed over HTTP
instead of being read from the startup log, which that build does not have.

Accepts a bundle directory, an extracted ``.app``, a release ZIP, or a
directory containing one ZIP.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HOME_MARKER = "Open Exportify"
_LISTENING = re.compile(r"listening on http://127\.0\.0\.1:(\d+)")
_SMOKE_PORT = 5290
_POLL_INTERVAL = 0.25
STARTUP_TIMEOUT = 90
_SYMLINK_MODE = 0o120777

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str) -> tuple[int, str]:
    try:
        with _OPENER.open(url, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def is_app_bundle(bundle: Path) -> bool:
    """True when ``bundle`` is a macOS ``.app`` application bundle."""
    return bundle.suffix == ".app" and (bundle / "Contents" / "MacOS").is_dir()


def _executable_directory(bundle: Path) -> Path:
    """Directory that directly contains the packaged executable."""
    return bundle / "Contents" / "MacOS" if is_app_bundle(bundle) else bundle


def find_executable(bundle: Path) -> Path:
    directory = _executable_directory(bundle)
    candidates = {
        candidate.name.lower(): candidate
        for candidate in directory.iterdir()
        if candidate.is_file()
    }
    for name in ("spotm3u.exe", "spotm3u"):
        candidate = candidates.get(name)
        if candidate is not None:
            return candidate
    raise SystemExit(f"no spotm3u executable found in {bundle}")


def _ffmpeg_directories(bundle: Path) -> tuple[Path, ...]:
    """Bundle locations that may hold FFmpeg, in preference order.

    One-folder builds keep FFmpeg under ``_internal``; macOS ``.app`` bundles
    keep it in ``Contents/Frameworks`` or ``Contents/Resources`` depending on
    how PyInstaller classifies and cross-links the directory.
    """
    if is_app_bundle(bundle):
        contents = bundle / "Contents"
        return (
            contents / "Frameworks" / "ffmpeg",
            contents / "Resources" / "ffmpeg",
            contents / "MacOS" / "ffmpeg",
        )
    return (bundle / "_internal" / "ffmpeg", bundle / "ffmpeg")


def check_bundled_ffmpeg(bundle: Path) -> None:
    required = {"ffmpeg", "ffprobe"}
    for ffmpeg_dir in _ffmpeg_directories(bundle):
        if not ffmpeg_dir.is_dir():
            continue
        present = {p.stem.lower() for p in ffmpeg_dir.iterdir() if p.is_file()}
        if present & required:
            return
    raise SystemExit(
        f"bundled ffmpeg directory missing: expected one of {_ffmpeg_directories(bundle)}"
    )


def _read_log(log_path: Path) -> str:
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _fail_if_exited(proc: subprocess.Popen, log_path: Path) -> None:
    """Fail fast with the captured output when the app died during startup."""
    code = proc.poll()
    if code is not None:
        raise SystemExit(
            f"packaged app exited with status {code} before serving:\n{_read_log(log_path)}"
        )


def wait_for_home(
    proc: subprocess.Popen,
    log_path: Path,
    *,
    preferred_port: int = _SMOKE_PORT,
    timeout: float = STARTUP_TIMEOUT,
) -> tuple[int, int, str]:
    """Wait for the packaged app to serve its home page.

    Returns ``(port, status, body)``. The startup log is still honored when the
    app had to fall back to a different port, but readiness itself comes from
    the HTTP response so a windowed Windows build -- which has no standard
    streams to log to -- is verified the same way as every other platform.
    """
    deadline = time.monotonic() + timeout
    port = preferred_port
    while time.monotonic() < deadline:
        match = _LISTENING.search(_read_log(log_path))
        if match:
            port = int(match.group(1))
        try:
            status, body = _get(f"http://127.0.0.1:{port}/")
        except OSError:
            status, body = 0, ""
        if status:
            return port, status, body
        _fail_if_exited(proc, log_path)
        time.sleep(_POLL_INTERVAL)
    raise SystemExit(f"packaged app did not serve within {timeout}s:\n{_read_log(log_path)}")


def smoke_test(bundle: Path) -> None:
    exe = find_executable(bundle)
    check_bundled_ffmpeg(bundle)

    work = Path(tempfile.mkdtemp(prefix="spotm3u-smoke-"))
    config = work / "config.toml"
    config.write_text(f"[web]\nport = {_SMOKE_PORT}\n", encoding="utf-8")
    log_path = work / "server.log"
    env = dict(os.environ)
    env["SPOTM3U_CONFIG"] = str(config)
    env["SPOTM3U_NO_WEBVIEW"] = "1"
    log_handle = log_path.open("wb")
    proc = subprocess.Popen(
        [str(exe)],
        cwd=work,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    try:
        port, status, body = wait_for_home(proc, log_path)
        assert status == 200, f"home returned {status}"
        assert HOME_MARKER in body, f"home template marker {HOME_MARKER!r} missing"

        status, css = _get(f"http://127.0.0.1:{port}/static/style.css")
        assert status == 200, f"static returned {status}"
        assert css.strip(), "static stylesheet is empty"

        assert _get(f"http://127.0.0.1:{port}/no-such-route")[0] == 404
        print(f"smoke test passed: {exe} on 127.0.0.1:{port}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_handle.close()


def _has_executable(directory: Path) -> bool:
    return any((directory / name).is_file() for name in ("spotm3u", "spotm3u.exe"))


def _app_root(start: Path) -> Path | None:
    """The enclosing ``.app`` bundle for a path inside one, if any."""
    for candidate in (start, *start.parents):
        if candidate.suffix == ".app":
            return candidate
    return None


def _bundle_root(target: Path) -> Path:
    target = target.expanduser().resolve()
    if target.is_dir():
        if is_app_bundle(target) or _has_executable(target):
            return target
        apps = sorted(p for p in target.rglob("*.app") if p.is_dir())
        if len(apps) == 1:
            return apps[0]
        if len(apps) > 1:
            raise SystemExit(f"multiple .app bundles found in {target}")
        # Onedir layout whose bundle root is nested one level down, e.g. an
        # already-extracted release ZIP (``<dir>/spotm3u/spotm3u``). Detect it
        # before the single-ZIP fallback, which would otherwise mistake
        # PyInstaller's own ``_internal/base_library.zip`` for the archive.
        bundles = sorted(p for p in target.iterdir() if p.is_dir() and _has_executable(p))
        if len(bundles) == 1:
            return bundles[0]
        if len(bundles) > 1:
            raise SystemExit(f"multiple bundles found in {target}")
        archives = sorted(target.rglob("*.zip"))
        if len(archives) == 1:
            return _unpack(archives[0], target.parent)
        executables = [
            p
            for p in target.rglob("*")
            if p.is_file() and p.name.lower() in {"spotm3u", "spotm3u.exe"}
        ]
        if len(executables) == 1:
            return _app_root(executables[0].parent) or executables[0].parent
        raise SystemExit(f"expected a bundle directory, .app, or exactly one ZIP in {target}")
    return _unpack(target, target.parent)


def _extract_zip(archive: Path, into: Path) -> None:
    """Extract a ZIP, restoring Unix permissions and symlink entries."""
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            zf.extract(info, into)
            target = into / info.filename
            mode = info.external_attr >> 16
            if mode == _SYMLINK_MODE:
                link = target.read_text(encoding="utf-8")
                target.unlink()
                target.symlink_to(link)
            elif mode & 0o777:
                target.chmod(mode & 0o777)


def _unpack(archive: Path, into: Path) -> Path:
    extract_dir = into / f"{archive.stem}-extracted"
    _extract_zip(archive, extract_dir)
    for candidate in extract_dir.rglob("*"):
        if candidate.is_file() and candidate.name.lower() in {"spotm3u", "spotm3u.exe"}:
            bundle = _app_root(candidate.parent) or candidate.parent
            _make_executable(candidate)
            for ffmpeg_dir in _ffmpeg_directories(bundle):
                for name in ("ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"):
                    _make_executable(ffmpeg_dir / name)
            return bundle
    raise SystemExit(f"no spotm3u executable found in extracted archive {archive}")


def _make_executable(path: Path) -> None:
    if path.is_file():
        path.chmod(path.stat().st_mode | 0o111)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <bundle-dir | release.zip | dir-with-zip>")
    smoke_test(_bundle_root(Path(sys.argv[1])))
