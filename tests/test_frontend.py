"""Tests for the built React frontend: locating it, building it, and serving it."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from spotm3u import frontend

_INDEX = '<!doctype html><div id="root"></div><script src="/app/assets/app.js"></script>'


def _built(directory: Path, *, assets: dict[str, str] | None = None) -> Path:
    """Write a minimal Vite-style build into ``directory`` and return it."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.html").write_text(_INDEX, encoding="utf-8")
    for name, content in (assets or {"app.js": "console.log(1)"}).items():
        asset = directory / "assets" / name
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text(content, encoding="utf-8")
    return directory


def _app(monkeypatch, dist: Path | None) -> Flask:
    """A bare application in which only the frontend routes are registered.

    The override is always set, so ``None`` means "definitely not built" - even
    in a checkout whose ``frontend/dist`` happens to exist.
    """
    monkeypatch.setenv(frontend.DIST_ENV, str(dist) if dist is not None else "/nonexistent/dist")
    app = Flask(__name__)
    frontend.register_frontend(app)
    return app


def test_dist_directory_prefers_the_override(monkeypatch, tmp_path) -> None:
    built = _built(tmp_path / "elsewhere")
    monkeypatch.setenv(frontend.DIST_ENV, str(built))
    # Even the repository's own build must not win over an explicit path.
    monkeypatch.setattr(frontend, "FRONTEND_DIR", _built(tmp_path / "checkout"))

    assert frontend.dist_directory() == built


def test_dist_directory_rejects_an_override_without_a_build(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(frontend.DIST_ENV, str(tmp_path / "not-built"))
    monkeypatch.setattr(frontend, "FRONTEND_DIR", _built(tmp_path / "checkout"))

    assert frontend.dist_directory() is None


def test_dist_directory_finds_the_bundled_frontend(monkeypatch, tmp_path) -> None:
    bundle = _built(tmp_path / "bundle" / "frontend")
    monkeypatch.delenv(frontend.DIST_ENV, raising=False)
    monkeypatch.setattr(frontend, "bundle_roots", lambda: (bundle.parent,))
    monkeypatch.setattr(frontend, "FRONTEND_DIR", _built(tmp_path / "checkout"))

    assert frontend.dist_directory() == bundle


def test_dist_directory_falls_back_to_the_source_checkout(monkeypatch, tmp_path) -> None:
    checkout = tmp_path / "frontend"
    _built(checkout / frontend.DIST_DIRNAME)
    monkeypatch.delenv(frontend.DIST_ENV, raising=False)
    monkeypatch.setattr(frontend, "bundle_roots", lambda: (tmp_path / "bundle",))
    monkeypatch.setattr(frontend, "FRONTEND_DIR", checkout)

    assert frontend.dist_directory() == checkout / frontend.DIST_DIRNAME


def test_dist_directory_is_none_without_any_build(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(frontend.DIST_ENV, raising=False)
    monkeypatch.setattr(frontend, "bundle_roots", lambda: (tmp_path / "bundle",))
    monkeypatch.setattr(frontend, "FRONTEND_DIR", tmp_path / "frontend")

    assert frontend.dist_directory() is None


def test_dependencies_are_reported_from_node_modules(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(frontend, "FRONTEND_DIR", tmp_path)

    assert frontend.dependencies_installed() is False
    with pytest.raises(frontend.FrontendError, match="npm install"):
        frontend.require_dependencies()

    (tmp_path / "node_modules").mkdir()

    assert frontend.dependencies_installed() is True
    frontend.require_dependencies()


def test_npm_command_is_the_npm_on_path(monkeypatch) -> None:
    monkeypatch.setattr(frontend.shutil, "which", lambda name: "/usr/bin/npm")

    assert frontend.npm_command() == ["/usr/bin/npm"]


def test_npm_command_needs_node(monkeypatch) -> None:
    monkeypatch.setattr(frontend.shutil, "which", lambda name: None)

    with pytest.raises(frontend.FrontendError, match="npm was not found"):
        frontend.npm_command()


def test_npm_command_uses_the_command_interpreter_on_windows(monkeypatch) -> None:
    # ``npm.cmd`` is a batch file, so it cannot be started on its own.
    monkeypatch.setattr(frontend.shutil, "which", lambda name: r"C:\nodejs\npm.cmd")
    monkeypatch.setattr(frontend, "os", SimpleNamespace(name="nt", environ={}))

    assert frontend.npm_command() == ["cmd.exe", "/c", r"C:\nodejs\npm.cmd"]


def test_install_command_respects_the_committed_lockfile(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(frontend, "FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(frontend.shutil, "which", lambda name: "/usr/bin/npm")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    assert frontend.install_command() == ["/usr/bin/npm", "ci"]


def test_install_command_without_a_lockfile_installs_normally(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(frontend, "FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(frontend.shutil, "which", lambda name: "/usr/bin/npm")

    assert frontend.install_command() == ["/usr/bin/npm", "install"]


def test_build_command_runs_the_project_build_script(monkeypatch) -> None:
    monkeypatch.setattr(frontend.shutil, "which", lambda name: "/usr/bin/npm")

    assert frontend.build_command() == ["/usr/bin/npm", "run", "build"]


def test_dev_command_pins_the_host_and_port(monkeypatch) -> None:
    monkeypatch.setattr(frontend.shutil, "which", lambda name: "/usr/bin/npm")

    assert frontend.dev_command(5199) == [
        "/usr/bin/npm",
        "run",
        "dev",
        "--",
        "--host",
        "127.0.0.1",
        "--port",
        "5199",
        "--strictPort",
    ]


def test_index_is_served_at_the_mount(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, _built(tmp_path / "dist")).test_client()

    response = client.get("/app/")

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert 'id="root"' in response.get_data(as_text=True)


def test_hashed_assets_are_served(monkeypatch, tmp_path) -> None:
    dist = _built(tmp_path / "dist", assets={"index-abc123.js": "console.log(1)"})
    client = _app(monkeypatch, dist).test_client()

    response = client.get("/app/assets/index-abc123.js")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "console.log(1)"


def test_client_side_routes_answer_with_the_shell(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, _built(tmp_path / "dist")).test_client()

    # A deep link has no file suffix, so it is a route, not a missing asset.
    response = client.get("/app/playlists/0")

    assert response.status_code == 200
    assert 'id="root"' in response.get_data(as_text=True)


def test_a_missing_asset_is_a_real_404(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, _built(tmp_path / "dist")).test_client()

    response = client.get("/app/assets/vanished.js")

    assert response.status_code == 404
    assert "root" not in response.get_data(as_text=True)


def test_files_outside_the_build_are_not_served(monkeypatch, tmp_path) -> None:
    dist = _built(tmp_path / "dist")
    (tmp_path / "secret.txt").write_text("do not serve", encoding="utf-8")
    client = _app(monkeypatch, dist).test_client()

    response = client.get("/app/../secret.txt")

    assert response.status_code == 404
    assert "do not serve" not in response.get_data(as_text=True)


def test_an_unbuilt_frontend_explains_how_to_build_it(monkeypatch, tmp_path) -> None:
    client = _app(monkeypatch, None).test_client()

    response = client.get("/app/")

    assert response.status_code == 404
    assert "uv run build" in response.get_data(as_text=True)
