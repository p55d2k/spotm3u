"""Search for online source candidates for a Spotify/Exportify track.

This layer is intentionally separate from the downloader and from local audio
matching. It only produces candidate metadata and never downloads audio.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
import math
import re

from ..models import Track
from ..normalization import normalize

_SEARCH_EXCLUSION_TOKENS = (
    "live",
    "lyrics",
    "interview",
    "reaction",
    "cover",
    "remix",
    "mashup",
    "trailer",
    "teaser",
    "movie",
    "scene",
    "soundtrack",
    "karaoke",
    "speed",
    "slowed",
    "sped",
    "nightcore",
    "fan edit",
)


@dataclass(frozen=True)
class SourceCandidate:
    """A single online source candidate for a track."""

    url: str
    title: str
    uploader: str | None = None
    artist: str | None = None
    duration_s: float | None = None
    source_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def platform(self) -> str | None:
        if self.source_type:
            return self.source_type
        source = (self.metadata.get("extractor") or self.metadata.get("source_platform") or "").strip()
        return source or None


class OnlineSourceSearcher:
    """Build search queries and collect candidate metadata for a track."""

    def __init__(self, *, max_results: int = 8) -> None:
        self.max_results = max_results

    def search(self, track: Track) -> tuple[SourceCandidate, ...]:
        """Search for likely standalone-recording candidates.

        When yt-dlp is available, this uses metadata-driven search without
        downloading the audio. If the search backend is unavailable or a search
        fails, this returns an empty tuple instead of raising.
        """
        queries = build_search_queries(track)
        if not queries:
            return ()

        try:
            import yt_dlp  # type: ignore
        except ImportError:  # pragma: no cover - optional dependency in tests
            return ()

        results: list[SourceCandidate] = []
        for query in queries[: self.max_results]:
            try:
                with yt_dlp.YoutubeDL(
                    {
                        "quiet": True,
                        "no_warnings": True,
                        "skip_download": True,
                        "extract_flat": True,
                        "noplaylist": True,
                        "default_search": "ytsearch",
                    }
                ) as ydl:
                    info = ydl.extract_info(f"ytsearch{self.max_results}:{query}", download=False)
            except (yt_dlp.utils.DownloadError, OSError):
                continue
            candidates = self._coerce_results(info)
            for candidate in candidates:
                if candidate not in results:
                    results.append(candidate)
            if len(results) >= self.max_results:
                break
        return tuple(sorted(results, key=lambda item: _candidate_score(track, item), reverse=True)[: self.max_results])

    @staticmethod
    def _coerce_results(info: Any) -> list[SourceCandidate]:
        """Convert yt-dlp result data into a SourceCandidate list."""
        if info is None:
            return []
        entries: list[Mapping[str, Any]] = []
        if isinstance(info, Mapping):
            raw_entries = info.get("entries")
            if isinstance(raw_entries, (list, tuple)):
                entries = [entry for entry in raw_entries if isinstance(entry, Mapping)]
            elif isinstance(info.get("webpage_url") or info.get("url"), str):
                entries = [info]
        elif isinstance(info, list):
            entries = [entry for entry in info if isinstance(entry, Mapping)]

        candidates: list[SourceCandidate] = []
        for entry in entries:
            candidate = _candidate_from_mapping(entry)
            if candidate is not None:
                candidates.append(candidate)
        return candidates


def build_search_queries(track: Track) -> tuple[str, ...]:
    """Build likely queries for the actual standalone recording."""
    title = _clean_query(track.title)
    if not title:
        return ()

    artists = [
        cleaned_artist
        for artist in (track.artists or [])
        if (cleaned_artist := _clean_query(artist))
    ]

    query_variants: list[str] = []
    artist_text = " ".join(artists)

    base = title if not artist_text else f"{title} {artist_text}"
    query_variants.append(base)
    query_variants.append(f"{base} official audio")
    query_variants.append(f"{base} full song")

    album = _clean_query(track.album or "")
    if album and album not in title and album not in artist_text:
        query_variants.append(f"{title} {artist_text} {album}".strip())
        query_variants.append(f"{title} {artist_text} {album} official audio".strip())

    # Prefer actual standalone recordings over videos by using explicit audio cues.
    query_variants.append(f"{base} audio")
    query_variants.append(f"{base} official")

    deduped: list[str] = []
    seen: set[str] = set()
    for query in query_variants:
        normalized = " ".join(query.split())
        if normalized and normalized.casefold() not in seen:
            seen.add(normalized.casefold())
            deduped.append(normalized)
    return tuple(deduped[:8])


def search_online_sources(track: Track, *, max_results: int = 8) -> tuple[SourceCandidate, ...]:
    """Convenience function used by callers that want a track-to-candidate search."""
    return OnlineSourceSearcher(max_results=max_results).search(track)


def _clean_query(value: str | None) -> str:
    if value is None:
        return ""
    text = normalize(value)
    return " ".join(part for part in text.split() if part)


def _candidate_from_mapping(raw: Mapping[str, Any]) -> SourceCandidate | None:
    title = _coerce_text(raw.get("title")) or _coerce_text(raw.get("track"))
    if not title:
        return None

    url = _coerce_text(raw.get("webpage_url") or raw.get("url") or raw.get("canonical_url"))
    if not url:
        return None

    uploader = _coerce_text(raw.get("uploader") or raw.get("channel") or raw.get("channel_id"))
    artist = _coerce_text(raw.get("artist") or raw.get("creator") or raw.get("uploader_artist"))
    source_type = _coerce_text(raw.get("extractor") or raw.get("source_type") or raw.get("type"))
    duration_s = _coerce_duration(raw.get("duration") or raw.get("duration_seconds"))

    candidate = SourceCandidate(
        url=url,
        title=title,
        uploader=uploader,
        artist=artist,
        duration_s=duration_s,
        source_type=source_type,
        metadata=dict(raw),
    )
    if _is_unwanted_candidate(candidate):
        return None
    return candidate


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_duration(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_unwanted_candidate(candidate: SourceCandidate) -> bool:
    haystack = " ".join(
        part
        for part in (
            candidate.title,
            candidate.uploader,
            candidate.artist,
            candidate.source_type,
            candidate.metadata.get("description", ""),
        )
        if part
    ).casefold()
    return any(token in haystack for token in _SEARCH_EXCLUSION_TOKENS)


def _candidate_score(track: Track, candidate: SourceCandidate) -> float:
    track_title = normalize(track.title)
    track_artists = " ".join(normalize(artist) for artist in track.artists if normalize(artist))
    title = normalize(candidate.title)
    artist = normalize(candidate.artist or candidate.uploader or "")

    score = 0.0
    if track_title and title:
        if track_title == title:
            score += 1.0
        else:
            score += 0.7 * _similarity(track_title, title)
    if track_artists and artist:
        score += 0.5 * _similarity(track_artists, artist)

    if candidate.duration_s is not None and track.duration_ms:
        diff = abs(candidate.duration_s - (track.duration_ms / 1000))
        if diff <= 3:
            score += 0.2
        elif diff <= 12:
            score += 0.1

    if not _is_unwanted_candidate(candidate):
        score += 0.15

    if candidate.source_type and "youtube" in candidate.source_type.casefold():
        score += 0.05

    return score


def _similarity(lhs: str, rhs: str) -> float:
    if not lhs or not rhs:
        return 0.0
    lhs_tokens = set(lhs.split())
    rhs_tokens = set(rhs.split())
    if not lhs_tokens or not rhs_tokens:
        return 0.0
    overlap = len(lhs_tokens & rhs_tokens)
    return overlap / max(len(lhs_tokens | rhs_tokens), 1)


__all__ = [
    "OnlineSourceSearcher",
    "SourceCandidate",
    "build_search_queries",
    "search_online_sources",
]
