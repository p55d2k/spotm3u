"""Best-effort lyrics retrieval for resolved audio files.

Lyrics come from the ``syncedlyrics`` library, which searches public lyrics
providers itself. SpotM3U never runs its own web search, scrapes lyrics sites,
or hardcodes provider URLs and parsers.

Synced lyrics in LRC form are preferred — the library searches for those first
and falls back to plain text — so a returned result is either timestamped LRC
(see :func:`is_synced_lyrics`) or plain text.

Retrieval is optional enrichment and is deliberately isolated here so it can
fail (or be replaced) without touching the download pipeline: a missing match,
a provider/network problem, or an unusable result all resolve to ``None``.
Fast mode disables it entirely through :func:`set_lyrics_enabled`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import syncedlyrics

from .models import Track

logger = logging.getLogger(__name__)

# An LRC line timestamp, e.g. ``[00:06.21]`` or ``[01:20]``. LRC files also carry
# non-timestamp metadata tags (``[ar:Artist]``), which deliberately do not match:
# only a timestamp makes the text synchronised. A line may hold several tags.
_LRC_TIMESTAMP = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]")

# When True (default), normal enrichment retrieves lyrics for each resolved
# track. Fast mode turns this off so the lightweight download path performs no
# lyrics lookups at all. Configured through ``lyrics.enabled`` in config.toml.
_LYRICS_ENABLED = True


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

    Only used to decide whether the text is worth a sidecar ``.lrc`` file; the
    embedded lyrics field holds whichever form the providers returned.
    """
    return bool(_LRC_TIMESTAMP.search(lyrics))


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
    try:
        raw = syncedlyrics.search(term)
    except Exception as exc:  # any provider/library failure is non-critical
        logger.debug("Lyrics lookup failed term=%s: %s", term, exc)
        return None
    lyrics = normalize_lyrics(raw)
    if lyrics is None:
        logger.debug("Lyrics not found term=%s", term)
    return lyrics


__all__ = [
    "fetch_lyrics",
    "is_synced_lyrics",
    "lyrics_enabled",
    "lyrics_search_term",
    "normalize_lyrics",
    "set_lyrics_enabled",
]
