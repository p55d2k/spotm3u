"""Metadata and artwork enrichment for resolved audio files."""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests

import mutagen.id3 as mutagen_id3

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
_CACHE_LOCK = threading.Lock()
_MUSICBRAINZ_BASE = "https://musicbrainz.org/ws/2"
_COVERART_BASE = "https://coverartarchive.org"
_ITUNES_BASE = "https://itunes.apple.com/search"
_REQUEST_TIMEOUT = 15
_USER_AGENT = "spotm3u/0.1 (https://github.com/zk/spotm3u)"


@dataclass(frozen=True)
class MetadataResult:
    """Result of metadata enrichment operation."""

    path: Path
    artwork_embedded: bool
    artwork_source: str | None
    fields_written: tuple[str, ...]
    errors: tuple[str, ...]


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


def _artist_album_match(artist: str | None, album: str | None, candidate_artist: str | None, candidate_album: str | None) -> bool:
    """Return True when artist+album metadata appear to match, ignoring punctuation/casing noise."""
    artist_key = _normalize_identity(artist)
    album_key = _normalize_identity(
        _normalize_album_for_search(album) if album else album
    )
    candidate_artist_key = _normalize_identity(candidate_artist)
    candidate_album_key = _normalize_identity(
        _normalize_album_for_search(candidate_album) if candidate_album else candidate_album
    )
    if not artist_key or not album_key or not candidate_artist_key or not candidate_album_key:
        return False

    def score(left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        if left in right or right in left:
            return 100.0
        return SequenceMatcher(None, left, right).ratio() * 100.0

    artist_score = score(artist_key, candidate_artist_key)
    album_score = score(album_key, candidate_album_key)
    return artist_score >= 80 and album_score >= 70


def _cache_dir(download_dir: Path) -> Path:
    cache_path = download_dir / _ARTWORK_CACHE_DIR
    cache_path.mkdir(parents=True, exist_ok=True)
    return cache_path


def _cache_key(artist: str, album: str) -> str:
    """Generate a stable cache key from artist and album."""
    normalized = _normalize_identity(artist) + "||" + _normalize_identity(
        _normalize_album_for_search(album)
    )
    safe = re.sub(r"[^a-z0-9]+", "_", normalized.casefold()).strip("_")
    return safe[:120] or "unknown"


def _cached_artwork_path(cache_dir: Path, artist: str, album: str) -> Path:
    return cache_dir / f"{_cache_key(artist, album)}.jpg"


def _load_cached_artwork(download_dir: Path, artist: str, album: str) -> bytes | None:
    cache_dir = _cache_dir(download_dir)
    path = _cached_artwork_path(cache_dir, artist, album)
    if path.is_file():
        try:
            return path.read_bytes()
        except OSError:
            return None
    return None


def _save_cached_artwork(download_dir: Path, artist: str, album: str, data: bytes) -> None:
    cache_dir = _cache_dir(download_dir)
    path = _cached_artwork_path(cache_dir, artist, album)
    try:
        path.write_bytes(data)
    except OSError:
        pass


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
            or data.startswith(b"RIFF") and data[8:12] == b"WEBP"
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
    """Return an existing front-cover APIC frame, if the source already has one."""
    try:
        tags = mutagen_id3.ID3(str(path))
    except (mutagen_id3.ID3NoHeaderError, OSError, ValueError):
        return None
    covers = [frame for frame in tags.getall("APIC") if getattr(frame, "type", None) == 3]
    if not covers:
        covers = tags.getall("APIC")
    if not covers:
        return None
    frame = covers[0]
    mime = getattr(frame, "mime", "image/jpeg")
    return frame.data, mime


def _sidecar_artwork(path: Path) -> tuple[bytes, str] | None:
    """Read artwork saved alongside an audio download by a source tool."""
    for suffix, mime in ((".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
                         (".png", "image/png"), (".webp", "image/webp")):
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
    """Find and download album artwork, using cache."""
    # Older versions wrote permanent negative-cache markers. Remove only the
    # marker for this identity so improved lookup logic gets a fresh attempt.
    stale_failure = _cache_dir(download_dir) / f"{_cache_key(artist, album)}.failed"
    try:
        stale_failure.unlink(missing_ok=True)
    except OSError:
        pass

    if audio_path is not None:
        embedded = _embedded_artwork(audio_path)
        if embedded is not None:
            data, mime = embedded
            _save_cached_artwork(download_dir, artist, album, data)
            return data, f"embedded:{mime}"
        sidecar = _sidecar_artwork(audio_path)
        if sidecar is not None:
            data, mime = sidecar
            _save_cached_artwork(download_dir, artist, album, data)
            return data, f"sidecar:{mime}"

    cached = _load_cached_artwork(download_dir, artist, album)
    if cached is not None:
        logger.debug("Artwork cache hit artist=%s album=%s", artist, album)
        return cached, "cache"

    releases = _search_musicbrainz_release(artist, album)
    candidates: list[ArtworkCandidate] = []
    for release in releases:
        release_artist = release.get("artist-credit-phrase")
        release_album = release.get("title")
        if release_artist and release_album and not _artist_album_match(
            artist, album, release_artist, release_album
        ):
            continue
        rg_id = release.get("id")
        if rg_id:
            candidates.extend(_get_coverart_candidates(rg_id))
    candidates.extend(_search_itunes_artwork(artist, album, title))

    for candidate in sorted(
        candidates,
        key=lambda item: (item.width or 0) * (item.height or 0),
        reverse=True,
    ):
        data = _download_artwork(candidate.url)
        if data is not None:
            _save_cached_artwork(download_dir, artist, album, data)
            logger.info(
                "Artwork downloaded artist=%s album=%s source=%s",
                artist,
                album,
                candidate.source,
            )
            return data, candidate.source

    # Negative results are deliberately not persisted. A later run may have
    # network access or a newly indexed release/artwork source.
    logger.info("Artwork not found artist=%s album=%s", artist, album)
    return None, "not-found"


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


def _embed_artwork(path: Path, artwork_data: bytes, mime_type: str = "image/jpeg") -> bool:
    """Embed artwork as APIC frame in MP3."""
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
        type=3,
        desc="Cover",
        data=artwork_data,
    )
    if hasattr(tags, "delall"):
        tags.delall("APIC")
    tags["APIC"] = apic

    try:
        tags.save(str(path), v2_version=3)
        return True
    except (OSError, ValueError) as exc:
        logger.warning("Failed to embed artwork path=%s: %s", path, exc)
        return False


def enrich_metadata(path: str | Path | list[Track] | tuple[Track, ...], track: Track | list[Track] | tuple[Track, ...] | None, download_dir: str | Path) -> MetadataResult | list[MetadataResult]:
    """Enrich one track or a batch of tracks with artwork and ID3 metadata.

    Accepts both the single-track form and the batch form used by the tests.
    """
    if isinstance(path, (list, tuple)):
        tracks = path
        resolved_paths = list(track) if isinstance(track, (list, tuple)) else list(track or ())
        if len(tracks) != len(resolved_paths):
            raise ValueError("track and path counts do not match")
        return [enrich_metadata(item_path, item_track, download_dir) for item_track, item_path in zip(tracks, resolved_paths)]

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

    try:
        fields_written.extend(_write_all_metadata(audio_path, track))
    except Exception as exc:  # pragma: no cover - defensive behavior
        errors.append(f"metadata write failed: {exc}")

    if track.album and track.artists:
        primary_artist = track.artists[0]
        artwork_data, source = _find_album_artwork(
            download_path,
            primary_artist,
            track.album,
            track.title,
            audio_path,
        )
        if artwork_data:
            try:
                embedded = _embed_artwork(
                    audio_path, artwork_data, _image_mime(artwork_data)
                )
            except (OSError, ValueError, RuntimeError) as exc:
                logger.warning("Artwork embed failed path=%s: %s", audio_path, exc)
                embedded = False
            if embedded:
                artwork_embedded = True
                artwork_source = source
            else:
                errors.append("artwork embed failed")
        else:
            if source is not None:
                errors.append(f"artwork not found: {source}")
            else:
                errors.append("artwork not found: unknown")
    else:
        errors.append("missing album/artist for artwork lookup")

    return MetadataResult(
        path=audio_path,
        artwork_embedded=artwork_embedded,
        artwork_source=artwork_source,
        fields_written=tuple(fields_written),
        errors=tuple(errors),
    )


def enrich_metadata_batch(
    tracks: list[Track], resolved_paths: list[Path], download_dir: str | Path
) -> list[MetadataResult]:
    """Enrich metadata for multiple tracks, reusing artwork cache."""
    results: list[MetadataResult] = []
    for track, path in zip(tracks, resolved_paths):
        result = enrich_metadata(path, track, download_dir)
        results.append(result)
    return results


__all__ = [
    "MetadataResult",
    "ArtworkCandidate",
    "MetadataError",
    "_artist_album_match",
    "_normalize_identity",
    "enrich_metadata",
    "enrich_metadata_batch",
]