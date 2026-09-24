"""Tests for the release packaging helper scripts."""

import ast
import importlib.util
import os
import plistlib
import shutil
import struct
import sys
import xml.etree.ElementTree as ET
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


def test_windows_app_config_enables_load_from_remote_sources() -> None:
    # pythonnet loads its .NET assembly through Assembly.LoadFrom, which refuses
    # browser-downloaded (Mark of the Web) files unless the host enables this.
    root = ET.parse(_PACKAGING / "windows_app_config.xml").getroot()
    setting = root.find("./runtime/loadFromRemoteSources")

    assert setting is not None
    assert setting.get("enabled") == "true"


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


def _icns_payload() -> bytes:
    """A minimal well-formed .icns container holding one PNG element."""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 128, 128)
    element = b"ic07" + struct.pack(">I", 8 + len(png)) + png
    return b"icns" + struct.pack(">I", 8 + len(element)) + element


def _fake_app(tmp_path: Path) -> Path:
    app = tmp_path / "SpotM3U.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / "SpotM3U"
    exe.write_bytes(b"exe")
    exe.chmod(0o755)
    plist = {
        "CFBundleExecutable": "SpotM3U",
        "CFBundleIconFile": "icon.icns",
        "CFBundleIdentifier": "com.p55d2k.spotm3u",
        "NSHighResolutionCapable": True,
    }
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(plist))
    ffmpeg = app / "Contents" / "Resources" / "ffmpeg"
    ffmpeg.mkdir(parents=True)
    for name in ("ffmpeg", "ffprobe"):
        (ffmpeg / name).write_bytes(b"bin")
    resources = app / "Contents" / "Resources"
    # The built React frontend the bundled Flask app serves at the root.
    bundle_frontend = resources / "frontend"
    (bundle_frontend / "assets").mkdir(parents=True)
    (bundle_frontend / "index.html").write_bytes(b'<div id="root"></div>')
    (bundle_frontend / "assets" / "app.js").write_bytes(b"console.log(1)")
    (resources / "icon.icns").write_bytes(_icns_payload())
    return app


def test_validate_app_accepts_complete_bundle(tmp_path) -> None:
    verify_macos_bundle.validate_app(_fake_app(tmp_path))


def _break_plist(app: Path, mutate) -> None:
    """Rewrite the bundle's Info.plist after applying ``mutate`` to its keys."""
    plist_path = app / "Contents" / "Info.plist"
    plist = plistlib.loads(plist_path.read_bytes())
    mutate(plist)
    plist_path.write_bytes(plistlib.dumps(plist))


def _drop_plist_key(app: Path, key: str) -> None:
    _break_plist(app, lambda plist: plist.pop(key, None))


def _set_plist_key(app: Path, key: str, value: object) -> None:
    def set_key(plist: dict) -> None:
        plist[key] = value

    _break_plist(app, set_key)


@pytest.mark.parametrize(
    ("break_bundle", "message"),
    [
        pytest.param(
            lambda app: (app / "Contents" / "MacOS" / "SpotM3U").unlink(),
            "executable",
            id="missing-executable",
        ),
        pytest.param(
            lambda app: shutil.rmtree(app / "Contents" / "Resources" / "ffmpeg"),
            "ffmpeg",
            id="missing-ffmpeg",
        ),
        pytest.param(
            lambda app: (app / "Contents" / "Resources" / "frontend" / "index.html").unlink(),
            "resource",
            id="missing-react-build",
        ),
        pytest.param(
            lambda app: (app / "Contents" / "Resources" / "icon.icns").unlink(),
            "icon",
            id="missing-icon",
        ),
        pytest.param(
            lambda app: (app / "Contents" / "Resources" / "icon.icns").write_bytes(b"not an icns"),
            "icon container",
            id="malformed-icns",
        ),
        pytest.param(
            lambda app: (app / "Contents" / "Resources" / "icon.icns").write_bytes(
                _icns_payload()[:-4]
            ),
            "truncated",
            id="truncated-icns",
        ),
        pytest.param(
            lambda app: _drop_plist_key(app, "CFBundleIconFile"),
            "CFBundleIconFile",
            id="plist-without-icon-name",
        ),
        pytest.param(
            lambda app: _set_plist_key(app, "CFBundleIconFile", "missing.icns"),
            "missing icon",
            id="icon-name-not-bundled",
        ),
        # PyInstaller sets LSBackgroundOnly for console EXEs; macOS then presents
        # the app without its icon, which is the bug the spec now overrides.
        pytest.param(
            lambda app: _set_plist_key(app, "LSBackgroundOnly", True),
            "LSBackgroundOnly",
            id="background-only",
        ),
        # Without NSHighResolutionCapable macOS treats the app as low resolution
        # and scales the icon instead of drawing its native representations.
        pytest.param(
            lambda app: _drop_plist_key(app, "NSHighResolutionCapable"),
            "NSHighResolutionCapable",
            id="no-high-resolution-flag",
        ),
    ],
)
def test_validate_app_rejects_a_broken_bundle(tmp_path, break_bundle, message) -> None:
    app = _fake_app(tmp_path)
    break_bundle(app)

    with pytest.raises(SystemExit, match=message):
        verify_macos_bundle.validate_app(app)


def test_validate_app_reports_the_icon_it_presented(tmp_path) -> None:
    assert verify_macos_bundle.validate_app(_fake_app(tmp_path)) == "icon.icns"


def test_verify_rejects_a_background_only_bundle_inside_an_archive(tmp_path) -> None:
    app = _fake_app(tmp_path)
    plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    plist["LSBackgroundOnly"] = True
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(plist))
    archive = make_archive.build_archive(app, tmp_path / "release" / "macos.zip")

    with pytest.raises(SystemExit, match="LSBackgroundOnly"):
        verify_macos_bundle._find_app(archive)


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


def test_spec_presents_the_macos_bundle_as_a_foreground_app() -> None:
    # ``uv run build`` must not leave macOS treating SpotM3U as a background-only
    # process: such an app gets no Dock tile and falls back to the generic
    # placeholder icon in Finder and the Cmd-Tab switcher.
    tree = ast.parse((_PACKAGING / "spotm3u.spec").read_text(encoding="utf-8"))
    bundles = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "BUNDLE"
    ]

    assert len(bundles) == 1
    keywords = {keyword.arg: keyword.value for keyword in bundles[0].keywords}
    info_plist = keywords["info_plist"]
    settings = {
        key.value: value.value
        for key, value in zip(info_plist.keys, info_plist.values, strict=True)
    }
    assert settings["LSBackgroundOnly"] is False
    assert settings["NSHighResolutionCapable"] is True


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
