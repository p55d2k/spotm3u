"""Compatibility shim for the Spotify playlist scraper module."""

from .playlists import PlaylistScraper, SpotifyPlaylistScraper, scrape_playlists

__all__ = ["PlaylistScraper", "SpotifyPlaylistScraper", "scrape_playlists"]
