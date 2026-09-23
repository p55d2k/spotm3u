"""Metadata and artwork enrichment for resolved audio files.

ID3 writing and the enrichment pipeline live here; the artwork lookup and
caching these functions rely on lives in :mod:`spotm3u.artwork`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import mutagen.id3 as mutagen_id3

from .artwork import (
    _find_album_artwork,
    _find_artist_artwork,
    album_artwork_enabled,
    artist_artwork_enabled,
)
from .artwork_sources import _image_mime
from .lyrics import fetch_lyrics, lyrics_enabled
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
USLT = mutagen_id3.USLT
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
_ORIGINAL_USLT = USLT
_ORIGINAL_APIC = APIC

# Standard ID3 frame holding a track's plain (unsynchronised) lyrics.
_USLT_FRAME = "USLT"


def _id3_symbol(name: str):
    current = globals().get(name)
    fallback = getattr(mutagen_id3, name)
    original = globals().get(f"_ORIGINAL_{name}", fallback)
    if current is not None and current is not original:
        return current
    return fallback


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


class MetadataError(RuntimeError):
    """Raised when metadata enrichment fails fatally (rare)."""


# When True (default) the standard ID3 text fields (title, artists, album,
# album artist, track/disc number, year, genre, comment) are written. When
# False tags are left exactly as the downloader produced them, so a file can be
# audio-only. Configured through ``metadata.tags`` in config.toml.
_ID3_TAGS_ENABLED = True


def set_id3_tags_enabled(enabled: bool) -> None:
    """Enable or disable writing the standard ID3 text fields."""
    global _ID3_TAGS_ENABLED
    _ID3_TAGS_ENABLED = enabled


def id3_tags_enabled() -> bool:
    """Return whether standard ID3 text field writing is currently enabled."""
    return _ID3_TAGS_ENABLED


# Master switch for embedding anything into an audio file. When False, no text
# fields, album cover, artist image or lyrics are written, whatever the
# individual options say, which is the "just the audio, save the space" mode.
# Configured through ``metadata.enabled`` in config.toml.
_METADATA_ENABLED = True


def set_metadata_enabled(enabled: bool) -> None:
    """Enable or disable all metadata embedding into audio files."""
    global _METADATA_ENABLED
    _METADATA_ENABLED = enabled


def metadata_enabled() -> bool:
    """Return whether embedding metadata into audio files is enabled at all."""
    return _METADATA_ENABLED


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


def _embed_lyrics(path: Path, lyrics: str) -> bool:
    """Write plain lyrics into the file's standard lyrics frame.

    Only the USLT frame is replaced, so every other field (including embedded
    album and artist artwork) is preserved.
    """
    ID3_cls = _id3_symbol("ID3")
    ID3NoHeaderError_cls = _id3_symbol("ID3NoHeaderError")
    USLT_cls = _id3_symbol("USLT")

    try:
        tags = ID3_cls(str(path))
    except ID3NoHeaderError_cls:
        tags = ID3_cls()

    if hasattr(tags, "delall"):
        tags.delall(_USLT_FRAME)
    tags[_USLT_FRAME] = USLT_cls(encoding=3, lang="eng", desc="", text=lyrics)

    try:
        tags.save(str(path), v2_version=3)
        return True
    except (OSError, ValueError) as exc:
        logger.debug("Failed to embed lyrics path=%s: %s", path, exc)
        return False


def _embed_track_lyrics(path: Path, track: Track) -> bool:
    """Retrieve and embed a track's lyrics, never failing the track.

    Lyrics are optional enrichment: retrieval, an unsupported audio format, or
    a metadata write problem are all contained here and reported through the
    return value.
    """
    try:
        lyrics = fetch_lyrics(track)
        if not lyrics:
            return False
        return _embed_lyrics(path, lyrics)
    except Exception as exc:  # pragma: no cover - defensive behavior
        logger.debug("Lyrics enrichment failed path=%s: %s", path, exc)
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

    embeddings_on = metadata_enabled()

    if embeddings_on and id3_tags_enabled():
        try:
            fields_written.extend(_write_all_metadata(audio_path, track))
        except Exception as exc:  # pragma: no cover - defensive behavior
            errors.append(f"metadata write failed: {exc}")

    if embeddings_on and album_artwork_enabled():
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

    if embeddings_on and artist_artwork_enabled() and track.artists:
        artist_artwork_embedded, artist_artwork_source = _embed_artist_artwork(
            audio_path, download_path, track.artists[0], errors
        )

    # Lyrics are skipped entirely in fast mode (``set_lyrics_enabled``) and by
    # the metadata master switch, which avoids the lyrics provider requests as
    # well as the metadata write.
    if embeddings_on and lyrics_enabled() and track.title:
        if _embed_track_lyrics(audio_path, track):
            fields_written.append(_USLT_FRAME)

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


__all__ = [
    "MetadataError",
    "MetadataResult",
    "enrich_metadata",
    "enrich_metadata_batch",
    "id3_tags_enabled",
    "metadata_enabled",
    "set_id3_tags_enabled",
    "set_metadata_enabled",
]
