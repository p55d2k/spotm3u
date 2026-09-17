"""Convert Exportify CSV/JSON exports into the application's generic models."""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TextIO

from spotm3u.models import Playlist, Track


class ExportifyParseError(ValueError):
    """The supplied files are not a valid Exportify export."""


def parse_exportify(source: Path | str) -> list[Playlist]:
    """Parse an extracted Exportify directory or a single export file."""
    path = Path(source)
    if path.suffix.lower() == ".zip":
        return parse_exportify_zip(path)
    if path.is_dir():
        files = sorted(
            file
            for file in path.rglob("*")
            if file.is_file() and file.suffix.lower() in {".csv", ".json"}
        )
        if not files:
            raise ExportifyParseError("The export contains no CSV or JSON playlist files.")
        return _parse_files((file.name, file.read_text(encoding="utf-8-sig")) for file in files)
    if not path.is_file():
        raise ExportifyParseError("The Exportify export does not exist.")
    if path.suffix.lower() == ".csv":
        return [_parse_csv(path.name, path.read_text(encoding="utf-8-sig"))]
    if path.suffix.lower() == ".json":
        return _parse_json(path.name, path.read_text(encoding="utf-8-sig"))
    raise ExportifyParseError("The export must be a CSV, JSON, or ZIP file.")


def parse_exportify_zip(source: Path | str) -> list[Playlist]:
    """Parse playlist files directly from an Exportify ZIP archive."""
    try:
        with zipfile.ZipFile(source) as archive:
            files = [
                entry
                for entry in archive.infolist()
                if not entry.is_dir() and Path(entry.filename).suffix.lower() in {".csv", ".json"}
            ]
            if not files:
                raise ExportifyParseError("The export contains no CSV or JSON playlist files.")
            return _parse_files(
                (entry.filename, archive.read(entry).decode("utf-8-sig")) for entry in files
            )
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as error:
        raise ExportifyParseError("The uploaded file is not a readable Exportify export.") from error


def _parse_files(files: Iterable[tuple[str, str]]) -> list[Playlist]:
    playlists: list[Playlist] = []
    for filename, content in files:
        suffix = Path(filename).suffix.lower()
        if suffix == ".csv":
            playlists.append(_parse_csv(filename, content))
        elif suffix == ".json":
            playlists.extend(_parse_json(filename, content))
    if not playlists:
        raise ExportifyParseError("The export contains no playlist data.")
    return playlists


def _parse_csv(filename: str, content: str) -> Playlist:
    try:
        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            raise ExportifyParseError(f"{filename} has no CSV header.")
        fields = {_field_key(name): name for name in reader.fieldnames if name}
        if not _first_field(fields, "trackname", "name", "title"):
            raise ExportifyParseError(f"{filename} is missing a track title column.")

        tracks: list[Track] = []
        for row_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values() if value is not None):
                continue
            tracks.append(_track_from_row(row, fields, filename, row_number))
    except csv.Error as error:
        raise ExportifyParseError(f"{filename} contains malformed CSV data.") from error

    playlist_name = Path(filename).stem
    return Playlist(id=None, name=playlist_name, track_count=len(tracks), tracks=tracks)


def _parse_json(filename: str, content: str) -> list[Playlist]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise ExportifyParseError(f"{filename} contains malformed JSON.") from error

    if isinstance(payload, list):
        if any(not isinstance(track, Mapping) for track in payload):
            raise ExportifyParseError(f"{filename} contains a malformed track.")
        return [
            Playlist(
                id=None,
                name=Path(filename).stem,
                track_count=len(payload),
                tracks=[
                    _track_from_mapping(track, filename, index)
                    for index, track in enumerate(payload, start=1)
                    if isinstance(track, Mapping)
                ],
            )
        ]
    if isinstance(payload, Mapping) and isinstance(payload.get("playlists"), list):
        items = payload["playlists"]
    elif isinstance(payload, Mapping):
        items = [payload]
    else:
        raise ExportifyParseError(f"{filename} does not contain playlist data.")

    playlists: list[Playlist] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise ExportifyParseError(f"{filename} playlist {index} is malformed.")
        raw_tracks = item.get("tracks", item.get("items", []))
        if not isinstance(raw_tracks, list):
            raise ExportifyParseError(f"{filename} playlist {index} has malformed tracks.")
        tracks = [
            _track_from_mapping(track, filename, index)
            for track in raw_tracks
            if isinstance(track, Mapping)
        ]
        if len(tracks) != len(raw_tracks):
            raise ExportifyParseError(f"{filename} playlist {index} contains a malformed track.")
        name = _text(item.get("name") or item.get("playlist_name")) or Path(filename).stem
        playlist_id = _text(item.get("id") or item.get("playlist_id"))
        playlists.append(
            Playlist(id=playlist_id, name=name, track_count=len(tracks), tracks=tracks)
        )
    return playlists


def _track_from_row(
    row: Mapping[str | None, str | None],
    fields: Mapping[str, str],
    filename: str,
    row_number: int,
) -> Track:
    values = {_field_key(key): value for key, value in row.items() if key}
    return _track_from_values(values, filename, row_number)


def _track_from_mapping(row: Mapping[object, object], filename: str, row_number: int) -> Track:
    values = {_field_key(str(key)): value for key, value in row.items()}
    return _track_from_values(values, filename, row_number)


def _track_from_values(values: Mapping[str, object], filename: str, row_number: int) -> Track:
    title = _text(_value(values, "trackname", "name", "title"))
    if not title:
        raise ExportifyParseError(f"{filename} row {row_number} is missing a track title.")
    artists_value = _value(values, "artistnames", "artistname", "artists", "artist")
    artists = _artists(artists_value)
    duration = _integer(_value(values, "durationms", "duration"))
    uri = _text(_value(values, "trackuri", "uri", "spotifyuri"))
    spotify_id = _text(_value(values, "trackid", "spotifyid")) or _spotify_id(uri)
    spotify_url = _text(_value(values, "spotifyurl", "trackurl", "url"))
    return Track(
        title=title,
        artists=artists,
        album=_text(_value(values, "albumname", "album")),
        duration_ms=duration,
        spotify_id=spotify_id,
        spotify_url=spotify_url,
    )


def _field_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _first_field(fields: Mapping[str, str], *names: str) -> str | None:
    return next((fields[name] for name in names if name in fields), None)


def _value(values: Mapping[str, object], *names: str) -> object:
    return next((values[name] for name in names if name in values), None)


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _artists(value: object) -> list[str]:
    if isinstance(value, list):
        return [artist for item in value if (artist := _text(item))]
    text = _text(value)
    return [artist.strip() for artist in text.split(";") if artist.strip()] if text else []


def _integer(value: object) -> int | None:
    text = _text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _spotify_id(uri: str | None) -> str | None:
    if uri and uri.startswith("spotify:track:"):
        return uri.rsplit(":", 1)[-1] or None
    return None
