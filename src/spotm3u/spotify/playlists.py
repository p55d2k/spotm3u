"""Playlist scraping and navigation for Spotify's web UI."""

from __future__ import annotations

from typing import Any

from spotm3u.models import Playlist

from .extractor import extract_playlists


class PlaylistScraper:
    """Fetch playlists from an authenticated Spotify browsing session."""

    def __init__(self, page: Any) -> None:
        self.page = page

    async def get_playlists(self) -> list[Playlist]:
        """Return the user's playlists as internal Playlist objects."""
        return await extract_playlists(self.page)

    async def open_playlist(self, playlist: Playlist) -> None:
        """Navigate the browser to a playlist page."""
        if self.page is None:
            raise RuntimeError("A page is required to open a playlist.")
        goto = getattr(self.page, "goto", None)
        if goto is None:
            raise RuntimeError("The supplied page does not support navigation.")
        await goto(playlist.url, wait_until="domcontentloaded")


SpotifyPlaylistScraper = PlaylistScraper


async def scrape_playlists(page: Any) -> list[Playlist]:
    """Convenience entry point for playlist extraction."""
    return await PlaylistScraper(page).get_playlists()


__all__ = ["PlaylistScraper", "SpotifyPlaylistScraper", "scrape_playlists"]
