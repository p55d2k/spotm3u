"""Album and artist artwork orchestration: lookup order, embedding, cleanup.

The artwork pipeline split out of :mod:`spotm3u.metadata`. Remote providers and
the candidate matching that decides which result is trustworthy live in
:mod:`spotm3u.artwork_sources`; the cache layout, keys, and in-process memo live
in :mod:`spotm3u.artwork_cache`. This module owns the lookup order, in-flight
deduplication, and the embedded/sidecar artwork already next to a file.
:mod:`spotm3u.metadata` writes the tags, including the images found here.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

import mutagen.id3 as mutagen_id3

from .artwork_cache import (
    _ARTIST_ARTWORK_SUBDIR,
    _ARTWORK_LOCK,
    _ARTWORK_MEMORY,
    _ARTWORK_MEMORY_LIMIT,
    _artist_cache_dir,
    _artist_cache_key,
    _artwork_memory_key,
    _cache_dir,
    _cache_key,
    _cached_artwork_path,
    _forget_memory_entry,
    _load_cached_artwork,
    _read_artwork_source,
    _read_cached_image,
    _read_cached_source,
    _remove_cached_entry,
    _save_cached_artwork,
    _write_artwork_source,
    _write_cached_image,
    _write_cached_source,
)
from .artwork_sources import (
    _ARTIST_ARTWORK_SOURCES,
    _ARTWORK_TRUSTED_SOURCES,
    ArtworkCandidate,
    _artist_album_match,
    _download_artwork,
    _get_coverart_candidates,
    _has_reliable_album,
    _search_deezer_artist_artwork,
    _search_itunes_artwork,
    _search_itunes_song_artwork,
    _search_musicbrainz_release,
)
from .models import Track

logger = logging.getLogger(__name__)


# Bounded concurrency: album artwork lookups share one in-flight request per
# identity and only a few external HTTP requests may be open at once, no matter
# how many resolution workers are running.
_ARTWORK_INFLIGHT: dict[str, _PendingArtworkFetch] = {}
_ARTWORK_FETCH_SEMAPHORE = threading.Semaphore(2)


class _PendingArtworkFetch:
    """A shared in-flight artwork fetch other threads can wait on."""

    __slots__ = ("event", "result", "done")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: tuple[bytes, str] | None = None
        self.done = False


# When True (default), local embedded/sidecar art is re-verified against the
# release when the album identity is reliable, so a wrong local cover is never
# borrowed. When False, existing local art is trusted and used immediately,
# which avoids the network lookups entirely for files that already carry a
# cover. Configured through ``artwork.verify_local`` in config.toml.
_ARTWORK_VERIFY_LOCAL = True


def set_artwork_verify_local(enabled: bool) -> None:
    """Enable or disable re-verification of local artwork against the release."""
    global _ARTWORK_VERIFY_LOCAL
    _ARTWORK_VERIFY_LOCAL = enabled


# When True (default), the performing artist's profile image is embedded as an
# ID3 artist picture (APIC type 8) next to the front cover. Configured through
# ``artwork.artist_artwork`` in config.toml.
_ARTIST_ARTWORK_ENABLED = True


def set_artist_artwork_enabled(enabled: bool) -> None:
    """Enable or disable embedding artist artwork."""
    global _ARTIST_ARTWORK_ENABLED
    _ARTIST_ARTWORK_ENABLED = enabled


def artist_artwork_enabled() -> bool:
    """Return whether artist artwork embedding is currently enabled."""
    return _ARTIST_ARTWORK_ENABLED


# When True (default) the front cover is looked up and embedded as the file's
# album artwork. When False no cover lookup or embedding happens at all, which
# keeps files smaller (embedded covers are the bulk of the metadata). Configured
# through ``artwork.album_artwork`` in config.toml.
_ALBUM_ARTWORK_ENABLED = True


def set_album_artwork_enabled(enabled: bool) -> None:
    """Enable or disable album cover lookup and embedding."""
    global _ALBUM_ARTWORK_ENABLED
    _ALBUM_ARTWORK_ENABLED = enabled


def album_artwork_enabled() -> bool:
    """Return whether album artwork embedding is currently enabled."""
    return _ALBUM_ARTWORK_ENABLED


def _deduplicated_artwork_fetch(
    download_dir: Path, cache_key: str, fetch: Callable[[], tuple[bytes | None, str | None]]
) -> tuple[bytes | None, str | None]:
    """Fetch artwork once per identity while concurrent workers wait and reuse.

    The first caller performs the network lookup (under a bounded semaphore);
    concurrent callers for the same identity join the in-flight request instead
    of repeating it. Positive results are memoized process-wide (per cache
    directory) so tracks from the same release share one lookup.

    The memo and its lock live in :mod:`spotm3u.artwork_cache`; the same critical
    section guards the memo and the in-flight registry so two workers can never
    both miss the memo and start a duplicate lookup for one identity.
    """
    memory_key = _artwork_memory_key(download_dir, cache_key)
    with _ARTWORK_LOCK:
        entry = _ARTWORK_MEMORY.get(memory_key)
        if entry is not None:
            return entry
        pending = _ARTWORK_INFLIGHT.get(memory_key)
        if pending is not None:
            owner = False
        else:
            pending = _PendingArtworkFetch()
            _ARTWORK_INFLIGHT[memory_key] = pending
            owner = True

    if not owner:
        pending.event.wait()
        return pending.result

    try:
        with _ARTWORK_FETCH_SEMAPHORE:
            result = fetch()
    finally:
        pending.result = result
        pending.done = True
        pending.event.set()
        with _ARTWORK_LOCK:
            _ARTWORK_INFLIGHT.pop(memory_key, None)

    if result[0] is not None:
        with _ARTWORK_LOCK:
            _ARTWORK_MEMORY[memory_key] = result
            _ARTWORK_MEMORY.move_to_end(memory_key)
            while len(_ARTWORK_MEMORY) > _ARTWORK_MEMORY_LIMIT:
                _ARTWORK_MEMORY.popitem(last=False)
    return result


def _embedded_artwork(path: Path) -> tuple[bytes, str] | None:
    """Return an existing front-cover APIC frame, if the source already has one.

    An image is returned only when it is clearly the front cover: an explicit
    type-3 frame, or the sole APIC frame in the file. When several frames exist
    and none is marked as a front cover the file is ambiguous (a back cover or
    artist photo could be picked), so nothing is returned instead of guessing.
    """
    try:
        tags = mutagen_id3.ID3(str(path))
    except (mutagen_id3.ID3NoHeaderError, OSError, ValueError):
        return None
    frames = tags.getall("APIC")
    if not frames:
        return None
    covers = [frame for frame in frames if getattr(frame, "type", None) == 3]
    if not covers and len(frames) == 1:
        covers = frames
    if not covers:
        return None
    frame = max(covers, key=lambda item: len(getattr(item, "data", b"") or b""))
    mime = getattr(frame, "mime", "image/jpeg")
    return frame.data, mime


def _sidecar_artwork(path: Path) -> tuple[bytes, str] | None:
    """Read artwork saved alongside an audio download by a source tool."""
    for suffix, mime in (
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".png", "image/png"),
        (".webp", "image/webp"),
    ):
        candidate = path.with_suffix(suffix)
        try:
            data = candidate.read_bytes()
        except OSError:
            continue
        if data:
            return data, mime
    return None


def _find_album_artwork(
    download_dir: Path,
    artist: str,
    album: str,
    title: str = "",
    audio_path: Path | None = None,
) -> tuple[bytes | None, str | None]:
    """Find artwork for a track, preferring a verified release cover.

    Album artwork belongs to the release, so when ``artist + album`` is
    reliable the verified release lookup (MusicBrainz / Cover Art Archive /
    iTunes) is authoritative. The local file's embedded or sidecar art is only
    a fallback: an existing local cover (for example from a mis-tagged or
    previously-downloaded file) is frequently the wrong artwork for the target
    release and must never be trusted ahead of a verified album identity.

    This release-first verification is disabled by ``set_artwork_verify_local``
    (the ``artwork.verify_local`` config option). When disabled, existing local
    art is trusted and used immediately so files that already carry a cover do
    no network lookups at all, at the cost of possibly borrowing a wrong cover.

    Cached entries that came from a verified lookup are authoritative. Legacy
    unmarked entries and ``embedded:``/``sidecar:`` entries may carry a wrong
    cover, so they are re-verified against the release when the album identity
    is reliable and local verification is enabled; offline, the best local
    evidence is kept. ``artist + song`` remains a fallback used only when the
    album lookup is impossible or fails.
    """
    # Older versions wrote permanent negative-cache markers. Remove only the
    # marker for this identity so improved lookup logic gets a fresh attempt.
    stale_failure = _cache_dir(download_dir) / f"{_cache_key(artist, album)}.failed"
    try:
        stale_failure.unlink(missing_ok=True)
    except OSError:
        pass

    cache_key = _cache_key(artist, album, title)
    release_first = _has_reliable_album(album)

    cached = _load_cached_artwork(download_dir, cache_key)
    cached_source = _read_artwork_source(download_dir, cache_key)
    trusted = cached_source in _ARTWORK_TRUSTED_SOURCES
    if cached is not None and (not release_first or trusted or not _ARTWORK_VERIFY_LOCAL):
        logger.debug("Artwork cache hit artist=%s album=%s", artist, album)
        return cached, "cache"

    def fetch() -> tuple[bytes | None, str | None]:
        if not _ARTWORK_VERIFY_LOCAL and audio_path is not None:
            local = _local_artwork(download_dir, cache_key, audio_path)
            if local is not None:
                return local
        if release_first:
            data, source = _fetch_release_artwork(download_dir, artist, album, title, cache_key)
            if data is not None:
                return data, source
        if audio_path is not None:
            local = _local_artwork(download_dir, cache_key, audio_path)
            if local is not None:
                return local
        if title:
            data, source = _fetch_song_artwork(download_dir, artist, title, cache_key)
            if data is not None:
                return data, source
        # Negative results are deliberately not persisted. A later run may have
        # network access or a newly indexed release/artwork source.
        if cached is not None:
            return cached, "cache"
        logger.info("Artwork not found artist=%s album=%s title=%s", artist, album, title)
        return None, "not-found"

    return _deduplicated_artwork_fetch(download_dir, cache_key, fetch)


def _local_artwork(
    download_dir: Path, cache_key: str, audio_path: Path
) -> tuple[bytes, str] | None:
    """Return embedded or sidecar artwork from a local file, cached by identity."""
    embedded = _embedded_artwork(audio_path)
    if embedded is not None:
        data, mime = embedded
        _save_cached_artwork(download_dir, cache_key, data)
        _write_artwork_source(download_dir, cache_key, f"embedded:{mime}")
        return data, f"embedded:{mime}"
    sidecar = _sidecar_artwork(audio_path)
    if sidecar is not None:
        data, mime = sidecar
        _save_cached_artwork(download_dir, cache_key, data)
        _write_artwork_source(download_dir, cache_key, f"sidecar:{mime}")
        return data, f"sidecar:{mime}"
    return None


def _fetch_release_artwork(
    download_dir: Path,
    artist: str,
    album: str,
    title: str = "",
    cache_key: str = "",
) -> tuple[bytes | None, str | None]:
    """Look up artwork by artist + album using MusicBrainz and iTunes."""
    releases = _search_musicbrainz_release(artist, album)
    candidates: list[ArtworkCandidate] = []
    for release in releases:
        release_artist = release.get("artist-credit-phrase")
        release_album = release.get("title")
        if (
            release_artist
            and release_album
            and not _artist_album_match(artist, album, release_artist, release_album)
        ):
            continue
        rg_id = release.get("id")
        if rg_id:
            candidates.extend(_get_coverart_candidates(rg_id))
    candidates.extend(_search_itunes_artwork(artist, album, title))

    return _pick_candidate(download_dir, candidates, cache_key, artist, album, title)


def _fetch_song_artwork(
    download_dir: Path, artist: str, title: str, cache_key: str = ""
) -> tuple[bytes | None, str | None]:
    """Look up artwork by artist + song title (album fallback)."""
    candidates = _search_itunes_song_artwork(artist, title)
    return _pick_candidate(download_dir, candidates, cache_key, artist, "", title)


def _pick_candidate(
    download_dir: Path,
    candidates: list[ArtworkCandidate],
    cache_key: str,
    artist: str,
    album: str,
    title: str,
) -> tuple[bytes | None, str | None]:
    """Download the highest-quality candidate and cache a successful image."""
    for candidate in sorted(
        candidates,
        key=lambda item: (item.width or 0) * (item.height or 0),
        reverse=True,
    ):
        data = _download_artwork(candidate.url)
        if data is not None:
            _save_cached_artwork(download_dir, cache_key, data)
            _write_artwork_source(download_dir, cache_key, candidate.source)
            logger.info(
                "Artwork downloaded artist=%s album=%s title=%s source=%s",
                artist,
                album,
                title,
                candidate.source,
            )
            return data, candidate.source
    return None, "not-found"


def _find_artist_artwork(download_dir: Path, artist: str) -> tuple[bytes | None, str | None]:
    """Find (or download) the profile image for one artist.

    Artist images are keyed by artist alone and cached in their own directory,
    so every track credited to the same artist reuses a single download. A
    failure returns ``(None, "not-found")`` and never raises: artist artwork is
    optional enrichment and must not fail track generation.
    """
    if not artist:
        return None, "not-found"

    cache_dir = _artist_cache_dir(download_dir)
    cache_key = _artist_cache_key(artist)
    cached = _read_cached_image(cache_dir, cache_key)
    if cached is not None and _read_cached_source(cache_dir, cache_key) in _ARTIST_ARTWORK_SOURCES:
        logger.debug("Artist artwork cache hit artist=%s", artist)
        return cached, "cache"

    def fetch() -> tuple[bytes | None, str | None]:
        candidate = _search_deezer_artist_artwork(artist)
        if candidate is None:
            logger.info("Artist artwork not found artist=%s", artist)
            return None, "not-found"
        data = _download_artwork(candidate.url)
        if data is None:
            return None, "not-found"
        _write_cached_image(cache_dir, cache_key, data)
        _write_cached_source(cache_dir, cache_key, candidate.source)
        logger.info("Artist artwork downloaded artist=%s source=%s", artist, candidate.source)
        return data, candidate.source

    # The memory key is namespaced by the artist subdirectory so it can never be
    # confused with a release identity that happens to sanitize the same way.
    return _deduplicated_artwork_fetch(download_dir, f"{_ARTIST_ARTWORK_SUBDIR}/{cache_key}", fetch)


def artwork_artist(track: Track) -> str | None:
    """Return the artist identity to use for artwork lookups.

    Prefers the explicit album artist and otherwise the first structured
    artist. Collaborations are never joined into one malformed string.
    """
    if not track.artists:
        return None
    return track.album_artist or track.artists[0]


def prune_missing_artwork(
    download_dir: str | Path,
    tracks: Iterable[Track],
    *,
    keep_artists: Iterable[str] = (),
) -> int:
    """Forget cached artwork for tracks whose audio file is gone.

    Artwork entries are keyed by release identity, not by audio file, so a
    deleted download never makes a cached cover *wrong* - and this deliberately
    does not pretend otherwise. What it removes is the entry nothing in the
    download folder references any more: the release image, its recorded
    ``*.src`` marker and the in-process memo for that identity. The next run
    re-fetches whatever it needs, so this only reclaims disk and keeps the cache
    honest about what is still downloaded.

    Artist profile images are shared by every album of that artist, so they are
    dropped only for artists absent from ``keep_artists`` (the artists that
    still have a track on disk or waiting to be re-downloaded). Returns the
    number of cache files removed.
    """
    directory = Path(download_dir)
    keep = {_artist_cache_key(artist) for artist in keep_artists if artist}
    removed = 0
    artists_to_drop: set[str] = set()
    release_keys: list[str] = []
    for track in tracks:
        artist = artwork_artist(track)
        if artist is None:
            continue
        cache_key = _cache_key(artist, track.album or "", track.title)
        release_keys.append(cache_key)
        artist_key = _artist_cache_key(artist)
        if artist_key not in keep:
            artists_to_drop.add(artist_key)

    release_cache = _cache_dir(directory)
    artist_cache = _artist_cache_dir(directory)
    for cache_key in release_keys:
        removed += _remove_cached_entry(release_cache, cache_key)
        _forget_memory_entry(directory, cache_key)
    for artist_key in sorted(artists_to_drop):
        removed += _remove_cached_entry(artist_cache, artist_key)
        _forget_memory_entry(directory, f"{_ARTIST_ARTWORK_SUBDIR}/{artist_key}")
    return removed


def cached_artwork_path(download_dir: str | Path, track: Track) -> Path | None:
    """Return the locally cached artwork file for a track, or None.

    Reads only the artwork cache written during resolution; performs no network
    requests, so it is safe to call while rendering the interface.
    """
    artist = artwork_artist(track)
    if artist is None:
        return None
    cache_key = _cache_key(artist, track.album or "", track.title)
    path = _cached_artwork_path(_cache_dir(download_dir), cache_key)
    return path if path.is_file() else None


__all__ = [
    "_find_album_artwork",
    "_find_artist_artwork",
    "album_artwork_enabled",
    "artist_artwork_enabled",
    "artwork_artist",
    "cached_artwork_path",
    "prune_missing_artwork",
    "set_album_artwork_enabled",
    "set_artist_artwork_enabled",
    "set_artwork_verify_local",
]
