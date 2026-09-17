"""Tests for Spotify's browser-based authentication flow."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from spotm3u.spotify.authentication import (
    AuthenticationTimeoutError,
    SpotifyAuthenticator,
)


def test_authenticate_opens_spotify_and_waits_for_authenticated_locator() -> None:
    async def exercise() -> None:
        locator = SimpleNamespace(count=AsyncMock(side_effect=[0, 1]))
        page = SimpleNamespace(
            url="https://open.spotify.com/",
            goto=AsyncMock(),
            locator=lambda selector: locator,
        )

        await SpotifyAuthenticator(
            timeout_seconds=1,
            poll_interval_seconds=0,
        ).authenticate(page)

        page.goto.assert_awaited_once_with(
            "https://open.spotify.com/",
            wait_until="domcontentloaded",
        )
        assert locator.count.await_count == 2

    asyncio.run(exercise())


def test_login_url_is_not_authenticated() -> None:
    async def exercise() -> None:
        page = SimpleNamespace(url="https://accounts.spotify.com/login")
        assert not await SpotifyAuthenticator().is_authenticated(page)

    asyncio.run(exercise())


def test_authenticate_times_out_with_actionable_error() -> None:
    async def exercise() -> None:
        locator = SimpleNamespace(count=AsyncMock(return_value=0))
        page = SimpleNamespace(
            url="https://open.spotify.com/",
            goto=AsyncMock(),
            locator=lambda selector: locator,
        )

        with pytest.raises(AuthenticationTimeoutError, match="browser window"):
            await SpotifyAuthenticator(
                timeout_seconds=0.001,
                poll_interval_seconds=0,
            ).authenticate(page)

    asyncio.run(exercise())
