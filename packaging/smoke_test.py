"""End-to-end smoke test for a packaged spotm3u bundle or release archive.

Starts the bundled executable directly -- no system Python, uv, or FFmpeg is
used -- then exercises the web server, a rendered template, static assets, and
the FFmpeg binaries bundled under ``_internal``. Exits non-zero on any failure
so CI treats the run as a failed build.

Accepts a bundle directory, a release ZIP, or a directory containing one ZIP.
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
STARTUP_TIMEOUT = 90

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str) -> tuple[int, str]:
    try:
        with _OPENER.open(url, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def find_executable(bundle: Path) -> Path:
    for name in ("spotm3u.exe", "spotm3u"):
        candidate = bundle / name
        if candidate.is_file():
            return candidate
    raise SystemExit(f"no spotm3u executable found in {bundle}")


def check_bundled_ffmpeg(bundle: Path) -> None:
    ffmpeg_dir = bundle / "_internal" / "ffmpeg"
    if not ffmpeg_dir.is_dir():
        raise SystemExit(f"bundled ffmpeg directory missing: expected {ffmpeg_dir}")
    present = {p.name for p in ffmpeg_dir.iterdir() if p.is_file()}
    required = {"ffmpeg", "ffmpeg.exe", "ffprobe", "ffprobe.exe"}
    if not (present & required):
        raise SystemExit(f"ffmpeg/ffprobe not found in {ffmpeg_dir}: got {sorted(present)}")


def smoke_test(bundle: Path) -> None:
    exe = find_executable(bundle)
    check_bundled_ffmpeg(bundle)

    work = Path(tempfile.mkdtemp(prefix="spotm3u-smoke-"))
    config = work / "config.toml"
    config.write_text(f"[web]\nport = {_SMOKE_PORT}\n", encoding="utf-8")
    log_path = work / "server.log"
    env = dict(os.environ)
    env["SPOTM3U_CONFIG"] = str(config)
    log_handle = log_path.open("wb")
    proc = subprocess.Popen(
        [str(exe)],
        cwd=work,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    try:
        port: int | None = None
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            match = _LISTENING.search(text)
            if match:
                port = int(match.group(1))
                break
            if proc.poll() is not None:
                raise SystemExit(f"packaged app exited before listening:\n{text}")
            time.sleep(0.25)
        if port is None:
            raise SystemExit(
                f"packaged app did not start within {STARTUP_TIMEOUT}s:\n"
                f"{log_path.read_text(encoding='utf-8', errors='replace')}"
            )

        status, body = _get(f"http://127.0.0.1:{port}/")
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


def _bundle_root(target: Path) -> Path:
    if target.is_dir():
        if (target / "spotm3u").is_file() or (target / "spotm3u.exe").is_file():
            return target
        archives = sorted(target.glob("*.zip"))
        if len(archives) == 1:
            return _unpack(archives[0], target.parent)
        raise SystemExit(f"expected a bundle directory or exactly one ZIP in {target}")
    return _unpack(target, target.parent)


def _unpack(archive: Path, into: Path) -> Path:
    extract_dir = into / f"{archive.stem}-extracted"
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(extract_dir)
    bundle = extract_dir / "spotm3u"
    _make_executable(bundle / "spotm3u.exe")
    _make_executable(bundle / "spotm3u")
    for name in ("ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"):
        _make_executable(bundle / "_internal" / "ffmpeg" / name)
    return bundle


def _make_executable(path: Path) -> None:
    if path.is_file():
        path.chmod(path.stat().st_mode | 0o111)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <bundle-dir | release.zip | dir-with-zip>")
    smoke_test(_bundle_root(Path(sys.argv[1])))
