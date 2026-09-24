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


# Sentinels standing in for the frontend commands, so these tests are about
# which steps run in which order (test_frontend.py covers the commands).
_NPM_INSTALL = ["npm", "ci"]
_NPM_BUILD = ["npm", "run", "build"]


def _install_fake_build(
    monkeypatch,
    tmp_path: Path,
    result: int,
    *,
    install_result: int = 0,
    frontend_result: int = 0,
    stub_frontend: bool = True,
) -> dict[str, object]:
    """Stub the icon generator, the frontend build, and PyInstaller.

    ``result`` is what PyInstaller returns, ``install_result`` and
    ``frontend_result`` what the two frontend steps return, so a failure in any
    one of them can be asserted on its own.
    """
    calls: dict[str, object] = {}
    calls["commands"] = []
    calls["frontend_cwds"] = []

    def fake_call(command, *, cwd=None, env=None) -> int:
        calls["commands"].append(command)
        if command is _NPM_INSTALL:
            calls["frontend_cwds"].append(cwd)
            return install_result
        if command is _NPM_BUILD:
            calls["frontend_cwds"].append(cwd)
            return frontend_result
        calls["command"] = command
        calls["cwd"] = cwd
        calls["ffmpeg"] = env.get("SPOTM3U_FFMPEG_DIR") if env else None
        if command[1].endswith("generate_icons.py"):
            return 0
        return result

    monkeypatch.setattr(build.subprocess, "call", fake_call)
    if stub_frontend:
        monkeypatch.setattr(build.frontend, "install_command", lambda: _NPM_INSTALL)
        monkeypatch.setattr(build.frontend, "build_command", lambda: _NPM_BUILD)
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


def test_main_builds_the_frontend_between_icons_and_pyinstaller(
    monkeypatch, tmp_path, capsys
) -> None:
    calls = _install_fake_build(monkeypatch, tmp_path, result=0)

    build.main()

    commands = calls["commands"]
    assert len(commands) == 4
    assert Path(commands[0][1]).name == "generate_icons.py"
    assert commands[1] is _NPM_INSTALL
    assert commands[2] is _NPM_BUILD
    # PyInstaller runs last so it packages the fresh frontend build.
    assert Path(commands[3][-1]).name == "spotm3u.spec"
    assert "Build complete." in capsys.readouterr().out


def test_main_runs_the_frontend_steps_in_the_frontend_directory(monkeypatch, tmp_path) -> None:
    calls = _install_fake_build(monkeypatch, tmp_path, result=0)

    build.main()

    assert calls["frontend_cwds"] == [
        str(build.frontend.FRONTEND_DIR),
        str(build.frontend.FRONTEND_DIR),
    ]
    assert Path(calls["frontend_cwds"][0]).name == "frontend"


def test_main_can_package_an_existing_frontend_build(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv(build.SKIP_FRONTEND_ENV, "1")
    calls = _install_fake_build(monkeypatch, tmp_path, result=0)

    build.main()

    assert _NPM_INSTALL not in calls["commands"]
    assert _NPM_BUILD not in calls["commands"]
    assert Path(calls["commands"][-1][-1]).name == "spotm3u.spec"
    assert "Keeping the existing frontend build" in capsys.readouterr().out


def test_main_fails_with_a_hint_when_npm_is_missing(monkeypatch, tmp_path, capsys) -> None:
    calls = _install_fake_build(monkeypatch, tmp_path, result=0, stub_frontend=False)
    monkeypatch.setattr(build.frontend.shutil, "which", lambda name: None)

    with pytest.raises(SystemExit) as exit_info:
        build.main()

    assert exit_info.value.code == 1
    assert "npm was not found" in capsys.readouterr().err
    # Icons were regenerated, and nothing was packaged from a half-built tree.
    assert len(calls["commands"]) == 1
    assert Path(calls["commands"][0][1]).name == "generate_icons.py"


def test_main_fails_loudly_when_the_dependency_install_fails(monkeypatch, tmp_path, capsys) -> None:
    calls = _install_fake_build(monkeypatch, tmp_path, result=0, install_result=7)

    with pytest.raises(SystemExit) as exit_info:
        build.main()

    assert exit_info.value.code == 7
    assert "Frontend dependency install failed" in capsys.readouterr().err
    # PyInstaller never ran, so no half-built bundle is reported as complete.
    assert Path(calls["commands"][-1][-1]).name != "spotm3u.spec"


def test_main_fails_loudly_when_the_frontend_build_fails(monkeypatch, tmp_path, capsys) -> None:
    _install_fake_build(monkeypatch, tmp_path, result=0, frontend_result=9)

    with pytest.raises(SystemExit) as exit_info:
        build.main()

    assert exit_info.value.code == 9
    assert "Frontend build failed" in capsys.readouterr().err


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
