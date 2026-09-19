"""Tests for bundling a self-contained macOS FFmpeg."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _load(name: str) -> object:
    path = _REPO / "packaging" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bundle = _load("bundle_darwin_ffmpeg")


def _otool(path: Path) -> list[str]:
    return bundle._dylib_deps(path)


def _is_darwin_with_ffmpeg() -> bool:
    return sys.platform == "darwin" and os.environ.get("SPOTM3U_SKIP_FFMPEG_INTEG") is None


def test_homebrew_dep_only_matches_homebrew_prefixes() -> None:
    assert bundle._homebrew_dep("/opt/homebrew/lib/libavcodec.dylib")
    assert bundle._homebrew_dep("/usr/local/lib/foo.dylib")
    assert not bundle._homebrew_dep("/usr/lib/libSystem.B.dylib")
    assert not bundle._homebrew_dep("/System/Library/Frameworks/CoreFoundation.framework")
    assert not bundle._homebrew_dep("/etc/libfoo.dylib")


def test_bundle_darwin_dylibs_rewrites_and_signs_copies(monkeypatch, tmp_path) -> None:
    out = tmp_path / "stage"
    out.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        (out / name).write_bytes(b"fake")
        (out / name).chmod(0o755)
    fake_lib = tmp_path / "libfoo.1.dylib"
    fake_lib.write_bytes(b"lib")
    calls: list[list[str]] = []

    def fake_run(args, *, check=False, **kwargs) -> None:
        calls.append(args)

    def fake_deps(path: Path) -> list[str]:
        if path.name in ("ffmpeg", "ffprobe", "libfoo.1.dylib"):
            return [str(fake_lib)]
        return []

    monkeypatch.setattr(bundle, "_dylib_deps", fake_deps)
    monkeypatch.setattr(bundle, "_homebrew_dep", lambda dep: True)
    monkeypatch.setattr(bundle, "_tool", lambda name: f"/usr/bin/{name}")

    class FakeSubprocess:
        DEVNULL = -3

    fake_subprocess = FakeSubprocess()
    fake_subprocess.run = fake_run
    monkeypatch.setattr(bundle, "subprocess", fake_subprocess)

    bundle.bundle_darwin_dylibs(out)

    copied = out / "libfoo.1.dylib"
    assert copied.is_file()
    changes = [args for args in calls if args and args[0].endswith("install_name_tool")]
    assert changes
    assert any(str(copied) == args[4] for args in changes)
    assert any(str(out / "ffmpeg") == args[4] for args in changes)
    assert any(args[1] == "-change" for args in changes)
    assert any(args[0] == "codesign" and args[1] == "--force" for args in calls)


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("SPOTM3U_SKIP_FFMPEG_INTEG"),
    reason="integration test requires macOS and Homebrew FFmpeg",
)
def test_bundle_darwin_ffmpeg_is_self_contained(tmp_path) -> None:
    out = tmp_path / "ffmpeg"
    bundle.bundle_darwin_ffmpeg(out)

    for name in ("ffmpeg", "ffprobe"):
        binary = out / name
        assert binary.stat().st_mode & 0o100
        deps = _otool(binary)
        assert deps, f"{name}: otool found no dependencies"
        homebrew_deps = [d for d in deps if bundle._homebrew_dep(d)]
        assert not homebrew_deps, f"{name} still references {homebrew_deps}"
        assert all(
            d.startswith("@loader_path/") or d.startswith("/usr/lib/") or d.startswith("/System/")
            for d in deps
        )
        result = subprocess.run(
            [str(binary), "-version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "FFmpeg developers" in result.stdout.splitlines()[0]
