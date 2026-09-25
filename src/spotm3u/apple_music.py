"""Optional, experimental Apple Music catalog matching (task 93).

Apple Music does not use an imported MP3's embedded ``SYLT`` data to render
synchronized lyrics. Community experimentation suggests that writing Apple's
catalog track identifier into the local file (the ``ITUNESCATALOGID`` field)
*may* let Music associate the import with its catalog track, after which
Apple's own server-side synced lyrics become available.

That behaviour is **undocumented and unverified**: this module therefore only
*identifies* the catalog track with strict, evidence-based matching, and the
feature stays **disabled by default** (``[apple_music] catalog_id`` in
``config.toml``). SpotM3U never scrapes Apple's lyric service, injects Apple
lyrics, or depends on private APIs, and a missing/failed match never affects a
download: it simply leaves the file without a catalog id.

Matching deliberately refuses to associate a local track with a catalog id on a
title + artist coincidence alone. A candidate must agree on the normalized
title, the artist, the version (a plain request is not matched to a remix/live
take), and — when the track carries them — the album and the duration.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import requests

from .artwork_sources import _normalize_album_for_search, _normalize_identity
from .metadata_cache import MetadataCache
from .models import Track
from .rate_limits import provider_request

logger = logging.getLogger(__name__)

_ITUNES_SEARCH = "https://itunes.apple.com/search"
_REQUEST_TIMEOUT = 15
_USER_AGENT = "spotm3u/0.1 (https://github.com/zk/spotm3u)"
_RESULT_LIMIT = 25

# Version markers that make one recording a *different* recording. A candidate
# carrying one the request does not (or vice versa) is not the requested track.
# ``remastered`` is deliberately absent: a remaster is the same performance and
# its naming varies wildly between catalogs.
_VERSION_KEYS = (
    "live",
    "acoustic",
    "unplugged",
    "remix",
    "instrumental",
    "karaoke",
    "cover",
    "demo",
    "reprise",
    "sped up",
    "slowed",
    "nightcore",
    "radio edit",
    "extended",
    "deluxe",
)
_VERSION_PATTERNS = tuple((key, re.compile(rf"\b{re.escape(key)}\b")) for key in _VERSION_KEYS)

# Metadata noise that is not a different recording and varies between catalogs:
# a remaster (and its year), upload-type labels. Stripped from both sides before
# comparing titles so ``Song`` and ``Song (Remastered 2011)`` are the same title.
_NEUTRAL_PATTERNS = (
    re.compile(r"\bremaster(?:ed)?(?:\s+\d{4})?\b"),
    re.compile(r"\bofficial\b"),
    re.compile(r"\baudio\b"),
    re.compile(r"\bvideo\b"),
    re.compile(r"\blyrics?\b"),
    re.compile(r"\bexplicit\b"),
    re.compile(r"\b(?:hd|hq)\b"),
)

# When True the catalog lookup runs during metadata enrichment and the resolved
# id is written as ``TXXX:ITUNESCATALOGID``. Off by default: the mechanism is
# experimental (see the module docstring). Configured through
# ``apple_music.catalog_id`` in config.toml.
_APPLE_CATALOG_ID_ENABLED = False

_CATALOG_CACHE = MetadataCache()


def set_apple_catalog_id_enabled(enabled: bool) -> None:
    """Enable or disable experimental Apple Music catalog-id lookup."""
    global _APPLE_CATALOG_ID_ENABLED
    _APPLE_CATALOG_ID_ENABLED = enabled


def apple_catalog_id_enabled() -> bool:
    """Return whether the experimental catalog-id lookup is enabled."""
    return _APPLE_CATALOG_ID_ENABLED


@dataclass(frozen=True)
class CatalogTrack:
    """One Apple Music catalog track confidently matched to a local track."""

    catalog_id: int
    title: str
    artist: str
    album: str | None = None
    duration_ms: int | None = None


def _title_key(value: str) -> tuple[str, frozenset[str]]:
    """Return ``(base title, version markers)`` for a track title.

    The base title is the normalized title up to the first version marker, so
    catalogs that spell the take out (``Song (Live at Wembley)``) still line up
    with a shorter local name (``Song (Live)``); the two must also agree on the
    marker *set*, so a plain request never matches a live/remix counterpart.
    When a marker opens the title (``Live to Tell``) there is no base left to
    compare, so the whole cleaned title is used instead — a weak comparison is
    worse than no match.
    """
    text = _normalize_identity(value)
    for pattern in _NEUTRAL_PATTERNS:
        text = pattern.sub(" ", text)
    markers = frozenset(key for key, pattern in _VERSION_PATTERNS if pattern.search(text))
    earliest = min(
        (match.start() for _key, pattern in _VERSION_PATTERNS if (match := pattern.search(text))),
        default=len(text),
    )
    base = " ".join(text[:earliest].split()) or " ".join(text.split())
    return base, markers


def _album_key(value: str | None) -> str:
    return _normalize_identity(_normalize_album_for_search(value)) if value else ""


def _duration_matches(requested_ms: int | None, candidate_ms: int | None) -> bool:
    """Whether two durations plausibly describe the same recording.

    Missing values are not disqualifying; a present pair must agree within a
    5-second floor or 3%, whichever is larger (catalog durations round slightly
    differently from the exported export).
    """
    if not requested_ms or not candidate_ms:
        return True
    tolerance = max(5_000, int(requested_ms * 0.03))
    return abs(candidate_ms - requested_ms) <= tolerance


def _requested_artist(track: Track) -> str:
    return track.album_artist or (track.artists[0] if track.artists else "")


def _candidate_qualifies(track: Track, result: dict[str, Any]) -> bool:
    """Whether one iTunes result can be the requested track, on strong evidence."""
    candidate_title = str(result.get("trackName") or "")
    candidate_artist = str(result.get("artistName") or "")
    if not candidate_title or not candidate_artist:
        return False
    if _title_key(candidate_title) != _title_key(track.title):
        return False
    if _normalize_identity(candidate_artist) != _normalize_identity(_requested_artist(track)):
        return False
    duration = result.get("trackTimeMillis")
    candidate_ms = int(duration) if isinstance(duration, (int, float)) else None
    return _duration_matches(track.duration_ms, candidate_ms)


def _select_catalog_track(track: Track, results: list[dict[str, Any]]) -> CatalogTrack | None:
    """Pick the single qualifying result, or ``None`` when the choice is unsafe.

    The album is treated as confirmation rather than decoration: when the local
    track carries an album, only a candidate from that album is accepted. A tie
    between two otherwise-identical recordings is not guessed at.
    """
    qualifying: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for result in results:
        if not isinstance(result, dict) or not _candidate_qualifies(track, result):
            continue
        raw_id = result.get("trackId")
        if not isinstance(raw_id, int) or raw_id in seen_ids:
            continue
        seen_ids.add(raw_id)
        qualifying.append(result)

    if not qualifying:
        return None

    album_key = _album_key(track.album)
    if album_key:
        qualifying = [
            result for result in qualifying if _album_key(result.get("collectionName")) == album_key
        ]
        if not qualifying:
            return None

    if len(qualifying) != 1:
        logger.debug(
            "Apple catalog match ambiguous title=%r candidates=%s",
            track.title,
            ",".join(str(result.get("trackId")) for result in qualifying),
        )
        return None

    result = qualifying[0]
    duration = result.get("trackTimeMillis")
    return CatalogTrack(
        catalog_id=int(result["trackId"]),
        title=str(result.get("trackName") or ""),
        artist=str(result.get("artistName") or ""),
        album=str(result.get("collectionName") or "") or None,
        duration_ms=int(duration) if isinstance(duration, (int, float)) else None,
    )


def _search(term: str) -> list[dict[str, Any]]:
    """Run one iTunes search, returning an empty list on any failure."""

    def request():
        return requests.get(
            _ITUNES_SEARCH,
            params={"term": term, "entity": "song", "limit": _RESULT_LIMIT, "media": "music"},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )

    try:
        if getattr(requests.get, "__module__", "requests.api") == "requests.api":
            response = provider_request("itunes", request)
        else:
            response = request()
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, OSError, ValueError, AttributeError) as exc:
        logger.debug("Apple catalog lookup failed term=%s: %s", term, exc)
        return []
    results = payload.get("results") if isinstance(payload, dict) else None
    return (
        [result for result in results if isinstance(result, dict)]
        if isinstance(results, list)
        else []
    )


def find_catalog_track(track: Track) -> CatalogTrack | None:
    """Return the confidently-matched Apple Music catalog track, or ``None``.

    Returns ``None`` when the lookup is disabled, when the track lacks a usable
    identity, when no result qualifies, and on any provider/network failure.
    Nothing raises: the caller only writes a catalog id when one is certain.
    """
    if not _APPLE_CATALOG_ID_ENABLED:
        return None
    title = (track.title or "").strip()
    artist = _requested_artist(track).strip()
    if not title or not artist:
        return None
    term = f"{artist} {title}"

    key = track.spotify_id or f"catalog:{term.casefold()}|{_album_key(track.album)}"

    def fetch() -> CatalogTrack | None:
        return _select_catalog_track(track, _search(term))

    # Never cache a miss as a permanent negative entry; only a positive match is
    # retained (MetadataCache drops None results).
    return _CATALOG_CACHE.get_or_fetch(key, fetch)


__all__ = [
    "CatalogTrack",
    "apple_catalog_id_enabled",
    "find_catalog_track",
    "set_apple_catalog_id_enabled",
]
