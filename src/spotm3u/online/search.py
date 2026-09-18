"""Search for online source candidates for a Spotify/Exportify track.

Discovery is separated from selection: this layer generates several focused
artist-aware queries, aggregates and deduplicates candidates from every query,
and returns them all. It never picks a "best" candidate and never downloads
audio. Recording identity and source-quality ranking happen in the ranking
layer after all queries have been seen.
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from ..models import Track
from ..normalization import normalize

# Cap on concurrent network queries for one track. Queries run in parallel
# because each one is an independent yt-dlp request; keeping the count modest
# avoids hammering the source site from a playlist of many tracks.
DEFAULT_SEARCH_WORKERS = 4

# A small, configurable set of focused search queries. The artist is always
# included because a title-only search tends to return same-title uploads by
# other artists (e.g. ``演员`` by Hebe Tien instead of by Joker Xue).
SEARCH_QUERY_TEMPLATES = (
    "{artist} {title}",
    "{artist} {title} lyrics",
    "{artist} {title} lyric",
    "{artist} {title} official audio",
    "{artist} {title} audio",
    "{artist} {title} official",
    "{title} {artist}",
)

# Title-only fallbacks used only when artist information is genuinely absent.
TITLE_ONLY_QUERY_TEMPLATES = (
    "{title}",
    "{title} lyrics",
    "{title} lyric",
    "{title} official audio",
    "{title} audio",
    "{title} official",
)

_SEARCH_EXCLUSION_TOKENS = (
    "interview",
    "reaction",
    "movie",
    "scene",
    "trailer",
    "teaser",
    "podcast",
    "documentary",
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
    source_query: str | None = None

    @property
    def platform(self) -> str | None:
        if self.source_type:
            return self.source_type
        source = (self.metadata.get("extractor") or self.metadata.get("source_platform") or "").strip()
        return source or None


class OnlineSourceSearcher:
    """Build search queries and collect candidate metadata for a track."""

    def __init__(
        self,
        *,
        max_results: int = 8,
        max_search_workers: int = DEFAULT_SEARCH_WORKERS,
    ) -> None:
        self.max_results = max_results
        self.max_search_workers = max_search_workers

    def search(self, track: Track) -> tuple[SourceCandidate, ...]:
        """Aggregate candidates from every focused query for ``track``.

        All queries in the configured template set are run so that a candidate
        appearing only in a later query is still discovered. Queries run
        concurrently and candidates are deduplicated by URL while preserving
        query order; ranking happens later. If the search backend is
        unavailable or a search fails, this returns an empty tuple instead of
        raising.
        """
        queries = build_search_queries(track)
        if not queries:
            return ()

        try:
            import yt_dlp  # type: ignore
        except ImportError:  # pragma: no cover - optional dependency in tests
            return ()

        workers = min(len(queries), self.max_search_workers)
        if workers <= 1:
            results_per_query = [
                self._run_query(yt_dlp, query) for query in queries
            ]
        else:
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="spotm3u-search",
            ) as pool:
                results_per_query = list(
                    pool.map(lambda query: self._run_query(yt_dlp, query), queries)
                )

        results: list[SourceCandidate] = []
        seen_urls: set[str] = set()
        for candidates in results_per_query:
            for candidate in candidates:
                if candidate.url in seen_urls:
                    continue
                seen_urls.add(candidate.url)
                results.append(candidate)
        return tuple(results)

    def _run_query(self, yt_dlp: Any, query: str) -> list[SourceCandidate]:
        """Run one focused query and return its coerced candidates."""
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
                info = ydl.extract_info(
                    f"ytsearch{self.max_results}:{query}", download=False
                )
        except (yt_dlp.utils.DownloadError, OSError):
            return []
        return OnlineSourceSearcher._coerce_results(info, source_query=query)

    @staticmethod
    def _coerce_results(info: Any, *, source_query: str | None = None) -> list[SourceCandidate]:
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
            candidate = _candidate_from_mapping(entry, source_query=source_query)
            if candidate is not None:
                candidates.append(candidate)
        return candidates


def build_search_queries(track: Track) -> tuple[str, ...]:
    """Build a small set of artist-aware queries for the standalone recording."""
    title = _clean_query(track.title)
    if not title:
        return ()

    artists = [
        cleaned_artist
        for artist in (track.artists or [])
        if (cleaned_artist := _clean_query(artist))
    ]

    if artists:
        templates = SEARCH_QUERY_TEMPLATES
        artist_text = " ".join(artists)
    else:
        templates = TITLE_ONLY_QUERY_TEMPLATES
        artist_text = ""

    query_variants: list[str] = []
    for template in templates:
        query_variants.append(template.format(artist=artist_text, title=title).strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for query in query_variants:
        normalized = " ".join(query.split())
        if normalized and normalized.casefold() not in seen:
            seen.add(normalized.casefold())
            deduped.append(normalized)
    return tuple(deduped)


def search_online_sources(track: Track, *, max_results: int = 8) -> tuple[SourceCandidate, ...]:
    """Convenience function used by callers that want a track-to-candidate search."""
    return OnlineSourceSearcher(max_results=max_results).search(track)


def _clean_query(value: str | None) -> str:
    if value is None:
        return ""
    text = normalize(value)
    return " ".join(part for part in text.split() if part)


def _candidate_from_mapping(
    raw: Mapping[str, Any], *, source_query: str | None = None
) -> SourceCandidate | None:
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
        source_query=source_query,
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


__all__ = [
    "OnlineSourceSearcher",
    "SEARCH_QUERY_TEMPLATES",
    "SourceCandidate",
    "TITLE_ONLY_QUERY_TEMPLATES",
    "build_search_queries",
    "search_online_sources",
]