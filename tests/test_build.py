"""Tests for the ``uv run build`` shortcut entry point."""

from pathlib import Path

import pytest

from spotm3u import build


def test_build_command_targets_pyinstaller_and_the_spec() -> None:
    command = build.build_command()

    assert command[0].endswith("uv")
    assert command[1:4] == ["run", "--group", "build"]
    assert command[-4:] == ["pyinstaller", "--noconfirm", "--clean", str(build._SPEC)]


def test_build_command_forwards_extra_arguments() -> None:
    command = build.build_command(["--distpath", "/tmp/out"])

    assert command[-2:] == ["--distpath", "/tmp/out"]


def test_build_command_requires_uv(monkeypatch) -> None:
    monkeypatch.setattr(build.shutil, "which", lambda _name: None)

    with pytest.raises(SystemExit) as exit_info:
        build.build_command()

    assert exit_info.value.code == 1


def test_report_success_points_at_existing_artifacts(tmp_path, capsys) -> None:
    produced = [tmp_path / "dist" / "SpotM3U"]
    produced[0].mkdir(parents=True)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(build, "artifact_paths", lambda: produced)
    try:
        build.report_success()
    finally:
        monkeypatch.undo()

    out = capsys.readouterr().out
    assert "Build complete." in out
    assert "dist/SpotM3U" in out


def test_report_success_mentions_macos_app_bundle(tmp_path, capsys) -> None:
    produced = [tmp_path / "dist" / "SpotM3U", tmp_path / "dist" / "SpotM3U.app"]
    for path in produced:
        path.mkdir(parents=True)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(build, "artifact_paths", lambda: produced)
    try:
        build.report_success()
    finally:
        monkeypatch.undo()

    assert "dist/SpotM3U.app" in capsys.readouterr().out


def _install_fake_build(monkeypatch, tmp_path: Path, result: int) -> dict[str, object]:
    """Stub subprocess/call so ``build.main`` never shells out for real."""
    calls: dict[str, object] = {}
    calls["commands"] = []

    def fake_call(command, *, cwd=None, env=None) -> int:
        calls["commands"].append(command)
        calls["command"] = command
        calls["cwd"] = cwd
        calls["ffmpeg"] = env.get("SPOTM3U_FFMPEG_DIR") if env else None
        if command[1].endswith("generate_icons.py"):
            return 0
        return result

    monkeypatch.setattr(build.subprocess, "call", fake_call)
    monkeypatch.setattr(build, "_FFMPEG_STAGE", tmp_path / "ffmpeg-stage")
    monkeypatch.setattr(build, "_ICON_GENERATOR", tmp_path / "packaging" / "generate_icons.py")
    monkeypatch.setattr(build, "_ICON_PNG", tmp_path / "assets" / "icon.png")
    monkeypatch.setattr(build, "_ROOT", tmp_path)
    (tmp_path / "assets").mkdir(parents=True)
    (tmp_path / "assets" / "icon.png").write_bytes(b"png")
    return calls


def test_main_builds_with_the_ffmpeg_stage_when_present(monkeypatch, tmp_path, capsys) -> None:
    (tmp_path / "ffmpeg-stage").mkdir()
    calls = _install_fake_build(monkeypatch, tmp_path, result=0)

    build.main()

    assert Path(calls["cwd"]).resolve() == tmp_path.resolve()
    assert calls["ffmpeg"] == str(tmp_path / "ffmpeg-stage")
    assert calls["command"][-4:] == ["pyinstaller", "--noconfirm", "--clean", str(build._SPEC)]
    assert "Build complete." in capsys.readouterr().out


def test_main_skips_the_ffmpeg_env_without_a_stage_dir(monkeypatch, tmp_path, capsys) -> None:
    calls = _install_fake_build(monkeypatch, tmp_path, result=0)

    build.main()

    assert calls["ffmpeg"] is None
    assert "Build complete." in capsys.readouterr().out


def test_main_generates_icons_before_pyinstaller(monkeypatch, tmp_path, capsys) -> None:
    calls = _install_fake_build(monkeypatch, tmp_path, result=0)

    build.main()

    commands = calls["commands"]
    assert len(commands) == 2
    assert Path(commands[0][1]).name == "generate_icons.py"
    assert Path(commands[1][-1]).name == "spotm3u.spec"


def test_main_fails_when_the_icon_source_is_missing(monkeypatch, tmp_path, capsys) -> None:
    calls = _install_fake_build(monkeypatch, tmp_path, result=0)
    (tmp_path / "assets" / "icon.png").unlink()

    with pytest.raises(SystemExit, match="application icon"):
        build.main()

    assert calls["commands"] == []


def test_main_fails_when_icon_generation_fails(monkeypatch, tmp_path, capsys) -> None:
    _install_fake_build(monkeypatch, tmp_path, result=0)
    monkeypatch.setattr(
        build,
        "subprocess",
        type("Subprocess", (), {"call": staticmethod(lambda command, **_: 1)})(),
    )

    with pytest.raises(SystemExit) as exit_info:
        build.main()

    assert exit_info.value.code == 1
    assert "Icon generation failed" in capsys.readouterr().err


def test_main_fails_loudly_when_the_build_fails(monkeypatch, tmp_path, capsys) -> None:
    calls = _install_fake_build(monkeypatch, tmp_path, result=2)

    with pytest.raises(SystemExit) as exit_info:
        build.main()

    assert exit_info.value.code == 2
    assert calls["ffmpeg"] is None
    assert "Build failed" in capsys.readouterr().err
