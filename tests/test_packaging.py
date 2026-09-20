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
make_pkg = _load("make_pkg")


def test_build_archive_roots_at_parent_directory(tmp_path) -> None:
    bundle = tmp_path / "SpotM3U"
    (bundle / "_internal").mkdir(parents=True)
    (bundle / "SpotM3U").write_bytes(b"exe")
    (bundle / "_internal" / "data.bin").write_bytes(b"data")

    dest = make_archive.build_archive(bundle, tmp_path / "out" / "app.zip")

    with zipfile.ZipFile(dest) as archive:
        names = sorted(archive.namelist())
    assert names == ["SpotM3U/SpotM3U", "SpotM3U/_internal/data.bin"]
    assert (tmp_path / "out" / "app.zip").exists()


def test_build_archive_fails_when_source_missing(tmp_path) -> None:
    with pytest.raises(SystemExit):
        make_archive.build_archive(tmp_path / "nope", tmp_path / "app.zip")


def test_build_archive_roots_app_bundle_at_parent(tmp_path) -> None:
    app = tmp_path / "SpotM3U.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "SpotM3U").write_bytes(b"exe")

    dest = make_archive.build_archive(app, tmp_path / "out" / "app.zip")

    with zipfile.ZipFile(dest) as archive:
        assert archive.namelist() == ["SpotM3U.app/Contents/MacOS/SpotM3U"]


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_build_archive_preserves_symlinks(tmp_path) -> None:
    app = tmp_path / "SpotM3U.app"
    target = app / "Contents" / "Resources" / "ffmpeg"
    target.mkdir(parents=True)
    (target / "ffmpeg").write_bytes(b"ffmpeg")
    link = app / "Contents" / "Frameworks" / "ffmpeg"
    link.parent.mkdir(parents=True)
    os.symlink("../Resources/ffmpeg", link)

    dest = make_archive.build_archive(app, tmp_path / "app.zip")

    with zipfile.ZipFile(dest) as archive:
        info = archive.getinfo("SpotM3U.app/Contents/Frameworks/ffmpeg")
        assert info.external_attr >> 16 == 0o120777
        assert archive.read(info) == b"../Resources/ffmpeg"
        assert "SpotM3U.app/Contents/Resources/ffmpeg/ffmpeg" in archive.namelist()


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
    app = tmp_path / "SpotM3U.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / "SpotM3U"
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
    (app / "Contents" / "MacOS" / "SpotM3U").unlink()

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


def test_pkg_command_installs_to_applications() -> None:
    staging = Path("/tmp/pkgstage")
    dest = Path("/tmp/SpotM3U-v1.0.0-macos-arm64.pkg")

    command = make_pkg.pkg_command(staging, dest, make_pkg.IDENTIFIER, "1.0.0")

    assert command[0] == "pkgbuild"
    assert command[command.index(str(staging)) - 1] == "--root"
    assert command[command.index("/Applications") - 1] == "--install-location"
    assert command[command.index(make_pkg.IDENTIFIER) - 1] == "--identifier"
    assert command[command.index("1.0.0") - 1] == "--version"
    assert command[-1] == str(dest)


def _fake_pkg_result(returncode: int = 0) -> object:
    return type("Result", (), {"returncode": returncode, "stdout": "", "stderr": "boom"})()  # type: ignore[misc]


def test_build_pkg_stages_clean_bundle_and_runs_pkgbuild(tmp_path, monkeypatch) -> None:
    app = tmp_path / "SpotM3U.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "SpotM3U").write_bytes(b"exe")
    dest = tmp_path / "out" / "SpotM3U.pkg"
    commands: list[list[str]] = []
    monkeypatch.setattr(
        make_pkg.subprocess,
        "run",
        lambda command, **_kw: commands.append(command) or _fake_pkg_result(),
    )

    created = make_pkg.build_pkg(app, dest, version="1.1.0")

    assert created == dest
    ditto = [c for c in commands if c[0] == "ditto"]
    stripped = [c for c in commands if c[0] == "xattr"]
    built = [c for c in commands if c[0] == "pkgbuild"]
    assert len(ditto) == len(stripped) == len(built) == 1
    staged_app = Path(ditto[0][-1])
    assert staged_app != app
    assert staged_app.suffix == ".app"
    assert stripped[0] == ["xattr", "-cr", str(staged_app)]
    assert built[0][-1] == str(dest)
    assert built[0][built[0].index("1.1.0") - 1] == "--version"
    assert not staged_app.parent.exists()


def test_build_pkg_keeps_provided_staging_dir(tmp_path, monkeypatch) -> None:
    app = tmp_path / "SpotM3U.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "SpotM3U").write_bytes(b"exe")
    staging = tmp_path / "stage"
    staging.mkdir()
    monkeypatch.setattr(make_pkg.subprocess, "run", lambda _command, **_kw: _fake_pkg_result())

    make_pkg.build_pkg(app, tmp_path / "out.pkg", staging_dir=staging)

    assert staging.is_dir()


def test_build_pkg_fails_when_source_missing(tmp_path) -> None:
    with pytest.raises(SystemExit, match="does not exist"):
        make_pkg.build_pkg(tmp_path / "SpotM3U.app", tmp_path / "out.pkg")


def test_build_pkg_rejects_a_non_app_source(tmp_path) -> None:
    source = tmp_path / "SpotM3U"
    source.mkdir()

    with pytest.raises(SystemExit, match="\\.app bundle"):
        make_pkg.build_pkg(source, tmp_path / "out.pkg")


def test_build_pkg_fails_loudly_when_pkgbuild_fails(tmp_path, monkeypatch) -> None:
    app = tmp_path / "SpotM3U.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "SpotM3U").write_bytes(b"exe")

    def failing(command, **_kw):
        if command[0] == "pkgbuild":
            return _fake_pkg_result(returncode=1)
        return _fake_pkg_result()

    monkeypatch.setattr(make_pkg.subprocess, "run", failing)

    with pytest.raises(SystemExit, match="pkgbuild failed"):
        make_pkg.build_pkg(app, tmp_path / "out.pkg")
