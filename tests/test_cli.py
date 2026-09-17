"""Smoke tests for the initial CLI scaffold."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from spotm3u.cli import app


def test_help_is_available() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Create local M3U playlists" in result.stdout


def test_startup_message(monkeypatch) -> None:
    async def authenticate() -> None:
        return None

    monkeypatch.setattr("spotm3u.cli._authenticate", authenticate)
    result = CliRunner().invoke(app)

    assert result.exit_code == 0
    assert "Spotify" in result.stdout
    assert "Local M3U" in result.stdout


def test_existing_session_skips_headed_browser() -> None:
    async def exercise() -> None:
        headless_page = SimpleNamespace(
            goto=AsyncMock(),
            context=SimpleNamespace(
                cookies=AsyncMock(
                    return_value=[{"name": "sp_dc", "value": "authenticated-session"}]
                )
            ),
        )

        with patch("spotm3u.cli.BrowserManager") as manager_type:
            manager = manager_type.return_value
            manager.start = AsyncMock()
            manager.new_page = AsyncMock(return_value=headless_page)
            manager.close = AsyncMock()
            manager_type.return_value._headless = True

            await _authenticate()

        manager_type.assert_called_once_with(headless=True)
        manager.start.assert_awaited_once_with()
        manager.close.assert_awaited_once_with()

    from spotm3u.cli import _authenticate

    asyncio.run(exercise())
