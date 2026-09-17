"""Source-independent text normalization used by the local audio layer."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def normalize(value: str | None) -> str:
    """Return a comparison-friendly representation of text."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff\u3040-\u30ff]+", " ", text)
    return text.strip()


def normalize_artists(artists: list[str] | str | None) -> str:
    """Normalize one or more artist names into a comparison string."""
    if isinstance(artists, str):
        artists = artists.replace(";", ", ").split(",")
    return normalize(" ".join(artists or []))


def track_key(title: str, artists: list[str] | str | None = None) -> str:
    """Build the canonical title/artist lookup key."""
    title_key = normalize(title)
    artist_key = normalize_artists(artists)
    return f"{title_key} {artist_key}".strip()


def filename_stem(path: str | Path) -> str:
    """Normalize an audio filename without its extension."""
    return normalize(Path(path).stem)


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
