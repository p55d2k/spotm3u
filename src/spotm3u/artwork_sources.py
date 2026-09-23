"""Remote artwork providers and the candidate matching behind them.

MusicBrainz / Cover Art Archive / iTunes / Deezer lookups live here together with
the candidate model they return and the identity matching that decides which
candidate is trustworthy. :mod:`spotm3u.artwork` orchestrates these calls and
owns caching, local artwork, and embedding.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import requests

logger = logging.getLogger(__name__)

_MUSICBRAINZ_BASE = "https://musicbrainz.org/ws/2"


_COVERART_BASE = "https://coverartarchive.org"


_ITUNES_BASE = "https://itunes.apple.com/search"


_ARTIST_BASE = "https://api.deezer.com/search/artist"


_ARTIST_API_BASE = "https://api.deezer.com/artist"


_REQUEST_TIMEOUT = 15


_USER_AGENT = "spotm3u/0.1 (https://github.com/zk/spotm3u)"


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


# Cache sources that are authoritative for the identity because they came from
# a verified external release lookup, not from the local audio file itself.
_ARTWORK_TRUSTED_SOURCES = frozenset({"coverartarchive", "itunes", "itunes-song"})


def set_artwork_request_timeout(seconds: int) -> None:
    """Set the HTTP timeout used for artwork lookups.

    Configured through ``artwork.request_timeout`` in config.toml; slow or
    unreachable artwork providers should not stall a job indefinitely.
    """
    global _REQUEST_TIMEOUT
    _REQUEST_TIMEOUT = int(seconds)


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


# Artist profile images are not available from the release-oriented artwork
# services (MusicBrainz / Cover Art Archive / iTunes return no artist image).
# Spotify itself is off limits: the project never uses the Spotify Web API and
# never scrapes Spotify's site. The artist identity from the export is therefore
# looked up in Deezer's public catalog, which serves profile images without auth.
# How many of the most popular exact-name artists are checked against the
# track's album/title before settling for popularity alone.
_ARTIST_VERIFICATION_LIMIT = 3


# Artist images are recorded with the Deezer artist id they were taken from
# (``deezer-artist:<id>``) so a wrong match can be traced to the artist it
# actually belongs to. Markers written before ids were recorded carried only
# the bare provider name and may be any namesake's image, so they are
# re-verified instead of trusted.
_ARTIST_ARTWORK_SOURCE = "deezer-artist"


def _is_trusted_artist_source(value: str | None) -> bool:
    """True when a cached artist image was resolved to a specific Deezer artist."""
    if not value:
        return False
    provider, separator, artist_id = value.partition(":")
    return bool(separator) and provider == _ARTIST_ARTWORK_SOURCE and artist_id.isdigit()


# Deezer picture fields, largest first. The largest available image is embedded
# because ID3 APIC data is stored at full size in the file.
_DEEZER_PICTURE_FIELDS: tuple[tuple[str, int], ...] = (
    ("picture_xl", 1000),
    ("picture_big", 500),
    ("picture_medium", 250),
    ("picture_small", 56),
)


def _deezer_picture(record: dict[str, Any]) -> tuple[str, int] | None:
    """Return the largest available picture on one Deezer record, or None."""
    for field, size in _DEEZER_PICTURE_FIELDS:
        url = record.get(field)
        if isinstance(url, str) and url:
            return url, size
    return None


def _deezer_artist_rank(record: dict[str, Any]) -> tuple[int, int, int]:
    """Rank one Deezer artist record by how likely it is the requested artist."""
    return (
        int(record.get("nb_fan") or 0),
        int(record.get("nb_album") or 0),
        int(record["id"]),
    )


def _deezer_artist_has_release(artist_id: int, *, album: str | None, title: str | None) -> bool:
    """True when Deezer lists a release by this artist matching the track's album/title.

    Deezer's artist releases include albums and singles, so an album-less track can
    still be confirmed through a single carrying the track's title. Anything that
    prevents the question from being answered -- a failed request, an unusable
    response, no album/title to check -- reports False, which the caller reads as
    "not confirmed" rather than as proof of a wrong artist.
    """
    album_key = _normalize_identity(_normalize_album_for_search(album)) if album else ""
    title_key = _normalize_identity(title) if title else ""
    if not album_key and not title_key:
        return False
    try:
        response = requests.get(
            f"{_ARTIST_API_BASE}/{artist_id}/albums",
            params={"limit": 100},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        releases = response.json().get("data", [])
    except (requests.RequestException, ValueError, AttributeError) as exc:
        logger.debug("Artist releases lookup failed deezer_id=%s: %s", artist_id, exc)
        return False
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict):
            continue
        release_key = _normalize_identity(
            _normalize_album_for_search(str(release.get("title") or ""))
        )
        if release_key and release_key in {album_key, title_key}:
            return True
    return False


def _pick_deezer_artist(
    results: Any,
    target: str,
    *,
    album: str | None = None,
    title: str | None = None,
) -> dict[str, Any] | None:
    """Pick the Deezer artist record that is ``target``, or None.

    The name must match exactly once normalized: a partial name is not evidence
    of identity, and Deezer's ranked search happily returns 'Adèle & Zalem',
    'Adele & Andy' and 'Mortelle Adèle' ahead of the requested artist. Namesakes
    are common too -- ``q=adele`` returns four different artists named exactly
    'Adele' -- so the most popular of the exact matches wins rather than the
    first one, which is an unrelated act with a few hundred fans. Album count
    and artist id break any remaining tie, so the pick is deterministic.

    When the caller knows the track's album or title, the most popular exact
    matches are checked against that artist's Deezer releases first, so a
    namesake that fame alone cannot separate from the requested artist is
    rejected on evidence. That check is a preference, not a gate: an artist
    whose releases Deezer does not list, or cannot be reached, still wins on
    popularity exactly as before, so a lookup failure never drops a correct
    image.
    """
    if not isinstance(results, list):
        return None
    candidates = [
        record
        for record in results
        if isinstance(record, dict)
        and _normalize_identity(str(record.get("name") or "")) == target
        and str(record.get("id", "")).isdigit()
        and _deezer_picture(record) is not None
    ]
    if not candidates:
        return None
    candidates.sort(key=_deezer_artist_rank, reverse=True)
    # With one candidate there is nothing to choose between, so skip the check.
    if len(candidates) > 1 and (album or title):
        for record in candidates[:_ARTIST_VERIFICATION_LIMIT]:
            if _deezer_artist_has_release(int(record["id"]), album=album, title=title):
                return record
    return candidates[0]


def _search_deezer_artist_artwork(
    artist: str, *, album: str | None = None, title: str | None = None
) -> ArtworkCandidate | None:
    """Find one artist's profile image in Deezer's public catalog.

    The artist is resolved by exact name, then by the track's own album/title
    where Deezer can confirm it, then by popularity, instead of taking the first
    plausible hit (see :func:`_pick_deezer_artist`). The resolved artist id is
    part of the returned source so the image on disk can be traced back to the
    Deezer entity it came from.
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

    record = _pick_deezer_artist(results, _normalize_identity(artist), album=album, title=title)
    if record is None:
        return None

    picture = _deezer_picture(record)
    if picture is None:
        return None
    url, size = picture
    artist_id = int(record["id"])
    logger.debug(
        "Artist artwork resolved artist=%s deezer_id=%s name=%s fans=%s",
        artist,
        artist_id,
        record.get("name"),
        record.get("nb_fan"),
    )
    return ArtworkCandidate(
        url=url,
        mime_type="image/jpeg",
        source=f"{_ARTIST_ARTWORK_SOURCE}:{artist_id}",
        width=size,
        height=size,
        artist=record.get("name"),
    )


__all__ = [
    "ArtworkCandidate",
    "_artist_album_match",
    "_image_mime",
    "_normalize_album_for_search",
    "_normalize_identity",
    "set_artwork_request_timeout",
]
