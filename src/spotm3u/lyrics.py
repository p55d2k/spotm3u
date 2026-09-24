"""Best-effort lyrics retrieval for resolved audio files.

Lyrics come from the ``syncedlyrics`` library, which searches public lyrics
providers itself. SpotM3U never runs its own web search, scrapes lyrics sites,
or hardcodes provider URLs and parsers.

Synced lyrics in LRC form are preferred — the library searches for those first
and falls back to plain text — so a returned result is either timestamped LRC
(see :func:`is_synced_lyrics`) or plain text. Parsing lives here too:
:func:`parse_lyrics` turns either form into a structured :class:`Lyrics` the
metadata writer can derive the plain and the timed frames from.

Retrieval is optional enrichment and is deliberately isolated here so it can
fail (or be replaced) without touching the download pipeline: a missing match,
a provider/network problem, or an unusable result all resolve to ``None``.
Fast mode disables it entirely through :func:`set_lyrics_enabled`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import syncedlyrics

from .metadata_cache import MetadataCache
from .models import Track
from .rate_limits import provider_request

logger = logging.getLogger(__name__)

# An LRC line timestamp, e.g. ``[00:06.21]`` or ``[01:20]``. LRC files also carry
# non-timestamp metadata tags (``[ar:Artist]``), which deliberately do not match:
# only a timestamp makes the text synchronised. A line may hold several tags.
_LRC_TIMESTAMP = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]")

# The same timestamp with its minutes/seconds/fraction captured, so a value can
# be turned into seconds.
_LRC_TIME = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")

# An LRC metadata tag at a line start, e.g. ``[ar:Artist]`` or ``[ti:Title]``.
# Deliberately excludes timestamp tags (which start with a digit) so a line like
# ``[00:12][00:24]Chorus`` strips every timestamp but no lyric text.
_LRC_META = re.compile(r"\[[a-zA-Z]+\s*:[^\]]*\]")

# When True (default), normal enrichment retrieves lyrics for each resolved
# track. Fast mode turns this off so the lightweight download path performs no
# lyrics lookups at all. Configured through ``lyrics.enabled`` in config.toml.
_LYRICS_ENABLED = True
_LYRICS_CACHE = MetadataCache()


def set_lyrics_enabled(enabled: bool) -> None:
    """Enable or disable lyrics retrieval."""
    global _LYRICS_ENABLED
    _LYRICS_ENABLED = enabled


def lyrics_enabled() -> bool:
    """Return whether lyrics retrieval is currently enabled."""
    return _LYRICS_ENABLED


def lyrics_search_term(track: Track) -> str:
    """Return the term handed to the lyrics library for one track.

    The library expects ``[track] [artist]``. The album is only used when no
    artist is known, where it supplies the identity the search would otherwise
    lack; it is not included alongside an artist because album titles introduce
    noise into lyrics matching.
    """
    artist = track.album_artist or (track.artists[0] if track.artists else "")
    parts = [track.title or "", artist]
    if not artist.strip() and track.album:
        parts.append(track.album)
    return " ".join(part.strip() for part in parts if part and part.strip())


def normalize_lyrics(raw: Any) -> str | None:
    """Return usable lyrics text, or ``None`` for an unusable result.

    Timestamps are kept: an LRC result is written as-is so players that read
    timed lyrics can use it, while a plain result stays plain. Anything that is
    not a non-empty string (a provider returning a structured payload, an empty
    page, whitespace) is treated as no result rather than written to the file.
    """
    if not isinstance(raw, str):
        return None
    text = raw.replace("\x00", "").strip()
    return text or None


def is_synced_lyrics(lyrics: str) -> bool:
    """Return whether ``lyrics`` carry LRC timestamps.

    Decides whether the text needs parsing: timestamps stay out of the plain
    lyrics field, and are only written, in structured form, into the synced
    lyrics field.
    """
    return bool(_LRC_TIMESTAMP.search(lyrics))


@dataclass(frozen=True)
class Lyrics:
    """Structured lyrics derived once from one source and shared by the writers.

    ``text`` is the timestamp-free lyric text that goes into the plain ``USLT``
    field. ``lines`` carries the same lines with their LRC timestamps in
    seconds, and is empty for plain lyrics: those get ``USLT`` only and no
    fabricated synchronization info.
    """

    text: str
    lines: tuple[tuple[float, str], ...] = ()

    @property
    def synced(self) -> bool:
        """Whether the lyrics carry timing information."""
        return bool(self.lines)


def _lrc_time_to_seconds(minutes: str, seconds: str, fraction: str | None) -> float:
    """Convert one LRC timestamp's parts to seconds, e.g. ``01:23.45`` -> 83.45.

    The fraction is any of decimal/thousandth precision (``.xx`` or ``.xxx``),
    and is scaled accordingly rather than assumed to be hundredths.
    """
    frac = int(fraction) / (10 ** len(fraction)) if fraction else 0.0
    return int(minutes) * 60 + int(seconds) + frac


def _split_lrc_line(line: str) -> tuple[list[float], str]:
    """Split one LRC line into its leading timestamps and the lyric text.

    Leading ``[...]`` groups are consumed in order: timestamp tags contribute a
    time, metadata tags (``[ar:...]``) are dropped. A line such as
    ``[00:12.00][01:20.00]Repeated chorus`` yields both timestamps. Any text
    before a bracket stops the scan; brackets inside lyric text are left alone.
    """
    times: list[float] = []
    rest = line
    while True:
        match = _LRC_TIME.match(rest)
        if match:
            times.append(_lrc_time_to_seconds(*match.groups()))
            rest = rest[match.end() :]
            continue
        match = _LRC_META.match(rest)
        if match:
            rest = rest[match.end() :]
            continue
        break
    return times, rest.strip()


def _parse_lrc_lines(text: str) -> tuple[tuple[tuple[float, str], ...], str]:
    """Parse LRC text into ``((seconds, line), ...)`` pairs and the plain text.

    A line carrying several timestamps yields one pair per timestamp. Timed and
    untimed lines both contribute to the plain text, so the two derived forms
    stay in step; empty and metadata-only lines are dropped from both.
    """
    pairs: list[tuple[float, str]] = []
    plain: list[str] = []
    for line in text.splitlines():
        times, lyric = _split_lrc_line(line)
        if not lyric:
            continue
        plain.append(lyric)
        pairs.extend((time, lyric) for time in times)
    return tuple(pairs), "\n".join(plain)


def parse_lyrics(raw: Any) -> Lyrics | None:
    """Return structured lyrics for ``raw``, or ``None`` when unusable.

    A plain result becomes ``Lyrics(text=...)``. An LRC result is parsed into
    ``(seconds, line)`` pairs shared by the synced and plain writers, so the two
    fields can never drift apart: the timestamps live only in the pairs, and the
    plain text is the parsed lines with the timestamps (and LRC metadata tags)
    gone. Anything :func:`normalize_lyrics` rejects is rejected here too, as is
    LRC text that times no actual lyrics.
    """
    text = normalize_lyrics(raw)
    if text is None:
        return None
    if not is_synced_lyrics(text):
        return Lyrics(text=text)
    pairs, plain = _parse_lrc_lines(text)
    if not pairs:
        return None
    return Lyrics(text=plain, lines=pairs)


def fetch_lyrics(track: Track) -> str | None:
    """Retrieve lyrics for ``track`` through the lyrics library.

    The library prefers synced (LRC) lyrics and falls back to plain text when a
    provider only has the latter, so the returned text may or may not carry
    timestamps.

    Returns ``None`` when retrieval is disabled, when the track has no usable
    identity, when no provider has the lyrics, or on any library/network
    failure. Nothing raises: the caller embeds lyrics only when they exist.
    """
    if not _LYRICS_ENABLED:
        return None
    term = lyrics_search_term(track)
    if not term:
        return None

    def fetch() -> str | None:
        def search():
            return syncedlyrics.search(term)

        try:
            raw = (
                search()
                if getattr(syncedlyrics.search, "__module__", "syncedlyrics") != "syncedlyrics"
                else provider_request("lyrics", search)
            )
        except Exception as exc:
            logger.debug("Lyrics lookup failed term=%s: %s", term, exc)
            return None
        lyrics = normalize_lyrics(raw)
        if lyrics is None:
            logger.debug("Lyrics not found term=%s", term)
        return lyrics

    # Test and embedding integrations may replace the provider function; do
    # not let those isolated callables share the process cache.
    if getattr(syncedlyrics.search, "__module__", "syncedlyrics") != "syncedlyrics":
        return fetch()
    key = (
        f"track:{track.spotify_id}"
        if track.spotify_id
        else f"song:{lyrics_search_term(track).casefold()}"
    )
    return _LYRICS_CACHE.get_or_fetch(key, fetch)


__all__ = [
    "Lyrics",
    "fetch_lyrics",
    "is_synced_lyrics",
    "lyrics_enabled",
    "lyrics_search_term",
    "normalize_lyrics",
    "parse_lyrics",
    "set_lyrics_enabled",
]
