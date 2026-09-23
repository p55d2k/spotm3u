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
from typing import Any, Literal

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
# resolved through MusicBrainz and Deezer's public catalog, which serves profile
# images without auth.

# How certain the identity behind an image is. Only ``verified`` is ever
# embedded; ``likely`` and ``unknown`` describe candidates that were found and
# then rejected, and exist so rejections can say why.
ArtistConfidence = Literal["verified", "likely", "unknown"]

# How many exact-name candidates have their Deezer releases checked. Each check
# is a request, and corroborating evidence -- not popularity -- is what decides.
_ARTIST_VERIFICATION_LIMIT = 3

# Deezer search hits kept as exact-name candidates, and MusicBrainz results
# scanned for an exact name match.
_ARTIST_SEARCH_LIMIT = 25
_MUSICBRAINZ_ARTIST_LIMIT = 10

# Artist images are recorded with the Deezer artist id they were taken from and
# the evidence that established the identity
# (``deezer-artist:<id>:verified:<evidence>``), so a wrong match can be traced
# to the artist it actually belongs to. Markers written by earlier releases
# recorded only the id -- authorized by name and popularity alone -- or nothing
# at all, so they are re-resolved instead of trusted.
_ARTIST_ARTWORK_SOURCE = "deezer-artist"
_VERIFIED_CONFIDENCE = "verified"

# Evidence slugs recorded in the marker, in the order the resolver tries them.
_EVIDENCE_MUSICBRAINZ_URL = "mb-artist-url"
_EVIDENCE_MUSICBRAINZ_ARTIST = "mb-artist"
_EVIDENCE_ALBUM = "deezer-release"
_EVIDENCE_TRACK = "deezer-track"
_EVIDENCE_SOLE_CANDIDATE = "sole-candidate"

# A Deezer artist page linked from MusicBrainz, e.g.
# ``https://www.deezer.com/artist/75798`` or a localized ``/en/artist/75798``.
_DEEZER_ARTIST_URL = re.compile(r"deezer\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?artist/(\d+)")

# Deezer picture fields, largest first. The largest available image is embedded
# because ID3 APIC data is stored at full size in the file.
_DEEZER_PICTURE_FIELDS: tuple[tuple[str, int], ...] = (
    ("picture_xl", 1000),
    ("picture_big", 500),
    ("picture_medium", 250),
    ("picture_small", 56),
)


@dataclass(frozen=True)
class ArtistIdentity:
    """An artist profile image whose identity was established by evidence.

    ``evidence`` names what established it and is recorded in the cache marker,
    so a stored image can be audited later without re-deriving the identity.
    """

    url: str
    size: int
    artist_id: int
    confidence: ArtistConfidence
    evidence: str
    name: str | None = None
    mbid: str | None = None

    @property
    def source(self) -> str:
        """Cache source marker recording the artist id and the evidence behind it."""
        return f"{_ARTIST_ARTWORK_SOURCE}:{self.artist_id}:{self.confidence}:{self.evidence}"


def _is_trusted_artist_source(value: str | None) -> bool:
    """True when a cached artist image was resolved to a verified artist identity.

    A marker that names an artist id but no verification came from a release that
    authorized images on name and popularity alone, so it is re-resolved rather
    than served: a wrong face is embedded permanently into the file.
    """
    if not value:
        return False
    parts = value.split(":")
    if len(parts) != 4:
        return False
    provider, artist_id, confidence, evidence = parts
    return (
        provider == _ARTIST_ARTWORK_SOURCE
        and artist_id.isdigit()
        and confidence == _VERIFIED_CONFIDENCE
        and bool(evidence)
    )


def _deezer_picture(record: dict[str, Any]) -> tuple[str, int] | None:
    """Return the largest available picture on one Deezer record, or None."""
    for field, size in _DEEZER_PICTURE_FIELDS:
        url = record.get(field)
        if isinstance(url, str) and url:
            return url, size
    return None


def _deezer_artist_rank(record: dict[str, Any]) -> tuple[int, int, int]:
    """Order Deezer artist records by popularity, then album count, then id.

    Popularity orders which candidates are worth checking, so the common case
    does not spend its requests on an obscure namesake. It never authorizes an
    image: see :func:`_resolve_artist_artwork`.
    """
    return (
        int(record.get("nb_fan") or 0),
        int(record.get("nb_album") or 0),
        int(record["id"]),
    )


def _deezer_get(url: str, params: dict[str, Any] | None = None) -> Any:
    """Fetch one Deezer resource, returning None instead of raising."""
    try:
        response = requests.get(
            url,
            params=params or {},
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError, AttributeError) as exc:
        logger.debug("Deezer lookup failed url=%s: %s", url, exc)
        return None


def _deezer_exact_name_candidates(artist: str) -> list[dict[str, Any]]:
    """Return exact-name Deezer artists offering a picture, most popular first.

    Only the exact normalized name counts: Deezer's ranked search returns
    ``Adèle & Zalem`` and ``Mortelle Adèle`` ahead of a requested ``Adele``, and
    a partial name is not identity. Deezer also returns several artists named
    exactly ``Adele``, so the caller has to establish which one is the artist
    behind the track rather than taking the first or the most popular hit.
    """
    results = _deezer_get(_ARTIST_BASE, {"q": artist, "limit": _ARTIST_SEARCH_LIMIT})
    data = results.get("data") if isinstance(results, dict) else None
    target = _normalize_identity(artist)
    candidates = [
        record
        for record in (data if isinstance(data, list) else [])
        if isinstance(record, dict)
        and _normalize_identity(str(record.get("name") or "")) == target
        and str(record.get("id", "")).isdigit()
        and _deezer_picture(record) is not None
    ]
    candidates.sort(key=_deezer_artist_rank, reverse=True)
    return candidates


def _deezer_artist_record(artist_id: int) -> dict[str, Any] | None:
    """Return one Deezer artist by id, or None when it cannot be read."""
    record = _deezer_get(f"{_ARTIST_API_BASE}/{artist_id}")
    if not isinstance(record, dict) or str(record.get("id")) != str(artist_id):
        return None
    return record


def _deezer_release_evidence(
    artist_id: int, *, album: str | None, title: str | None
) -> frozenset[str]:
    """Return which of the track's identities appear in this artist's Deezer releases.

    ``{_EVIDENCE_ALBUM}``, ``{_EVIDENCE_TRACK}`` or both. Deezer's artist releases
    include albums and singles, so an album-less track can still be confirmed
    through a single carrying its title. An unreadable release list reports no
    evidence: identity that cannot be shown is not identity, and guessing is what
    this function exists to prevent.
    """
    album_key = _normalize_identity(_normalize_album_for_search(album)) if album else ""
    title_key = _normalize_identity(title) if title else ""
    if not album_key and not title_key:
        return frozenset()
    data = _deezer_get(f"{_ARTIST_API_BASE}/{artist_id}/albums", {"limit": 100})
    releases = data.get("data") if isinstance(data, dict) else None
    evidence: set[str] = set()
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict):
            continue
        release_key = _normalize_identity(
            _normalize_album_for_search(str(release.get("title") or ""))
        )
        if not release_key:
            continue
        if album_key and release_key == album_key:
            evidence.add(_EVIDENCE_ALBUM)
        if title_key and release_key == title_key:
            evidence.add(_EVIDENCE_TRACK)
    return frozenset(evidence)


def _musicbrainz_get(entity: str, params: dict[str, Any]) -> dict[str, Any]:
    """Fetch one MusicBrainz entity, returning an empty mapping on any failure."""
    try:
        response = requests.get(
            f"{_MUSICBRAINZ_BASE}/{entity}",
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError, AttributeError) as exc:
        logger.debug("MusicBrainz %s lookup failed: %s", entity, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _musicbrainz_credit_phrase(entry: dict[str, Any]) -> str:
    """Return the credited artist name of one MusicBrainz entity.

    Search responses carry the credit as ``artist-credit`` entries whose ``name``
    holds the credited name and whose ``joinphrase`` holds the separator between
    collaborators; only some endpoints also include a ready-made
    ``artist-credit-phrase``. Both shapes are handled, so a missing phrase never
    turns a real MusicBrainz match into no match.
    """
    phrase = entry.get("artist-credit-phrase")
    if isinstance(phrase, str) and phrase.strip():
        return phrase
    credits = entry.get("artist-credit")
    if not isinstance(credits, list):
        return ""
    parts: list[str] = []
    for credit in credits:
        if not isinstance(credit, dict):
            continue
        artist = credit.get("artist")
        name = credit.get("name") or (artist.get("name") if isinstance(artist, dict) else "") or ""
        parts.append(f"{name}{credit.get('joinphrase') or ''}")
    return "".join(parts).strip()


def _musicbrainz_credit_mbid(artist_credit: Any, artist: str) -> str | None:
    """Return the MBID of an artist-credit entry naming ``artist`` exactly."""
    target = _normalize_identity(artist)
    entries = artist_credit if isinstance(artist_credit, list) else []
    for credit in entries:
        entry = credit.get("artist") if isinstance(credit, dict) else None
        if not isinstance(entry, dict):
            continue
        if _normalize_identity(str(entry.get("name") or "")) != target:
            continue
        mbid = entry.get("id")
        if isinstance(mbid, str) and mbid:
            return mbid
    return None


def _musicbrainz_release_artist_mbid(artist: str, album: str) -> str | None:
    """Return the artist MBID of a MusicBrainz release group for artist and album.

    The release group has to match the requested album and be credited to an
    artist with the requested name, so the MBID describes this track's release
    rather than a same-named other act.
    """
    for group in _search_musicbrainz_release(artist, album):
        if not isinstance(group, dict):
            continue
        credit_phrase = _musicbrainz_credit_phrase(group)
        if not _artist_album_match(artist, album, credit_phrase, group.get("title")):
            continue
        mbid = _musicbrainz_credit_mbid(group.get("artist-credit"), artist)
        if mbid:
            return mbid
    return None


def _musicbrainz_recording_artist_mbid(artist: str, title: str) -> str | None:
    """Return the artist MBID of a MusicBrainz recording for artist and title.

    Used for tracks with no album to search a release by, so a single still has a
    MusicBrainz identity to anchor on.
    """
    query = f"artist:{requests.utils.quote(artist)} AND recording:{requests.utils.quote(title)}"
    data = _musicbrainz_get(
        "recording", {"query": query, "fmt": "json", "limit": _MUSICBRAINZ_ARTIST_LIMIT}
    )
    recordings = data.get("recordings")
    for recording in recordings if isinstance(recordings, list) else []:
        if not isinstance(recording, dict):
            continue
        credit_phrase = _musicbrainz_credit_phrase(recording)
        if not _artist_title_match(artist, title, credit_phrase, recording.get("title")):
            continue
        mbid = _musicbrainz_credit_mbid(recording.get("artist-credit"), artist)
        if mbid:
            return mbid
    return None


def _musicbrainz_artist_mbid(artist: str, *, album: str | None, title: str | None) -> str | None:
    """Resolve the MusicBrainz identity of the artist behind one track."""
    if album and _has_reliable_album(album):
        mbid = _musicbrainz_release_artist_mbid(artist, album)
        if mbid:
            return mbid
    if title:
        return _musicbrainz_recording_artist_mbid(artist, title)
    return None


def _musicbrainz_artist_deezer_ids(mbid: str) -> list[int]:
    """Return the Deezer artist ids MusicBrainz links this artist to, in order.

    MusicBrainz artist entities carry editor-maintained URL relationships to the
    artist's pages on other services, which is an exact external identity: it
    says which Deezer artist this is, instead of searching for a display name.
    An artist can be linked to more than one Deezer page (a canonical one and a
    duplicate), so every linked id is returned for the caller to pick from.
    """
    data = _musicbrainz_get(f"artist/{mbid}", {"inc": "url-rels", "fmt": "json"})
    relations = data.get("relations")
    ids: list[int] = []
    for relation in relations if isinstance(relations, list) else []:
        if not isinstance(relation, dict):
            continue
        target = relation.get("url")
        resource = target.get("resource") if isinstance(target, dict) else None
        match = _DEEZER_ARTIST_URL.search(str(resource or ""))
        if match and int(match.group(1)) not in ids:
            ids.append(int(match.group(1)))
    return ids


def _musicbrainz_linked_identity(
    artist: str, mbid: str, deezer_ids: list[int]
) -> ArtistIdentity | None:
    """Return the image of the artist MusicBrainz links to, or None.

    Which page is used is decided by Deezer's own ranking (fans, then albums,
    then id) *among the pages MusicBrainz attributes to this artist*: the
    identity is already established by the relationship, so popularity only
    chooses the artist's canonical page over a duplicate stub.
    """
    records = [
        record
        for record in (_deezer_artist_record(artist_id) for artist_id in deezer_ids)
        if record is not None and _deezer_picture(record) is not None
    ]
    if not records:
        return None
    records.sort(key=_deezer_artist_rank, reverse=True)
    return _artist_identity(
        records[0], artist=artist, evidence=_EVIDENCE_MUSICBRAINZ_URL, mbid=mbid
    )


def _musicbrainz_exact_name_artists(artist: str) -> list[dict[str, Any]]:
    """Return MusicBrainz artists whose name matches exactly once normalized."""
    data = _musicbrainz_get(
        "artist",
        {
            "query": f"artist:{requests.utils.quote(artist)}",
            "fmt": "json",
            "limit": _MUSICBRAINZ_ARTIST_LIMIT,
        },
    )
    artists = data.get("artists")
    target = _normalize_identity(artist)
    return [
        entry
        for entry in (artists if isinstance(artists, list) else [])
        if isinstance(entry, dict) and _normalize_identity(str(entry.get("name") or "")) == target
    ]


def _artist_identity(
    record: dict[str, Any],
    *,
    artist: str,
    evidence: str,
    mbid: str | None = None,
) -> ArtistIdentity | None:
    """Build a verified identity from one Deezer artist record, or None."""
    picture = _deezer_picture(record)
    if picture is None:
        return None
    url, size = picture
    identity = ArtistIdentity(
        url=url,
        size=size,
        artist_id=int(record["id"]),
        confidence=_VERIFIED_CONFIDENCE,
        evidence=evidence,
        name=str(record.get("name") or "") or None,
        mbid=mbid,
    )
    logger.debug(
        "Artist artwork resolved artist=%s provider=deezer artist_id=%s name=%s "
        "confidence=%s evidence=%s mbid=%s",
        artist,
        identity.artist_id,
        identity.name,
        identity.confidence,
        identity.evidence,
        identity.mbid or "none",
    )
    return identity


def _reject_artist_artwork(artist: str, reason: str, candidate_ids: str = "none") -> None:
    """Log why an artist image was rejected, so a bad image is diagnosable later."""
    logger.debug(
        "Artist artwork rejected artist=%s candidate_ids=%s reason=%s",
        artist,
        candidate_ids,
        reason,
    )


def _resolve_artist_artwork(
    artist: str, *, album: str | None = None, title: str | None = None
) -> ArtistIdentity | None:
    """Resolve an artist profile image, or None when identity is not established.

    Evidence is tried strongest first:

    1. A MusicBrainz artist identity that links to a Deezer artist page, which is
       an exact external identity and needs no name search at all.
    2. MusicBrainz release/recording artist identity plus the track's Deezer
       release/track evidence, or a single exact-name Deezer candidate.
    3. Matching album/track evidence in the candidate's own Deezer releases,
       unique among the exact-name candidates.
    4. A sole exact-name Deezer candidate that MusicBrainz also knows by exactly
       that name.

    Everything weaker -- several exact-name artists with no corroborating
    release, a release list that cannot be read, a name that only partly matches
    -- is a ``likely`` or ``unknown`` candidate and returns None. Popularity is
    never evidence of identity: a famous namesake is still a namesake, and a
    wrong face is embedded permanently into the file.
    """
    mbid = _musicbrainz_artist_mbid(artist, album=album, title=title)
    if mbid:
        deezer_ids = _musicbrainz_artist_deezer_ids(mbid)
        identity = _musicbrainz_linked_identity(artist, mbid, deezer_ids)
        if identity is not None:
            return identity
        _reject_artist_artwork(
            artist,
            "musicbrainz lists no usable deezer page for this artist",
            ",".join(str(value) for value in deezer_ids) or "none",
        )

    candidates = _deezer_exact_name_candidates(artist)
    if not candidates:
        _reject_artist_artwork(artist, "no deezer artist matches the name exactly")
        return None

    corroborated: list[tuple[dict[str, Any], frozenset[str]]] = []
    for record in candidates[:_ARTIST_VERIFICATION_LIMIT]:
        evidence = _deezer_release_evidence(int(record["id"]), album=album, title=title)
        if evidence:
            corroborated.append((record, evidence))

    if len(corroborated) == 1:
        record, evidence = corroborated[0]
        return _artist_identity(record, artist=artist, evidence="-".join(sorted(evidence)))

    if len(corroborated) > 1:
        # Both the album and the track is stronger than either alone, but only
        # when it still leaves one candidate: two namesakes each carrying an
        # album of the same name are not something to guess between.
        strongest = [item for item in corroborated if len(item[1]) > 1]
        if len(strongest) == 1:
            record, evidence = strongest[0]
            return _artist_identity(record, artist=artist, evidence="-".join(sorted(evidence)))
        _reject_artist_artwork(
            artist,
            "several exact-name artists carry a release matching the track",
            ",".join(str(record["id"]) for record in candidates),
        )
        return None

    # No release evidence anywhere. Only a single unambiguous candidate can be
    # identified, and only when the second authority agrees that the name is
    # unambiguous. Otherwise this is exactly the popularity guess this resolver
    # must not make.
    if len(candidates) == 1:
        if mbid:
            return _artist_identity(
                candidates[0], artist=artist, evidence=_EVIDENCE_MUSICBRAINZ_ARTIST, mbid=mbid
            )
        if _musicbrainz_exact_name_artists(artist):
            return _artist_identity(candidates[0], artist=artist, evidence=_EVIDENCE_SOLE_CANDIDATE)
        _reject_artist_artwork(
            artist,
            "sole deezer candidate is not corroborated by musicbrainz",
            str(candidates[0]["id"]),
        )
        return None

    _reject_artist_artwork(
        artist,
        "several exact-name artists with no release or track evidence",
        ",".join(str(record["id"]) for record in candidates),
    )
    return None


__all__ = [
    "ArtistConfidence",
    "ArtistIdentity",
    "ArtworkCandidate",
    "_artist_album_match",
    "_image_mime",
    "_normalize_album_for_search",
    "_normalize_identity",
    "set_artwork_request_timeout",
]
