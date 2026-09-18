"""Data models shared by the playlist and local-audio layers."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Playlist:
    """A source-independent playlist and its ordered tracks."""

    id: str | None
    name: str
    url: str = ""
    track_count: int | None = None
    tracks: list["Track"] = field(default_factory=list)


@dataclass(frozen=True)
class Track:
    """Track metadata collected from a playlist source."""

    title: str
    artists: list[str]
    album: str | None = None
    duration_ms: int | None = None
    spotify_id: str | None = None
    spotify_url: str | None = None
    album_artist: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    release_year: int | str | None = None
    genre: str | None = None
    comments: str | None = None


@dataclass(frozen=True)
class ResolvedTrack:
    """A track matched to a verified audio file available locally."""

    track: Track
    local_path: Path
    resolution_method: str = "local"
    source_url: str | None = None
    status: str = "local"
