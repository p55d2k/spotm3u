"""Spotify authentication through the visible browser session."""

import asyncio
import time
from collections.abc import Callable

from playwright.async_api import Page

SPOTIFY_HOME_URL = "https://open.spotify.com/"
DEFAULT_TIMEOUT_SECONDS = 300.0
POLL_INTERVAL_SECONDS = 0.5

# These labels are user-facing navigation that is only available after login.
AUTHENTICATED_LOCATORS = (
    '[aria-label="Your Library"]',
    '[data-testid="user-widget-link"]',
)


class AuthenticationTimeoutError(TimeoutError):
    """Raised when the user does not finish Spotify authentication in time."""


class SpotifyAuthenticator:
    """Open Spotify and wait for the user to authenticate in the browser."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must not be negative")
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock

    async def authenticate(self, page: Page) -> None:
        """Navigate to Spotify and wait until the account appears authenticated."""
        await page.goto(SPOTIFY_HOME_URL, wait_until="domcontentloaded")
        deadline = self._clock() + self._timeout_seconds

        while self._clock() < deadline:
            if await self.is_authenticated(page):
                return
            await asyncio.sleep(self._poll_interval_seconds)

        raise AuthenticationTimeoutError(
            "Spotify login timed out. Sign in using the Spotify browser window "
            f"within {self._timeout_seconds:g} seconds and try again."
        )

    async def is_authenticated(self, page: Page) -> bool:
        """Return whether Spotify shows an authenticated navigation state."""
        if "/login" in page.url:
            return False

        for selector in AUTHENTICATED_LOCATORS:
            if await page.locator(selector).count() > 0:
                return True
        return False
