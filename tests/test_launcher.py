"""Tests for the production launcher entry point."""

import logging
import os
import sys
from pathlib import Path

from spotm3u import launcher


class _FakeApp:
    """Attribute-based stand-in for the Flask app used by ``main``."""

    config = {"PORT": 5001}
    logger = logging.getLogger("test-launcher")

    def __init__(self) -> None:
        self.run_calls: dict[str, object] = {}

    def run(self, **kwargs) -> None:
        self.run_calls.update(kwargs)


def test_main_serves_flask_app_without_debug(monkeypatch) -> None:
    # Don't actually bind a socket.
    fake_app = _FakeApp()

    monkeypatch.setattr(launcher, "create_app", lambda: fake_app)

    launcher.main()

    assert fake_app.run_calls["host"] == "127.0.0.1"
    assert fake_app.run_calls["port"] == 5001
    assert fake_app.run_calls["debug"] is False
    assert fake_app.run_calls["use_reloader"] is False
    assert fake_app.run_calls["threaded"] is True


def test_bundle_root_falls_back_to_executable_directory(monkeypatch) -> None:
    monkeypatch.delenv("_MEIPASS", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert launcher.bundle_roots()[-1] == Path(sys.executable).resolve().parent


def test_bundle_config_points_at_bundled_config_toml(monkeypatch, tmp_path) -> None:
    bundled_config = tmp_path / "config.toml"
    bundled_config.write_text("", encoding="utf-8")
    monkeypatch.setattr(launcher, "is_frozen", lambda: True)
    monkeypatch.setattr(launcher, "bundle_roots", lambda: (tmp_path,))
    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)

    launcher.configure_config_path_for_bundle()

    assert os.environ.get("SPOTM3U_CONFIG") == str(bundled_config)


def test_bundle_config_respects_explicit_config_env(monkeypatch, tmp_path) -> None:
    tomldir = tmp_path / "bin"
    tomldir.mkdir()
    explicit = tomldir / "explicit.toml"
    explicit.write_text("", encoding="utf-8")
    monkeypatch.setattr(launcher, "is_frozen", lambda: True)
    monkeypatch.setattr(launcher, "bundle_roots", lambda: (tmp_path,))
    monkeypatch.setenv("SPOTM3U_CONFIG", str(explicit))

    launcher.configure_config_path_for_bundle()

    assert os.environ["SPOTM3U_CONFIG"] == str(explicit)


def test_bundle_config_prefers_executable_dir_over_internal(monkeypatch, tmp_path) -> None:
    exe_dir = tmp_path / "exe"
    internal = tmp_path / "_internal"
    exe_dir.mkdir()
    internal.mkdir()
    user_config = exe_dir / "config.toml"
    user_config.write_text("", encoding="utf-8")
    (internal / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(launcher, "is_frozen", lambda: True)
    monkeypatch.setattr(launcher, "bundle_roots", lambda: (internal, exe_dir))
    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)

    launcher.configure_config_path_for_bundle()

    assert os.environ["SPOTM3U_CONFIG"] == str(user_config)
