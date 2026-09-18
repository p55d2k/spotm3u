"""Smoke tests for the CLI."""

from typer.testing import CliRunner

from spotm3u.cli import app


def test_help_is_available() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Create local M3U playlists" in result.stdout


def test_startup_message() -> None:
    result = CliRunner().invoke(app)

    assert result.exit_code == 0
    assert "Exportify" in result.stdout
    assert "Local M3U" in result.stdout
    assert "Reusable playlist" in result.stdout