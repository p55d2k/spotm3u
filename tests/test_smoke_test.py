"""Tests for the packaged-application smoke test helper."""

import importlib.util
import zipfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, _REPO / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke_test = _load("smoke_test")


def test_find_executable_win_prefers_exe(tmp_path) -> None:
    (tmp_path / "spotm3u.exe").write_bytes(b"exe")
    (tmp_path / "spotm3u").write_bytes(b"exe")

    assert smoke_test.find_executable(tmp_path) == tmp_path / "spotm3u.exe"


def test_find_executable_missing_raises(tmp_path) -> None:
    with pytest.raises(SystemExit, match="spotm3u"):
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


def test_bundle_root_unpacks_single_zip(tmp_path) -> None:
    bundle = tmp_path / "spotm3u"
    bundle.mkdir(parents=True)
    (bundle / "spotm3u").write_bytes(b"exe")
    archive = tmp_path / "spotm3u-test.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(bundle / "spotm3u", "spotm3u/spotm3u")
    wrapper = tmp_path / "artifact"
    wrapper.mkdir()
    (wrapper / archive.name).write_bytes(archive.read_bytes())

    root = smoke_test._bundle_root(wrapper)

    assert (root / "spotm3u").is_file()


def _dist_bundle() -> Path | None:
    candidate = Path(__file__).resolve().parent.parent / "dist" / "spotm3u"
    return candidate if candidate.is_dir() else None


@pytest.mark.skipif(_dist_bundle() is None, reason="no local PyInstaller build")
def test_smoke_test_passes_on_local_build() -> None:
    smoke_test.smoke_test(_dist_bundle())
