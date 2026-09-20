"""Tests for the release packaging helper scripts."""

import importlib.util
import os
import shutil
import sys
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
verify_macos_bundle = _load("verify_macos_bundle")


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


def test_build_archive_roots_app_bundle_at_parent(tmp_path) -> None:
    app = tmp_path / "spotm3u.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "spotm3u").write_bytes(b"exe")

    dest = make_archive.build_archive(app, tmp_path / "out" / "app.zip")

    with zipfile.ZipFile(dest) as archive:
        assert archive.namelist() == ["spotm3u.app/Contents/MacOS/spotm3u"]


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_build_archive_preserves_symlinks(tmp_path) -> None:
    app = tmp_path / "spotm3u.app"
    target = app / "Contents" / "Resources" / "ffmpeg"
    target.mkdir(parents=True)
    (target / "ffmpeg").write_bytes(b"ffmpeg")
    link = app / "Contents" / "Frameworks" / "ffmpeg"
    link.parent.mkdir(parents=True)
    os.symlink("../Resources/ffmpeg", link)

    dest = make_archive.build_archive(app, tmp_path / "app.zip")

    with zipfile.ZipFile(dest) as archive:
        info = archive.getinfo("spotm3u.app/Contents/Frameworks/ffmpeg")
        assert info.external_attr >> 16 == 0o120777
        assert archive.read(info) == b"../Resources/ffmpeg"
        assert "spotm3u.app/Contents/Resources/ffmpeg/ffmpeg" in archive.namelist()


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


def _fake_app(tmp_path: Path) -> Path:
    app = tmp_path / "spotm3u.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / "spotm3u"
    exe.write_bytes(b"exe")
    exe.chmod(0o755)
    (app / "Contents" / "Info.plist").write_bytes(b"plist")
    ffmpeg = app / "Contents" / "Resources" / "ffmpeg"
    ffmpeg.mkdir(parents=True)
    for name in ("ffmpeg", "ffprobe"):
        (ffmpeg / name).write_bytes(b"bin")
    resources = app / "Contents" / "Resources" / "spotm3u"
    (resources / "templates").mkdir(parents=True)
    (resources / "templates" / "index.html").write_bytes(b"<html>")
    (resources / "static").mkdir(parents=True)
    (resources / "static" / "style.css").write_bytes(b"body{}")
    (app / "Contents" / "Resources" / "icon.icns").write_bytes(b"\x69\x63\x6e\x73")
    return app


def test_validate_app_accepts_complete_bundle(tmp_path) -> None:
    verify_macos_bundle.validate_app(_fake_app(tmp_path))


def test_validate_app_rejects_missing_executable(tmp_path) -> None:
    app = _fake_app(tmp_path)
    (app / "Contents" / "MacOS" / "spotm3u").unlink()

    with pytest.raises(SystemExit, match="executable"):
        verify_macos_bundle.validate_app(app)


def test_validate_app_rejects_missing_ffmpeg(tmp_path) -> None:
    app = _fake_app(tmp_path)
    shutil.rmtree(app / "Contents" / "Resources" / "ffmpeg")

    with pytest.raises(SystemExit, match="ffmpeg"):
        verify_macos_bundle.validate_app(app)


def test_validate_app_rejects_missing_resource(tmp_path) -> None:
    app = _fake_app(tmp_path)
    (app / "Contents" / "Resources" / "spotm3u" / "templates" / "index.html").unlink()

    with pytest.raises(SystemExit, match="resource"):
        verify_macos_bundle.validate_app(app)


def test_validate_app_rejects_missing_icon(tmp_path) -> None:
    app = _fake_app(tmp_path)
    (app / "Contents" / "Resources" / "icon.icns").unlink()

    with pytest.raises(SystemExit, match="icon"):
        verify_macos_bundle.validate_app(app)


def test_verify_round_trips_archive_with_cross_links(tmp_path) -> None:
    app = _fake_app(tmp_path)
    if sys.platform != "win32":
        frameworks = app / "Contents" / "Frameworks"
        frameworks.mkdir()
        os.symlink("../Resources/ffmpeg", frameworks / "ffmpeg")
        os.symlink("../Resources/spotm3u", frameworks / "spotm3u")
    archive = make_archive.build_archive(app, tmp_path / "release" / "macos.zip")

    found = verify_macos_bundle._find_app(archive)

    verify_macos_bundle.validate_app(found)


def test_find_app_detects_directory_containing_bundle(tmp_path) -> None:
    app = _fake_app(tmp_path)

    assert verify_macos_bundle._find_app(app) == app
    assert verify_macos_bundle._find_app(tmp_path) == app
