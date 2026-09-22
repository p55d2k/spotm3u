"""Metadata and artwork enrichment for resolved audio files."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import mutagen.id3 as mutagen_id3
import requests

from .models import Track

logger = logging.getLogger(__name__)

ID3 = mutagen_id3.ID3
ID3NoHeaderError = mutagen_id3.ID3NoHeaderError
TIT2 = mutagen_id3.TIT2
TPE1 = mutagen_id3.TPE1
TPE2 = mutagen_id3.TPE2
TALB = mutagen_id3.TALB
TRCK = mutagen_id3.TRCK
TPOS = mutagen_id3.TPOS
TDRC = mutagen_id3.TDRC
TCON = mutagen_id3.TCON
COMM = mutagen_id3.COMM
APIC = mutagen_id3.APIC

_ORIGINAL_ID3 = ID3
_ORIGINAL_ID3NoHeaderError = ID3NoHeaderError
_ORIGINAL_TIT2 = TIT2
_ORIGINAL_TPE1 = TPE1
_ORIGINAL_TPE2 = TPE2
_ORIGINAL_TALB = TALB
_ORIGINAL_TRCK = TRCK
_ORIGINAL_TPOS = TPOS
_ORIGINAL_TDRC = TDRC
_ORIGINAL_TCON = TCON
_ORIGINAL_COMM = COMM
_ORIGINAL_APIC = APIC


def _id3_symbol(name: str):
    current = globals().get(name)
    fallback = getattr(mutagen_id3, name)
    original = globals().get(f"_ORIGINAL_{name}", fallback)
    if current is not None and current is not original:
        return current
    return fallback


_ARTWORK_CACHE_DIR = "artwork_cache"
# Artist images live in their own subdirectory of the artwork cache. Artist
# identity alone is the cache key there, so it can never collide with (or be
# mistaken for) a release entry keyed by artist + album / artist + title.
_ARTIST_ARTWORK_SUBDIR = "artists"
_MUSICBRAINZ_BASE = "https://musicbrainz.org/ws/2"
_COVERART_BASE = "https://coverartarchive.org"
_ITUNES_BASE = "https://itunes.apple.com/search"
_ARTIST_BASE = "https://api.deezer.com/search/artist"
_REQUEST_TIMEOUT = 15
_USER_AGENT = "spotm3u/0.1 (https://github.com/zk/spotm3u)"

# Bounded concurrency: album artwork lookups share one in-flight request per
# identity and only a few external HTTP requests may be open at once, no matter
# how many resolution workers are running.
_ARTWORK_MEMORY_LIMIT = 1024
_ARTWORK_MEMORY: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
_ARTWORK_INFLIGHT: dict[str, _PendingArtworkFetch] = {}
_ARTWORK_LOCK = threading.Lock()
_ARTWORK_FETCH_SEMAPHORE = threading.Semaphore(2)


class _PendingArtworkFetch:
    """A shared in-flight artwork fetch other threads can wait on."""

    __slots__ = ("event", "result", "done")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: tuple[bytes, str] | None = None
        self.done = False


@dataclass(frozen=True)
class MetadataResult:
    """Result of metadata enrichment operation."""

    path: Path
    artwork_embedded: bool
    artwork_source: str | None
    fields_written: tuple[str, ...]
    errors: tuple[str, ...]
    artist_artwork_embedded: bool = False
    artist_artwork_source: str | None = None


@dataclass(frozen=True)
class ArtworkCandidate:
    """An album artwork candidate with metadata."""

    url: str
    mime_type: str
    source: str
    width: int | None = None
    height: int | None = None
    artist: str | None = None
    album: str | None = None
    title: str | None = None


class MetadataError(RuntimeError):
    """Raised when metadata enrichment fails fatally (rare)."""


def _normalize_identity(value: str | None) -> str:
    """Normalize human-readable metadata for comparison."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("&", " and ")
    text = text.replace("–", "-").replace("—", "-")
    text = text.casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def _identity_score(left: str, right: str) -> float:
    """Score how closely two normalized identities match, 0 to 100."""
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 100.0
    return SequenceMatcher(None, left, right).ratio() * 100.0


def _artist_album_match(
    artist: str | None, album: str | None, candidate_artist: str | None, candidate_album: str | None
) -> bool:
    """Return True when artist+album metadata appear to match, ignoring punctuation/casing noise."""
    artist_key = _normalize_identity(artist)
    album_key = _normalize_identity(_normalize_album_for_search(album) if album else album)
    candidate_artist_key = _normalize_identity(candidate_artist)
    candidate_album_key = _normalize_identity(
        _normalize_album_for_search(candidate_album) if candidate_album else candidate_album
    )
    if not artist_key or not album_key or not candidate_artist_key or not candidate_album_key:
        return False
    artist_score = _identity_score(artist_key, candidate_artist_key)
    album_score = _identity_score(album_key, candidate_album_key)
    return artist_score >= 80 and album_score >= 70


def _artist_title_match(
    artist: str | None, title: str | None, candidate_artist: str | None, candidate_title: str | None
) -> bool:
    """Return True when artist+title identity matches, with artist as the dominant constraint.

    Used only for the song-title artwork fallback, so common song titles still
    require a strongly matching artist identity.
    """
    artist_key = _normalize_identity(artist)
    title_key = _normalize_identity(title)
    candidate_artist_key = _normalize_identity(candidate_artist)
    candidate_title_key = _normalize_identity(candidate_title)
    if not artist_key or not title_key or not candidate_artist_key or not candidate_title_key:
        return False
    artist_score = _identity_score(artist_key, candidate_artist_key)
    title_score = _identity_score(title_key, candidate_title_key)
    return artist_score >= 80 and title_score >= 70


def _has_reliable_album(album: str | None) -> bool:
    """Return True when ``album`` is present and not clearly invalid."""
    if not album:
        return False
    return bool(_normalize_identity(_normalize_album_for_search(album)))


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


def _write_artwork_source(download_dir: Path, cache_key: str, source: str) -> None:
    """Record where a cached artwork entry came from.

    Verified release lookups record a trusted source; local embedded/sidecar
    art records an ``embedded:`` / ``sidecar:`` prefix so it can be re-verified
    against the release on a later networked run instead of being trusted.
    """
    _write_cached_source(_cache_dir(download_dir), cache_key, source)


# Cache sources that are authoritative for the identity because they came from
# a verified external release lookup, not from the local audio file itself.
_ARTWORK_TRUSTED_SOURCES = frozenset({"coverartarchive", "itunes", "itunes-song"})

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


def _normalize_album_for_search(album: str) -> str:
    """Normalize album title for search queries."""
    value = unicodedata.normalize("NFKC", album)
    value = re.sub(
        r"\s*[\(\[]\s*(?:deluxe|expanded|remaster(?:ed)?|anniversary|"
        r"edition|explicit|clean|bonus|special|mono|stereo).*?[\)\]]",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", value).strip()


def _search_musicbrainz_release(artist: str, album: str) -> list[dict[str, Any]]:
    """Search MusicBrainz for release groups matching artist and album."""
    query_parts: list[str] = []
    if artist:
        query_parts.append(f"artist:{requests.utils.quote(artist)}")
    if album:
        query_parts.append(f"release:{requests.utils.quote(_normalize_album_for_search(album))}")
    query = " AND ".join(query_parts)
    url = f"{_MUSICBRAINZ_BASE}/release-group"
    params = {"query": query, "fmt": "json", "limit": 10}
    headers = {"User-Agent": _USER_AGENT}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("release-groups", [])
    except (requests.RequestException, ValueError) as exc:
        logger.debug("MusicBrainz search failed artist=%s album=%s: %s", artist, album, exc)
        return []


def _get_coverart_candidates(release_group_id: str) -> list[ArtworkCandidate]:
    """Fetch artwork candidates from Cover Art Archive for a release group."""
    url = f"{_COVERART_BASE}/release-group/{release_group_id}"
    headers = {"User-Agent": _USER_AGENT}

    try:
        resp = requests.get(url, headers=headers, timeout=_REQUEST_TIMEOUT)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Cover Art Archive lookup failed rgid=%s: %s", release_group_id, exc)
        return []

    candidates: list[ArtworkCandidate] = []
    for image in data.get("images", []):
        if not image.get("front", False):
            continue
        image_url = image.get("image")
        if not image_url:
            continue
        mime_type = image.get("mime-type") or "image/jpeg"
        if mime_type not in {"image/jpeg", "image/png"}:
            continue
        candidates.append(
            ArtworkCandidate(
                url=image_url,
                mime_type=mime_type,
                source="coverartarchive",
                width=image.get("width"),
                height=image.get("height"),
            )
        )
    return candidates


def _search_itunes_artwork(artist: str, album: str, title: str = "") -> list[ArtworkCandidate]:
    """Search Apple's public iTunes catalog for album artwork."""
    queries = [
        {"term": f"{artist} {album}", "entity": "album"},
        {"term": f"{artist} {title}", "entity": "song"},
    ]
    candidates: list[ArtworkCandidate] = []
    for params in queries:
        if not params["term"].strip():
            continue
        try:
            response = requests.get(
                _ITUNES_BASE,
                params={**params, "limit": 25, "media": "music"},
                headers={"User-Agent": _USER_AGENT},
                timeout=_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
        except (requests.RequestException, ValueError, AttributeError) as exc:
            logger.debug("iTunes artwork lookup failed artist=%s album=%s: %s", artist, album, exc)
            continue
        for result in results:
            result_artist = result.get("artistName")
            result_album = result.get("collectionName") or result.get("albumName")
            result_title = result.get("trackName")
            if not _artist_album_match(artist, album, result_artist, result_album):
                continue
            image_url = result.get("artworkUrl100") or result.get("artworkUrl60")
            if not image_url:
                continue
            candidates.append(
                ArtworkCandidate(
                    url=re.sub(r"\b\d+x\d+bb\b", "1200x1200bb", image_url),
                    mime_type="image/jpeg",
                    source="itunes",
                    width=1200,
                    height=1200,
                    artist=result_artist,
                    album=result_album,
                    title=result_title,
                )
            )
    return candidates


def _search_itunes_song_artwork(artist: str, title: str) -> list[ArtworkCandidate]:
    """Search Apple's public iTunes catalog for song artwork (album fallback)."""
    term = f"{artist} {title}".strip()
    if not term:
        return []
    try:
        response = requests.get(
            _ITUNES_BASE,
            params={"term": term, "entity": "song", "limit": 25, "media": "music"},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except (requests.RequestException, ValueError, AttributeError) as exc:
        logger.debug("iTunes song artwork lookup failed artist=%s title=%s: %s", artist, title, exc)
        return []

    candidates: list[ArtworkCandidate] = []
    for result in results:
        result_artist = result.get("artistName")
        result_title = result.get("trackName")
        if not _artist_title_match(artist, title, result_artist, result_title):
            continue
        image_url = result.get("artworkUrl100") or result.get("artworkUrl60")
        if not image_url:
            continue
        candidates.append(
            ArtworkCandidate(
                url=re.sub(r"\b\d+x\d+bb\b", "1200x1200bb", image_url),
                mime_type="image/jpeg",
                source="itunes-song",
                width=1200,
                height=1200,
                artist=result_artist,
                album=result.get("collectionName") or result.get("albumName"),
                title=result_title,
            )
        )
    return candidates


def _artwork_memory_key(download_dir: Path, cache_key: str) -> str:
    """Scope the in-process memo to the cache directory so distinct libraries
    never share identity results."""
    return f"{str(_cache_dir(download_dir))}::{cache_key}"


def _deduplicated_artwork_fetch(
    download_dir: Path, cache_key: str, fetch: Callable[[], tuple[bytes | None, str | None]]
) -> tuple[bytes | None, str | None]:
    """Fetch artwork once per identity while concurrent workers wait and reuse.

    The first caller performs the network lookup (under a bounded semaphore);
    concurrent callers for the same identity join the in-flight request instead
    of repeating it. Positive results are memoized process-wide (per cache
    directory) so tracks from the same release share one lookup.
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


def _select_best_artwork(candidates: list[ArtworkCandidate]) -> ArtworkCandidate | None:
    """Select the highest quality artwork candidate."""
    if not candidates:
        return None
    scored = []
    for candidate in candidates:
        area = (candidate.width or 0) * (candidate.height or 0)
        scored.append((area, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _download_artwork(url: str) -> bytes | None:
    """Download artwork image data."""
    headers = {"User-Agent": _USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "").split(";", 1)[0].lower()
        data = resp.content
        if not (
            content_type in {"image/jpeg", "image/png", "image/webp"}
            or data.startswith(b"\xff\xd8\xff")
            or data.startswith(b"\x89PNG\r\n\x1a\n")
            or data.startswith(b"RIFF")
            and data[8:12] == b"WEBP"
        ):
            return None
        return data
    except (requests.RequestException, ValueError, AttributeError) as exc:
        logger.debug("Artwork download failed url=%s: %s", url, exc)
        return None


def _image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


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


# Artist profile images are not available from the release-oriented artwork
# services (MusicBrainz / Cover Art Archive / iTunes return no artist image).
# Spotify itself is off limits: the project never uses the Spotify Web API and
# never scrapes Spotify's site. The artist identity from the export is therefore
# looked up in Deezer's public catalog, which serves profile images without auth.
_ARTIST_ARTWORK_SOURCES = frozenset({"deezer-artist"})

# Deezer picture fields, largest first. The largest available image is embedded
# because ID3 APIC data is stored at full size in the file.
_DEEZER_PICTURE_FIELDS: tuple[tuple[str, int], ...] = (
    ("picture_xl", 1000),
    ("picture_big", 500),
    ("picture_medium", 250),
    ("picture_small", 56),
)


def _artist_cache_key(artist: str) -> str:
    """Generate a stable cache key for one artist's profile image."""
    safe = re.sub(r"[^a-z0-9]+", "_", _normalize_identity(artist)).strip("_")
    return f"artist_{safe[:110] or 'unknown'}"


def _search_deezer_artist_artwork(artist: str) -> ArtworkCandidate | None:
    """Find one artist's profile image in Deezer's public catalog.

    Only results whose artist identity matches strongly are accepted, and an
    exact name always outranks a substring match so a tribute act or a
    compilation page never replaces the requested artist.
    """
    try:
        response = requests.get(
            _ARTIST_BASE,
            params={"q": artist, "limit": 25},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json().get("data", [])
    except (requests.RequestException, ValueError, AttributeError) as exc:
        logger.debug("Artist artwork lookup failed artist=%s: %s", artist, exc)
        return None

    target = _normalize_identity(artist)
    best: tuple[tuple[int, float, int], ArtworkCandidate] | None = None
    for result in results:
        if not isinstance(result, dict):
            continue
        name = _normalize_identity(result.get("name"))
        score = _identity_score(target, name)
        if not name or score < 80.0:
            continue
        picture = next(
            (
                (result[field], size)
                for field, size in _DEEZER_PICTURE_FIELDS
                if isinstance(result.get(field), str) and result[field]
            ),
            None,
        )
        if picture is None:
            continue
        url, size = picture
        rank = (1 if name == target else 0, score, size)
        if best is None or rank > best[0]:
            best = (
                rank,
                ArtworkCandidate(
                    url=url,
                    mime_type="image/jpeg",
                    source="deezer-artist",
                    width=size,
                    height=size,
                    artist=result.get("name"),
                ),
            )
    return best[1] if best is not None else None


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


def _write_all_metadata(path: Path, track: Track) -> tuple[str, ...]:
    """Write all ID3 metadata to MP3 file and save. Returns list of fields written."""
    written: list[str] = []

    ID3_cls = _id3_symbol("ID3")
    ID3NoHeaderError_cls = _id3_symbol("ID3NoHeaderError")
    TIT2_cls = _id3_symbol("TIT2")
    TPE1_cls = _id3_symbol("TPE1")
    TPE2_cls = _id3_symbol("TPE2")
    TALB_cls = _id3_symbol("TALB")
    TRCK_cls = _id3_symbol("TRCK")
    TPOS_cls = _id3_symbol("TPOS")
    TDRC_cls = _id3_symbol("TDRC")
    TCON_cls = _id3_symbol("TCON")
    COMM_cls = _id3_symbol("COMM")

    try:
        tags = ID3_cls(str(path))
    except ID3NoHeaderError_cls:
        tags = ID3_cls()

    def clear_frame(frame_key: str) -> None:
        if hasattr(tags, "delall"):
            tags.delall(frame_key)

    if track.title:
        clear_frame("TIT2")
        tags["TIT2"] = TIT2_cls(encoding=3, text=[track.title])
        written.append("TIT2")

    if track.artists:
        clear_frame("TPE1")
        tags["TPE1"] = TPE1_cls(encoding=3, text=track.artists)
        written.append("TPE1")

    if track.album:
        clear_frame("TALB")
        tags["TALB"] = TALB_cls(encoding=3, text=[track.album])
        written.append("TALB")

    album_artist = track.album_artist or (track.artists[0] if track.artists else None)
    if album_artist:
        clear_frame("TPE2")
        tags["TPE2"] = TPE2_cls(encoding=3, text=[album_artist])
        written.append("TPE2")

    track_number = track.track_number if track.track_number is not None else 1
    try:
        clear_frame("TRCK")
        tags["TRCK"] = TRCK_cls(encoding=3, text=[str(track_number)])
        written.append("TRCK")
    except (ValueError, TypeError):
        pass

    disc_number = track.disc_number if track.disc_number is not None else 1
    try:
        clear_frame("TPOS")
        tags["TPOS"] = TPOS_cls(encoding=3, text=[str(disc_number)])
        written.append("TPOS")
    except (ValueError, TypeError):
        pass

    if track.release_year is not None:
        try:
            clear_frame("TDRC")
            tags["TDRC"] = TDRC_cls(encoding=3, text=[str(track.release_year)])
            written.append("TDRC")
        except (ValueError, TypeError):
            pass

    if track.genre:
        try:
            clear_frame("TCON")
            tags["TCON"] = TCON_cls(encoding=3, text=[track.genre])
            written.append("TCON")
        except (ValueError, TypeError):
            pass

    if track.comments:
        try:
            clear_frame("COMM")
            tags["COMM"] = COMM_cls(encoding=3, lang="eng", desc="", text=[track.comments])
            written.append("COMM")
        except (ValueError, TypeError):
            pass

    try:
        tags.save(str(path), v2_version=3)
    except (OSError, ValueError) as exc:
        logger.warning("Failed to save metadata path=%s: %s", path, exc)

    return tuple(written)


def _remove_artwork_frames(tags, picture_type: int, description: str) -> None:
    """Remove existing APIC frames of one picture type before a replacement.

    Only frames of the same picture type are removed, so a front cover
    (type 3) and an artist picture (type 8) can coexist in one file without
    overwriting each other.
    """
    if hasattr(tags, "keys") and hasattr(tags, "get"):
        for key in list(tags.keys()):
            frame = tags.get(key)
            if frame is None or getattr(frame, "type", None) != picture_type:
                continue
            try:
                del tags[key]
            except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
                continue
        return
    if hasattr(tags, "delall"):  # pragma: no cover - lightweight tag doubles
        tags.delall(f"APIC:{description}")


def _order_front_cover_first(tags) -> None:
    """Keep the front-cover APIC frame first among the file's pictures.

    Many players use the first picture as the cover image. Replacing a cover
    re-inserts it, which would otherwise push it behind an artist picture, so
    the APIC frames are rewritten with the front cover first.
    """
    if not (hasattr(tags, "keys") and hasattr(tags, "get")):
        return  # pragma: no cover - lightweight tag doubles
    try:
        entries = [
            (key, tags.get(key)) for key in list(tags.keys()) if str(key).split(":", 1)[0] == "APIC"
        ]
    except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
        return
    frames = [frame for _key, frame in entries if frame is not None]
    covers = [frame for frame in frames if getattr(frame, "type", None) == 3]
    if not covers or frames[0] in covers:
        return
    ordered = covers + [frame for frame in frames if frame not in covers]
    for key, _frame in entries:
        try:
            del tags[key]
        except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
            continue
    for frame in ordered:
        tags[getattr(frame, "HashKey", None) or "APIC"] = frame


def _embed_artwork(
    path: Path,
    artwork_data: bytes,
    mime_type: str = "image/jpeg",
    *,
    picture_type: int = 3,
    description: str = "Cover",
) -> bool:
    """Embed artwork as an APIC frame in an MP3.

    ``picture_type`` follows the ID3v2 picture types: 3 is the front cover and
    8 is the performing artist. Frames of the same type are replaced; frames of
    every other type (an album cover, an existing artist picture) are left
    untouched, so album and artist artwork coexist in the same file.
    """
    ID3_cls = _id3_symbol("ID3")
    ID3NoHeaderError_cls = _id3_symbol("ID3NoHeaderError")
    APIC_cls = _id3_symbol("APIC")

    try:
        tags = ID3_cls(str(path))
    except ID3NoHeaderError_cls:
        tags = ID3_cls()

    apic = APIC_cls(
        encoding=3,
        mime=mime_type,
        type=picture_type,
        desc=description,
        data=artwork_data,
    )
    _remove_artwork_frames(tags, picture_type, description)
    tags[getattr(apic, "HashKey", None) or f"APIC:{description}"] = apic
    _order_front_cover_first(tags)

    try:
        tags.save(str(path), v2_version=3)
        return True
    except (OSError, ValueError) as exc:
        logger.warning("Failed to embed artwork path=%s: %s", path, exc)
        return False


def _embed_artist_artwork(
    audio_path: Path,
    download_dir: Path,
    artist: str,
    errors: list[str],
) -> tuple[bool, str | None]:
    """Embed the artist's profile image as an ID3 artist picture (APIC type 8).

    Returns ``(embedded, source)``. Artist artwork is optional enrichment: a
    missing, invalid or unavailable image is recorded as a non-fatal error and
    never propagates an exception or fails the track.
    """
    try:
        artist_data, artist_source = _find_artist_artwork(download_dir, artist)
    except Exception as exc:  # pragma: no cover - defensive behavior
        logger.warning("Artist artwork lookup failed path=%s: %s", audio_path, exc)
        errors.append(f"artist artwork failed: {exc}")
        return False, None

    if not artist_data:
        errors.append("artist artwork not found")
        return False, None

    try:
        embedded = _embed_artwork(
            audio_path,
            artist_data,
            _image_mime(artist_data),
            picture_type=8,
            description="Artist",
        )
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("Artist artwork embed failed path=%s: %s", audio_path, exc)
        embedded = False
    if embedded:
        return True, artist_source
    errors.append("artist artwork embed failed")
    return False, None


def enrich_metadata(
    path: str | Path | list[Track] | tuple[Track, ...],
    track: Track | list[Track] | tuple[Track, ...] | None,
    download_dir: str | Path,
) -> MetadataResult | list[MetadataResult]:
    """Enrich one track or a batch of tracks with artwork and ID3 metadata.

    Accepts both the single-track form and the batch form used by the tests.
    """
    if isinstance(path, (list, tuple)):
        tracks = path
        resolved_paths = list(track) if isinstance(track, (list, tuple)) else list(track or ())
        if len(tracks) != len(resolved_paths):
            raise ValueError("track and path counts do not match")
        return [
            enrich_metadata(item_path, item_track, download_dir)
            for item_track, item_path in zip(tracks, resolved_paths, strict=True)
        ]

    audio_path = Path(path)
    download_path = Path(download_dir)

    if not audio_path.is_file():
        return MetadataResult(
            path=audio_path,
            artwork_embedded=False,
            artwork_source=None,
            fields_written=(),
            errors=(f"audio file not found: {audio_path}",),
        )

    errors: list[str] = []
    fields_written: list[str] = []
    artwork_embedded = False
    artwork_source: str | None = None
    artist_artwork_embedded = False
    artist_artwork_source: str | None = None

    try:
        fields_written.extend(_write_all_metadata(audio_path, track))
    except Exception as exc:  # pragma: no cover - defensive behavior
        errors.append(f"metadata write failed: {exc}")

    if track.artists:
        primary_artist = track.album_artist or track.artists[0]
        artwork_data, source = _find_album_artwork(
            download_path,
            primary_artist,
            track.album,
            track.title,
            audio_path,
        )
        if artwork_data:
            try:
                embedded = _embed_artwork(audio_path, artwork_data, _image_mime(artwork_data))
            except (OSError, ValueError, RuntimeError) as exc:
                logger.warning("Artwork embed failed path=%s: %s", audio_path, exc)
                embedded = False
            if embedded:
                artwork_embedded = True
                artwork_source = source
            else:
                errors.append("artwork embed failed")
        elif source is not None:
            errors.append(f"artwork not found: {source}")
        else:
            errors.append("artwork not found: unknown")
    else:
        errors.append("missing album/artist for artwork lookup")

    if _ARTIST_ARTWORK_ENABLED and track.artists:
        artist_artwork_embedded, artist_artwork_source = _embed_artist_artwork(
            audio_path, download_path, track.artists[0], errors
        )

    return MetadataResult(
        path=audio_path,
        artwork_embedded=artwork_embedded,
        artwork_source=artwork_source,
        fields_written=tuple(fields_written),
        errors=tuple(errors),
        artist_artwork_embedded=artist_artwork_embedded,
        artist_artwork_source=artist_artwork_source,
    )


def enrich_metadata_batch(
    tracks: list[Track], resolved_paths: list[Path], download_dir: str | Path
) -> list[MetadataResult]:
    """Enrich metadata for multiple tracks, reusing artwork cache."""
    results: list[MetadataResult] = []
    for track, path in zip(tracks, resolved_paths, strict=True):
        result = enrich_metadata(path, track, download_dir)
        results.append(result)
    return results


def artwork_artist(track: Track) -> str | None:
    """Return the artist identity to use for artwork lookups.

    Prefers the explicit album artist and otherwise the first structured
    artist. Collaborations are never joined into one malformed string.
    """
    if not track.artists:
        return None
    return track.album_artist or track.artists[0]


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
    "MetadataResult",
    "ArtworkCandidate",
    "MetadataError",
    "artist_artwork_enabled",
    "artwork_artist",
    "cached_artwork_path",
    "set_artist_artwork_enabled",
    "_artist_album_match",
    "_normalize_identity",
    "enrich_metadata",
    "enrich_metadata_batch",
]
