"""Source-independent text normalization used by the local audio layer.

These helpers return matching-only values.  Callers retain the source metadata
on :class:`~spotm3u.models.Track` (or their local file metadata) unchanged.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
import re

from zhconv import convert as _zh_convert


_FEATURING_RE = re.compile(r"\b(?:featuring|feat\.?|ft\.?)\b", re.IGNORECASE)
_TRACK_NUMBER_RE = re.compile(r"^\s*(?:\d{1,3}\s*[-_.]\s*|\d{1,3}\s+)")
_FILENAME_TAG_RE = re.compile(
    r"(?:\s*[\(\[]\s*(?:official\s+)?(?:audio|video|lyrics?|lyric\s+video)\s*[\)\]]"
    r"|\s*-\s*(?:official\s+)?(?:audio|video|lyrics?|lyric\s+video)\s*$)",
    re.IGNORECASE,
)


def normalize(value: str | None) -> str:
    """Return a comparison-friendly representation without changing input."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    # Keep letters and numbers from every script; punctuation and symbols are
    # separators, so "Artist_Title" and "Artist - Title" compare equally.
    text = "".join(char if char.isalnum() else " " for char in text)
    return " ".join(text.split())


def normalize_cjk(value: str | None) -> str:
    """Normalize CJK text for comparison after converting Traditional to Simplified.

    YouTube titles and uploader names frequently use Traditional Chinese for
    songs that Spotify describes in Simplified form (e.g. ``薛之謙`` vs
    ``薛之谦``, ``演員`` vs ``演员``). Script unification makes the two forms
    match during search-result ranking without changing source metadata.
    """
    base = normalize(value)
    if not base:
        return base
    return _zh_convert(base, "zh-cn")


def normalize_artists(artists: list[str] | str | None) -> str:
    """Normalize one or more artist names into a comparison string."""
    if isinstance(artists, str):
        artists = re.split(r"\s*(?:,|;|/|\||&|\band\b)\s*", artists, flags=re.IGNORECASE)
    text = " ".join(
        _FEATURING_RE.sub(" ", artist)
        for artist in (artists or [])
        if artist
    )
    return normalize(text)


def track_key(title: str, artists: list[str] | str | None = None) -> str:
    """Build the canonical title/artist lookup key."""
    title_key = normalize(title)
    artist_key = normalize_artists(artists)
    return f"{title_key} {artist_key}".strip()


def filename_stem(path: str | Path) -> str:
    """Normalize an audio filename without its extension."""
    stem = _TRACK_NUMBER_RE.sub("", Path(path).stem)
    stem = _FILENAME_TAG_RE.sub(" ", stem)
    return normalize(stem)


def filename_keys(path: str | Path) -> tuple[str, ...]:
    """Return equivalent normalized forms for common filename layouts."""
    stem = _TRACK_NUMBER_RE.sub("", Path(path).stem)
    stem = _FILENAME_TAG_RE.sub(" ", stem)
    parts = [normalize(part) for part in re.split(r"\s+-\s+", stem) if part]
    normalized_stem = normalize(stem)
    keys = [normalized_stem]
    if len(parts) >= 2:
        keys.extend((" ".join(reversed(parts)), " ".join(parts)))
    return tuple(dict.fromkeys(key for key in keys if key))


def sanitize_filename_component(value: str | None) -> str:
    """Make a user-facing track component safe for a local filename."""
    if value is None:
        return "Unknown"
    cleaned = re.sub(r'[<>:"/\\|?*]', " ", str(value))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return cleaned or "Unknown"


def build_output_basename(title: str, artists: list[str] | str | None) -> str:
    """Build the conventional ``title - artist`` audio filename stem."""
    artist_text = ", ".join(artists) if isinstance(artists, list) else (artists or "")
    title_part = sanitize_filename_component(title)
    artist_part = sanitize_filename_component(artist_text.replace(";", ", "))
    return f"{title_part} - {artist_part}" if artist_part and artist_part != title_part else title_part
