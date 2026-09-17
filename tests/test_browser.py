"""Tests for the Playwright browser manager."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from spotm3u.spotify.browser import BrowserManager


def test_start_creates_headed_chromium_context_and_page() -> None:
    async def exercise() -> None:
        page = object()
        context = SimpleNamespace(
            new_page=AsyncMock(return_value=page),
            close=AsyncMock(),
        )
        browser = SimpleNamespace(
            new_context=AsyncMock(return_value=context),
            close=AsyncMock(),
        )
        playwright = SimpleNamespace(
            chromium=SimpleNamespace(
                launch=AsyncMock(return_value=browser),
            ),
            stop=AsyncMock(),
        )
        playwright_manager = SimpleNamespace(start=AsyncMock(return_value=playwright))

        with patch(
            "spotm3u.spotify.browser.async_playwright",
            return_value=playwright_manager,
        ):
            manager = BrowserManager()
            await manager.start()
            result = await manager.new_page()

        assert result is page
        playwright.chromium.launch.assert_awaited_once_with(headless=False)
        browser.new_context.assert_awaited_once_with()
        context.new_page.assert_awaited_once_with()

        await manager.close()
        context.close.assert_awaited_once_with()
        browser.close.assert_awaited_once_with()
        playwright.stop.assert_awaited_once_with()

    asyncio.run(exercise())


def test_new_page_requires_start() -> None:
    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="start"):
            await BrowserManager().new_page()

    asyncio.run(exercise())


def test_close_is_idempotent() -> None:
    async def exercise() -> None:
        manager = BrowserManager()

        await manager.close()
        await manager.close()

    asyncio.run(exercise())
