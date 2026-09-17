"""Playwright browser lifecycle management for Spotify automation."""

from pathlib import Path

from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

DEFAULT_USER_DATA_DIR = Path(".browser-data")


class BrowserManager:
    """Own a persistent Chromium context and its pages."""

    def __init__(
        self,
        *,
        headless: bool = False,
        user_data_dir: str | Path = DEFAULT_USER_DATA_DIR,
    ) -> None:
        self._headless = headless
        self._user_data_dir = Path(user_data_dir)
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    async def start(self) -> None:
        """Start Playwright with a persistent local browser profile."""
        if self._context is not None:
            return

        playwright = await async_playwright().start()
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(self._user_data_dir),
                headless=self._headless,
            )
        except Exception:
            await playwright.stop()
            raise

        self._playwright = playwright
        self._context = context

    async def new_page(self) -> Page:
        """Create a page in the manager's browser context."""
        if self._context is None:
            raise RuntimeError("BrowserManager.start() must be called before new_page()")
        return await self._context.new_page()

    async def switch_to_headless(self) -> Page:
        """Switch from the visible login browser to the same persistent background session."""
        await self.close()
        self._headless = True
        await self.start()
        return await self.new_page()

    async def close(self) -> None:
        """Close the persistent context and Playwright process cleanly."""
        context, playwright = (
            self._context,
            self._playwright,
        )
        self._context = None
        self._playwright = None

        try:
            if context is not None:
                await context.close()
        finally:
            if playwright is not None:
                await playwright.stop()
