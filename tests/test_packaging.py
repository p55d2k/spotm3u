"""Tests for the release packaging helper scripts."""

import importlib.util
import zipfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_PACKAGING = _REPO / "packaging"


def _load(name: str) -> object:
    path = _PACKAGING / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_archive = _load("make_archive")
stage_ffmpeg = _load("stage_ffmpeg")


def test_build_archive_roots_at_parent_directory(tmp_path) -> None:
    bundle = tmp_path / "spotm3u"
    (bundle / "_internal").mkdir(parents=True)
    (bundle / "spotm3u").write_bytes(b"exe")
    (bundle / "_internal" / "data.bin").write_bytes(b"data")

    dest = make_archive.build_archive(bundle, tmp_path / "out" / "app.zip")

    with zipfile.ZipFile(dest) as archive:
        names = sorted(archive.namelist())
    assert names == ["spotm3u/_internal/data.bin", "spotm3u/spotm3u"]
    assert (tmp_path / "out" / "app.zip").exists()


def test_build_archive_fails_when_source_missing(tmp_path) -> None:
    with pytest.raises(SystemExit):
        make_archive.build_archive(tmp_path / "nope", tmp_path / "app.zip")


def test_stage_ffmpeg_copies_real_binaries(tmp_path, monkeypatch) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    a = fake_bin / "ffmpeg.exe"
    a.write_bytes(b"ffmpeg")
    b = fake_bin / "ffprobe.exe"
    b.write_bytes(b"ffprobe")
    monkeypatch.setattr(
        stage_ffmpeg.shutil,
        "which",
        lambda name: str(fake_bin / f"{name}.exe"),
    )

    staged = stage_ffmpeg.stage_ffmpeg(tmp_path / "ffmpeg-stage" / "ffmpeg")

    assert sorted(p.name for p in staged) == ["ffmpeg.exe", "ffprobe.exe"]
    assert all(p.read_bytes() == (fake_bin / p.name).read_bytes() for p in staged)
    assert all(p.stat().st_mode & 0o100 for p in staged)


def test_stage_ffmpeg_errors_when_binary_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(stage_ffmpeg.shutil, "which", lambda name: None)

    with pytest.raises(SystemExit, match="ffmpeg"):
        stage_ffmpeg.stage_ffmpeg(tmp_path / "ffmpeg-stage")
