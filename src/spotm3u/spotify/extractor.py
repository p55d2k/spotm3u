"""DOM extraction helpers for Spotify playlist metadata."""

from __future__ import annotations

import re
from typing import Any

from spotm3u.models import Playlist

PLAYLIST_LINK_SELECTOR = "a[href*='/playlist/'], a[href*='spotify:playlist:']"
PLAYLIST_CONTAINER_SELECTOR = (
    "[data-testid*='playlist'], "
    "[aria-label*='playlist'], "
    "article, li, div"
)


def normalize_text(value: str | None) -> str:
    """Collapse whitespace to a single readable string."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def parse_playlist_id(url: str) -> str | None:
    """Extract a Spotify playlist id from a playlist URL."""
    match = re.search(r"(?:/playlist/|spotify:playlist:)([A-Za-z0-9]+)", url)
    if match:
        return match.group(1)
    return None


def parse_track_count(value: str | None) -> int | None:
    """Best-effort parse of a rendered track count from playlist text."""
    if not value:
        return None
    match = re.search(r"(\d+)\s*(tracks?|songs?)", value, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def coerce_playlist_url(url: str | None) -> str:
    """Normalize playlist URLs to the canonical open.spotify.com form."""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("spotify:playlist:"):
        return "https://open.spotify.com/playlist/" + url.split(":playlist:", 1)[1]
    if url.startswith("//"):
        return "https:" + url
    if not url.startswith("http"):
        return "https://open.spotify.com" + url if url.startswith("/") else "https://open.spotify.com/" + url
    return url


def build_playlist(record: dict[str, Any]) -> Playlist | None:
    """Convert raw DOM metadata into a Playlist model."""
    href = str(record.get("href") or "").strip()
    name = normalize_text(str(record.get("name") or "") or str(record.get("text") or ""))
    if not href or not name:
        return None
    if "/playlist/" not in href and "spotify:playlist:" not in href:
        return None
    url = coerce_playlist_url(href)
    return Playlist(
        id=parse_playlist_id(url),
        name=name,
        url=url,
        track_count=parse_track_count(str(record.get("meta") or record.get("text") or "")),
    )


async def _evaluate_playlists(page: Any) -> list[dict[str, Any]]:
    """Try to read playlist metadata directly from the DOM for a real browser page."""
    if not hasattr(page, "evaluate"):
        return []

    try:
        return await page.evaluate(
            """
            () => {
              const seen = new Set();
              const rows = [];
              const nodes = [...document.querySelectorAll('a[href*="/playlist/"], a[href*="spotify:playlist:"]')];
              for (const node of nodes) {
                const href = node.getAttribute('href') || '';
                if (!href || (!href.includes('/playlist/') && !href.includes('spotify:playlist:'))) continue;
                const label = (node.getAttribute('aria-label') || node.textContent || '').replace(/\s+/g, ' ').trim();
                const container = node.closest('[data-testid], article, li, div') || node.parentElement || document.body;
                const containerText = (container.textContent || '').replace(/\s+/g, ' ').trim();
                const key = `${href}|${label}`;
                if (!label || seen.has(key)) continue;
                seen.add(key);
                const match = /\b(\d+)\s*(tracks?|songs?)\b/i.exec(containerText || '');
                rows.push({
                  href,
                  name: label,
                  text: containerText,
                  meta: match ? `${match[1]} ${match[2]}` : '',
                });
              }
              return rows;
            }
            """
        )
    except Exception:
        return []


async def _locator_playlists(page: Any) -> list[dict[str, Any]]:
    """Fallback to Playwright locators when evaluate() is unavailable or does not work."""
    candidates = [
        PLAYLIST_LINK_SELECTOR,
        "a[href*='/playlist/']",
        "[href*='/playlist/']",
        "[data-testid*='playlist'] a",
        "[aria-label*='playlist']",
    ]
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []

    for selector in candidates:
        locator = getattr(page, "locator", None)
        if locator is None:
            continue
        try:
            item = page.locator(selector)
            count = await item.count()
        except Exception:
            continue
        for index in range(count):
            try:
                node = item.nth(index)
                href = await node.get_attribute("href")
                if not href:
                    continue
                text = normalize_text(await node.inner_text())
                if not text:
                    continue
                if "/playlist/" not in href and "spotify:playlist:" not in href:
                    continue
                key = f"{href}|{text}"
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "href": href,
                    "name": text,
                    "text": text,
                    "meta": text,
                })
            except Exception:
                continue
    return rows


async def extract_playlists(page: Any) -> list[Playlist]:
    """Read playlist cards from an authenticated Spotify page and coerce them to Playlist models."""
    records: list[dict[str, Any]] = []
    records.extend(await _evaluate_playlists(page))
    if not records:
        records.extend(await _locator_playlists(page))

    playlists: list[Playlist] = []
    seen_urls: set[str] = set()
    for record in records:
        playlist = build_playlist(record)
        if playlist is None:
            continue
        if playlist.url in seen_urls:
            continue
        seen_urls.add(playlist.url)
        playlists.append(playlist)
    return playlists


class PlaylistExtractor:
    """Extract Spotify playlists from a page or DOM-like object."""

    async def extract(self, page: Any) -> list[Playlist]:
        return await extract_playlists(page)


SpotifyPlaylistExtractor = PlaylistExtractor


__all__ = [
    "PLAYLIST_CONTAINER_SELECTOR",
    "PLAYLIST_LINK_SELECTOR",
    "PlaylistExtractor",
    "SpotifyPlaylistExtractor",
    "build_playlist",
    "coerce_playlist_url",
    "extract_playlists",
    "normalize_text",
    "parse_playlist_id",
    "parse_track_count",
]
