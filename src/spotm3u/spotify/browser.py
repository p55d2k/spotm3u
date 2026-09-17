"""Playwright browser lifecycle management for Spotify automation."""

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)


class BrowserManager:
    """Own a headed Chromium browser, context, and its pages."""

    def __init__(self, *, headless: bool = False) -> None:
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def start(self) -> None:
        """Start Playwright and create the browser's default context."""
        if self._context is not None:
            return

        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch(headless=self._headless)
            context = await browser.new_context()
        except Exception:
            await playwright.stop()
            raise

        self._playwright = playwright
        self._browser = browser
        self._context = context

    async def new_page(self) -> Page:
        """Create a page in the manager's browser context."""
        if self._context is None:
            raise RuntimeError("BrowserManager.start() must be called before new_page()")
        return await self._context.new_page()

    async def close(self) -> None:
        """Close the context, browser, and Playwright process cleanly."""
        context, browser, playwright = (
            self._context,
            self._browser,
            self._playwright,
        )
        self._context = None
        self._browser = None
        self._playwright = None

        try:
            if context is not None:
                await context.close()
        finally:
            try:
                if browser is not None:
                    await browser.close()
            finally:
                if playwright is not None:
                    await playwright.stop()
