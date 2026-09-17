"""Tests for Spotify playlist scraping from the browser DOM."""

import asyncio
from unittest.mock import AsyncMock

from spotm3u.models import Playlist
from spotm3u.spotify.extractor import extract_playlists
from spotm3u.spotify.playlists import PlaylistScraper


def test_extract_playlists_from_dom_returns_playlist_objects() -> None:
    async def exercise() -> None:
        page = type(
            "Page",
            (),
            {
                "evaluate": AsyncMock(
                    return_value=[
                        {
                            "href": "/playlist/37i9dQZF1DXdPec7aLT2rD",
                            "name": "Running",
                            "text": "Running 87 tracks",
                            "meta": "87 tracks",
                        },
                        {
                            "href": "https://open.spotify.com/playlist/37i9dQZF1DXcF7gqQq6Q0yY",
                            "name": "Gym",
                            "text": "Gym 142 tracks",
                            "meta": "142 tracks",
                        },
                    ]
                )
            },
        )()

        playlists = await extract_playlists(page)

        assert playlists == [
            Playlist(
                id="37i9dQZF1DXdPec7aLT2rD",
                name="Running",
                url="https://open.spotify.com/playlist/37i9dQZF1DXdPec7aLT2rD",
                track_count=87,
            ),
            Playlist(
                id="37i9dQZF1DXcF7gqQq6Q0yY",
                name="Gym",
                url="https://open.spotify.com/playlist/37i9dQZF1DXcF7gqQq6Q0yY",
                track_count=142,
            ),
        ]

    asyncio.run(exercise())


def test_playlist_scraper_open_playlist_navigates_to_url() -> None:
    async def exercise() -> None:
        page = type("Page", (), {"goto": AsyncMock()})()
        scraper = PlaylistScraper(page)

        await scraper.open_playlist(
            Playlist(
                id="abc123",
                name="Late Night",
                url="https://open.spotify.com/playlist/abc123",
                track_count=63,
            )
        )

        page.goto.assert_awaited_once_with(
            "https://open.spotify.com/playlist/abc123",
            wait_until="domcontentloaded",
        )

    asyncio.run(exercise())
