"""Artwork cache: on-disk layout, cache keys, and the in-process memo.

The orchestrating code in :mod:`spotm3u.artwork` reads and writes artwork through
these helpers, so the cache directory layout and the memo it keeps are described
in one place instead of being scattered through the lookup code.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from .artwork_sources import _normalize_album_for_search, _normalize_identity

# Bounded in-process artwork memo, keyed by cache directory and identity so that
# distinct music libraries never share results.
_ARTWORK_MEMORY_LIMIT = 1024
_ARTWORK_MEMORY: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
_ARTWORK_LOCK = threading.Lock()

_ARTWORK_CACHE_DIR = "artwork_cache"


# Artist images live in their own subdirectory of the artwork cache. Artist
# identity alone is the cache key there, so it can never collide with (or be
# mistaken for) a release entry keyed by artist + album / artist + title.
_ARTIST_ARTWORK_SUBDIR = "artists"


def _cache_dir(download_dir: Path) -> Path:
    cache_path = download_dir / _ARTWORK_CACHE_DIR
    cache_path.mkdir(parents=True, exist_ok=True)
    return cache_path


def _artist_cache_dir(download_dir: Path) -> Path:
    """Return the artist-image cache directory, separate from release artwork."""
    cache_path = _cache_dir(download_dir) / _ARTIST_ARTWORK_SUBDIR
    cache_path.mkdir(parents=True, exist_ok=True)
    return cache_path


def _cache_key(artist: str = "", album: str = "", title: str = "") -> str:
    """Generate a stable cache key from a release or song identity.

    Album identity (artist + album) is preferred because artwork belongs to the
    release; the song identity (artist + title) is used as the fallback key when
    album metadata is missing.
    """
    release = album or title
    normalized = (
        _normalize_identity(artist)
        + "||"
        + (_normalize_identity(_normalize_album_for_search(release)) if release else "")
    )
    safe = re.sub(r"[^a-z0-9]+", "_", normalized.casefold()).strip("_")
    return safe[:120] or "unknown"


def _cached_artwork_path(cache_dir: Path, cache_key: str) -> Path:
    return cache_dir / f"{cache_key}.jpg"


def _read_cached_image(cache_dir: Path, cache_key: str) -> bytes | None:
    """Read a cached image from one cache directory, or None."""
    path = _cached_artwork_path(cache_dir, cache_key)
    if path.is_file():
        try:
            data = path.read_bytes()
            return data or None
        except OSError:
            return None
    return None


def _write_cached_image(cache_dir: Path, cache_key: str, data: bytes) -> None:
    """Write image data into one cache directory, replacing any existing entry."""
    path = _cached_artwork_path(cache_dir, cache_key)
    if not data:
        return
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=cache_dir, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary.write(data)
            temporary_path = temporary.name
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _load_cached_artwork(download_dir: Path, cache_key: str) -> bytes | None:
    return _read_cached_image(_cache_dir(download_dir), cache_key)


def _save_cached_artwork(download_dir: Path, cache_key: str, data: bytes) -> None:
    _write_cached_image(_cache_dir(download_dir), cache_key, data)


def _artwork_source_path(cache_dir: Path, cache_key: str) -> Path:
    return cache_dir / f"{cache_key}.src"


def _read_cached_source(cache_dir: Path, cache_key: str) -> str | None:
    """Read the recorded source of one cached image, or None."""
    path = _artwork_source_path(cache_dir, cache_key)
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def _write_cached_source(cache_dir: Path, cache_key: str, source: str) -> None:
    """Record where one cached image came from."""
    path = _artwork_source_path(cache_dir, cache_key)
    try:
        path.write_text(source)
    except OSError:
        pass


def _read_artwork_source(download_dir: Path, cache_key: str) -> str | None:
    """Read the recorded source of a cached artwork entry, or None."""
    return _read_cached_source(_cache_dir(download_dir), cache_key)


def _remove_cached_entry(cache_dir: Path, cache_key: str) -> int:
    """Delete one image entry and its recorded source; return files removed."""
    removed = 0
    for path in (
        _cached_artwork_path(cache_dir, cache_key),
        _artwork_source_path(cache_dir, cache_key),
    ):
        try:
            path.unlink()
        except OSError:
            # Already gone or not removable: either way the entry is unusable.
            continue
        removed += 1
    return removed


def _forget_memory_entry(download_dir: Path, cache_key: str) -> None:
    """Drop one identity from the in-process memo so it cannot be re-served."""
    memory_key = _artwork_memory_key(download_dir, cache_key)
    with _ARTWORK_LOCK:
        _ARTWORK_MEMORY.pop(memory_key, None)


def _write_artwork_source(download_dir: Path, cache_key: str, source: str) -> None:
    """Record where a cached artwork entry came from.

    Verified release lookups record a trusted source; local embedded/sidecar
    art records an ``embedded:`` / ``sidecar:`` prefix so it can be re-verified
    against the release on a later networked run instead of being trusted.
    """
    _write_cached_source(_cache_dir(download_dir), cache_key, source)


def _artwork_memory_key(download_dir: Path, cache_key: str) -> str:
    """Scope the in-process memo to the cache directory so distinct libraries
    never share identity results."""
    return f"{str(_cache_dir(download_dir))}::{cache_key}"


def _artist_cache_key(artist: str) -> str:
    """Generate a stable cache key for one artist's profile image."""
    safe = re.sub(r"[^a-z0-9]+", "_", _normalize_identity(artist)).strip("_")
    return f"artist_{safe[:110] or 'unknown'}"


__all__ = [
    "_artist_cache_dir",
    "_artist_cache_key",
    "_artwork_source_path",
    "_cache_dir",
    "_cache_key",
    "_cached_artwork_path",
    "_save_cached_artwork",
    "_write_artwork_source",
    "_write_cached_image",
    "_write_cached_source",
]
