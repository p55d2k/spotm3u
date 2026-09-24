"""JSON API consumed by the React frontend (``docs/api.md``).

Every route here does the same three things and nothing else: parse the
request, call the shared helpers in :mod:`spotm3u.web_jobs`, and shape a
response. No download, metadata, lyrics, or matching logic lives here - it all
belongs to the existing modules.

This is the application's only frontend-facing surface besides the built React
application; there are no server-rendered pages.

Response format
---------------

* A successful call answers with the resource itself, never a wrapper. The
  payloads are the ones the frontend expects (``ProcessingJob.snapshot()`` and
  the batch summary built by :func:`spotm3u.web_jobs._batch_status`).
* A failed call answers with ``{"error": "<message>", "code": "<code>"}`` and a
  meaningful status. ``message`` is user-facing text the frontend can show
  as-is; ``code`` is stable and meant to be branched on. Codes are the
  ``ERROR_*`` constants below and are listed in ``docs/api.md``.
"""

from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    Flask,
    current_app,
    jsonify,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge

from .artwork import cached_artwork_path
from .exportify import ExportifyParseError, parse_exportify
from .m3u import check_playlist
from .media_player import (
    MediaPlayerError,
    add_to_media_player,
    library_import_available,
    library_player_name,
    media_player_available,
)
from .models import Playlist, Track
from .normalization import sanitize_filename_component
from .uploads import PickedFile
from .web_jobs import (
    _annotate_artwork,
    _annotate_lyrics,
    _batch_status,
    _build_processing_job,
    _current_job_directory,
    _download_dir,
    _import_batch,
    _save_job_state,
    _selected_playlist_ids,
    _selection_matches,
    _valid_playlist_index,
    store_and_parse,
    update_payload,
)

API_PREFIX = "/api"

# Uploads can be rejected before a job exists at all.
ERROR_UPLOAD_INVALID = "upload_invalid"
ERROR_UPLOAD_MISSING_FILE = "upload_missing_file"
ERROR_UPLOAD_TOO_LARGE = "upload_too_large"
# The upload (or its on-disk job directory) is no longer available.
ERROR_JOB_EXPIRED = "job_expired"
ERROR_PLAYLISTS_UNREADABLE = "playlists_unreadable"
# The request names something this export does not contain.
ERROR_PLAYLIST_UNAVAILABLE = "playlist_unavailable"
ERROR_SELECTION_REQUIRED = "selection_required"
ERROR_SELECTION_EXPIRED = "selection_expired"
# No processing job exists yet, or one is still in flight.
ERROR_JOB_NOT_FOUND = "job_not_found"
ERROR_JOB_NOT_READY = "job_not_ready"
ERROR_JOB_RUNNING = "job_running"
# The generated playlist points at files that are gone.
ERROR_PLAYLIST_INCOMPLETE = "playlist_incomplete"
# Media library handoff.
ERROR_MEDIA_PLAYER_UNAVAILABLE = "media_player_unavailable"
ERROR_NOTHING_TO_IMPORT = "nothing_to_import"
ERROR_MEDIA_PLAYER_FAILED = "media_player_failed"
# Transport-level answers from the API as a whole.
ERROR_NOT_FOUND = "not_found"
ERROR_METHOD_NOT_ALLOWED = "method_not_allowed"

api = Blueprint("api", __name__, url_prefix=API_PREFIX)


def _error(message: str, code: str, status: int):
    """Answer with the API's one error shape (see the module docstring)."""
    return jsonify({"error": message, "code": code}), status


def _job_or_error(job_id: str) -> Path | None:
    """The job directory, or ``None`` when the upload has expired."""
    return _current_job_directory(current_app, job_id)


def _playlists_or_none(job_directory: Path) -> list[Playlist] | None:
    """Read the playlists of a stored upload, or ``None`` when they are unreadable."""
    try:
        return parse_exportify(job_directory / "extracted")
    except ExportifyParseError as error:
        current_app.logger.info("Unable to parse job %s: %s", job_directory.name, error)
        return None
    except (OSError, UnicodeError):
        current_app.logger.exception("Unable to read playlist data for %s", job_directory.name)
        return None


def _validated_playlist_ids(raw: object, playlists: list[Playlist]) -> list[str] | None:
    """Normalize requested playlist ids, rejecting any this export does not have.

    Ids are the playlist's position in the export, which is what the job state
    and the routes have always used. Duplicates are dropped so a repeated id
    cannot start the same conversion twice.
    """
    if not isinstance(raw, list) or not raw:
        return None
    ids: list[str] = []
    for value in raw:
        if not isinstance(value, (int, str)):
            return None
        playlist_id = str(value)
        if _valid_playlist_index(playlist_id, len(playlists)) is None:
            return None
        if playlist_id not in ids:
            ids.append(playlist_id)
    return ids


def _save_selection(job_directory: Path, playlist_ids: list[str]) -> None:
    """Persist a selection the way the rest of the application already reads it.

    ``selected_playlist_ids`` is the batch selection and ``selected_playlist_id``
    the single one; writing both keeps :func:`spotm3u.web_jobs._selection_matches`
    and every other helper working on the same job state.
    """
    state: dict[str, object] = {"selected_playlist_ids": list(playlist_ids)}
    if len(playlist_ids) == 1:
        state["selected_playlist_id"] = playlist_ids[0]
    _save_job_state(job_directory, state)


def _fast_mode(body: dict) -> bool:
    """The fast-mode choice for a start request.

    A JSON body carries ``fast_mode`` explicitly. When it does not, the
    ``[fast] enabled`` configuration default applies.
    """
    if "fast_mode" not in body:
        return bool(current_app.config.get("FAST_MODE", False))
    return bool(body["fast_mode"])


def _playlist_summary(playlist: Playlist, index: int) -> dict[str, object]:
    """One playlist as the selection screen needs it, without its tracks."""
    return {
        "id": str(index),
        "name": playlist.name,
        "url": playlist.url,
        "track_count": (
            playlist.track_count if playlist.track_count is not None else len(playlist.tracks)
        ),
    }


def _track_payload(track: Track, index: int) -> dict[str, object]:
    return {
        "index": index,
        "title": track.title,
        "artists": list(track.artists),
        "album": track.album,
        "duration_ms": track.duration_ms,
        "album_artist": track.album_artist,
        "track_number": track.track_number,
        "disc_number": track.disc_number,
        "release_year": track.release_year,
        "genre": track.genre,
        "comments": track.comments,
        "spotify_id": track.spotify_id,
        "spotify_url": track.spotify_url,
    }


def _playlist_payload(playlist: Playlist, index: int) -> dict[str, object]:
    """One playlist with its tracks, in export order."""
    return {
        **_playlist_summary(playlist, index),
        "tracks": [
            _track_payload(track, position) for position, track in enumerate(playlist.tracks)
        ],
    }


def _job_payload(job_id: str, job_directory: Path, playlists: list[Playlist]) -> dict[str, object]:
    """The state of one imported export: what it holds and what is selected."""
    return {
        "job_id": job_id,
        "playlists": [
            _playlist_summary(playlist, index) for index, playlist in enumerate(playlists)
        ],
        "selected_playlist_ids": _selected_playlist_ids(job_directory),
        "download_dir": str(_download_dir(current_app)),
        "defaults": {"fast_mode": bool(current_app.config.get("FAST_MODE", False))},
    }


def _scope(job_directory: Path) -> list[str]:
    """Which playlists a status request covers: its query, or the selection."""
    requested = request.args.get("playlist_ids")
    if requested is None:
        return _selected_playlist_ids(job_directory)
    return [value for value in requested.split(",") if value]


def _resolved_selection(body: dict, job_directory: Path, playlists: list[Playlist]):
    """The playlist ids a processing request is about, or the error to answer.

    A body may name the playlists explicitly; without one the selection stored
    on the job is used, which is what the selection screen just saved. Returns
    ``(ids, None)`` or ``(None, error response)`` so the caller can return the
    error unchanged.
    """
    requested = body.get("playlist_ids", _selected_playlist_ids(job_directory))
    if not isinstance(requested, list) or not requested:
        return None, _error(
            "Choose at least one playlist before continuing.", ERROR_SELECTION_REQUIRED, 400
        )
    playlist_ids = _validated_playlist_ids(requested, playlists)
    if playlist_ids is None:
        return None, _error(
            "That playlist is not available for this upload.", ERROR_PLAYLIST_UNAVAILABLE, 400
        )
    return playlist_ids, None


def _track_state(job) -> dict[str, object]:
    """A job snapshot with the per-track artwork flags the track lists show."""
    state = job.as_dict()
    _annotate_artwork(job, state)
    return state


@api.get("/meta")
def metadata():
    """Capabilities and defaults the frontend needs before any upload exists."""
    from . import __version__

    return jsonify(
        {
            "version": __version__,
            "media_player_available": media_player_available(),
            "library_import_available": library_import_available(),
            "library_name": library_player_name(),
            "defaults": {"fast_mode": bool(current_app.config.get("FAST_MODE", False))},
            "limits": {
                "max_upload_size": int(current_app.config.get("MAX_CONTENT_LENGTH") or 0),
                "max_decompressed_size": int(current_app.config.get("MAX_DECOMPRESSED_SIZE") or 0),
                "max_archive_entries": int(current_app.config.get("MAX_ARCHIVE_ENTRIES") or 0),
            },
        }
    )


@api.get("/update")
def update_status():
    """Whether a newer SpotM3U release is available (never fails on the network)."""
    return jsonify(update_payload(current_app))


@api.get("/icon.png")
def app_icon():
    """The canonical SpotM3U artwork, so the frontend never ships a second copy."""
    from .desktop import webview_icon_path

    icon = webview_icon_path()
    if icon is None:
        return _error("The application icon is not available.", ERROR_NOT_FOUND, 404)
    return send_file(icon, mimetype="image/png", max_age=3600)


@api.post("/upload")
def upload_archive():
    """Import an Exportify ZIP uploaded by the browser."""
    uploaded_file = request.files.get("file")
    if uploaded_file is None or not uploaded_file.filename:
        return _error(
            "Choose the Exportify ZIP file before uploading.",
            ERROR_UPLOAD_MISSING_FILE,
            400,
        )
    job, playlists, error, status = store_and_parse(current_app, uploaded_file)
    if error is not None:
        return _error(error, ERROR_UPLOAD_INVALID, status)
    session["job_id"] = job.job_id
    return jsonify(_job_payload(job.job_id, job.directory, playlists)), 201


@api.post("/upload/picked")
def upload_picked_archive():
    """Import the archive the desktop shell picked in the OS file dialog.

    The path is collected from the window bridge rather than sent by the page,
    so this route can only read a file the user chose themselves.
    """
    controls = current_app.config.get("WINDOW_CONTROLS")
    source_path = controls.take_pending_import() if controls is not None else None
    if source_path is None:
        return _error(
            "Choose the Exportify ZIP file before uploading.",
            ERROR_UPLOAD_MISSING_FILE,
            400,
        )
    try:
        with source_path.open("rb") as stream:
            job, playlists, error, status = store_and_parse(
                current_app, PickedFile(source_path.name, stream)
            )
    except OSError:
        current_app.logger.exception("Unable to read the picked archive")
        return _error(
            "That file could not be read. Please choose it again.", ERROR_UPLOAD_INVALID, 400
        )
    if error is not None:
        return _error(error, ERROR_UPLOAD_INVALID, status)
    session["job_id"] = job.job_id
    return jsonify(_job_payload(job.job_id, job.directory, playlists)), 201


@api.get("/jobs/<job_id>")
def job_detail(job_id: str):
    """The imported export: its playlists, current selection, and defaults."""
    job_directory = _job_or_error(job_id)
    if job_directory is None:
        return _error(
            "That upload has expired. Please upload the ZIP again.", ERROR_JOB_EXPIRED, 404
        )
    playlists = _playlists_or_none(job_directory)
    if playlists is None:
        return _error(
            "The uploaded export could not be read. Please upload it again.",
            ERROR_PLAYLISTS_UNREADABLE,
            400,
        )
    return jsonify(_job_payload(job_id, job_directory, playlists))


@api.get("/jobs/<job_id>/playlists/<playlist_id>")
def playlist_detail(job_id: str, playlist_id: str):
    """One playlist of the export, with its tracks in order."""
    job_directory = _job_or_error(job_id)
    if job_directory is None:
        return _error(
            "That upload has expired. Please upload the ZIP again.", ERROR_JOB_EXPIRED, 404
        )
    playlists = _playlists_or_none(job_directory)
    if playlists is None:
        return _error(
            "The uploaded export could not be read. Please upload it again.",
            ERROR_PLAYLISTS_UNREADABLE,
            400,
        )
    index = _valid_playlist_index(playlist_id, len(playlists))
    if index is None:
        return _error(
            "That playlist is not available for this upload.",
            ERROR_PLAYLIST_UNAVAILABLE,
            404,
        )
    return jsonify(_playlist_payload(playlists[index], index))


@api.put("/jobs/<job_id>/selection")
def save_selection(job_id: str):
    """Store the playlists the user selected for conversion."""
    job_directory = _job_or_error(job_id)
    if job_directory is None:
        return _error(
            "That upload has expired. Please upload the ZIP again.", ERROR_JOB_EXPIRED, 404
        )
    playlists = _playlists_or_none(job_directory)
    if playlists is None:
        return _error(
            "The uploaded export could not be read. Please upload it again.",
            ERROR_PLAYLISTS_UNREADABLE,
            400,
        )
    body = request.get_json(silent=True) or {}
    playlist_ids, error = _resolved_selection(body, job_directory, playlists)
    if error is not None:
        return error
    _save_selection(job_directory, playlist_ids)
    return jsonify({"job_id": job_id, "playlist_ids": playlist_ids})


@api.post("/jobs/<job_id>/processing")
def start_processing(job_id: str):
    """Start converting the selected playlists (or the ones named in the body).

    The effective selection is stored on the way in, so the status and result
    routes find the same playlists afterwards.
    """
    job_directory = _job_or_error(job_id)
    if job_directory is None:
        return _error(
            "That upload has expired. Please upload the ZIP again.", ERROR_JOB_EXPIRED, 404
        )
    playlists = _playlists_or_none(job_directory)
    if playlists is None:
        return _error(
            "The uploaded export could not be read. Please upload it again.",
            ERROR_PLAYLISTS_UNREADABLE,
            400,
        )
    body = request.get_json(silent=True) or {}
    playlist_ids, error = _resolved_selection(body, job_directory, playlists)
    if error is not None:
        return error
    _save_selection(job_directory, playlist_ids)

    fast_mode = _fast_mode(body)
    manager = current_app.config["JOB_MANAGER"]
    # The job outlives this request in its own thread, so it gets the real
    # application object rather than the request-bound proxy.
    app = current_app._get_current_object()
    jobs = []
    for playlist_id in playlist_ids:
        existing = manager.get(job_id, playlist_id)
        if existing is not None:
            jobs.append(existing)
            continue
        job = _build_processing_job(
            app=app,
            job_id=job_id,
            playlist_id=playlist_id,
            playlist=playlists[int(playlist_id)],
            output_dir=_download_dir(app),
            music_library=app.config["MUSIC_LIBRARY"],
            # One M3U per playlist, so converting a batch never overwrites
            # another playlist's playlist file.
            m3u_filename=f"playlist-{playlist_id}.m3u",
            fast_mode=fast_mode,
        )
        manager.submit(job)
        job.start()
        jobs.append(job)
    return jsonify({"job_id": job_id, **_batch_status(jobs)}), 202


@api.get("/jobs/<job_id>/processing")
def processing_status(job_id: str):
    """Live progress for the selected playlists, ready to poll.

    ``?playlist_ids=0,2`` scopes the answer to specific playlists; without it,
    whichever playlists are currently selected are reported.
    """
    job_directory = _job_or_error(job_id)
    if job_directory is None:
        return _error(
            "That upload has expired. Please upload the ZIP again.", ERROR_JOB_EXPIRED, 404
        )
    playlist_ids = _scope(job_directory)
    if not playlist_ids:
        return _error(
            "That playlist selection has expired. Please choose playlists again.",
            ERROR_SELECTION_EXPIRED,
            404,
        )
    manager = current_app.config["JOB_MANAGER"]
    jobs = [job for job in (manager.get(job_id, pid) for pid in playlist_ids) if job is not None]
    if not jobs:
        return _error(
            "No conversion has been started for those playlists.",
            ERROR_JOB_NOT_FOUND,
            404,
        )
    return jsonify({"job_id": job_id, **_batch_status(jobs)})


@api.get("/jobs/<job_id>/playlists/<playlist_id>/processing")
def playlist_processing_status(job_id: str, playlist_id: str):
    """Live progress for one playlist: one ``ProcessingJob.snapshot()``."""
    job_directory = _job_or_error(job_id)
    if job_directory is None:
        return _error(
            "That upload has expired. Please upload the ZIP again.", ERROR_JOB_EXPIRED, 404
        )
    if not _selection_matches(job_directory, playlist_id):
        return _error(
            "That playlist selection has expired. Please choose playlists again.",
            ERROR_SELECTION_EXPIRED,
            404,
        )
    job = current_app.config["JOB_MANAGER"].get(job_id, playlist_id)
    if job is None or job.playlist_id != playlist_id:
        return _error(
            "No conversion has been started for that playlist.",
            ERROR_JOB_NOT_FOUND,
            404,
        )
    return jsonify(_track_state(job))


@api.post("/jobs/<job_id>/playlists/<playlist_id>/processing/retry")
def retry_processing(job_id: str, playlist_id: str):
    """Re-resolve the tracks of one playlist that have no usable file on disk.

    Tracks whose audio is still there keep their result, so a retry costs only
    the unresolved ones. ``retried`` reports how many were picked up: zero when
    there was nothing left to do.
    """
    job_directory = _job_or_error(job_id)
    if job_directory is None:
        return _error(
            "That upload has expired. Please upload the ZIP again.", ERROR_JOB_EXPIRED, 404
        )
    if not _selection_matches(job_directory, playlist_id):
        return _error(
            "That playlist selection has expired. Please choose playlists again.",
            ERROR_SELECTION_EXPIRED,
            404,
        )
    job = current_app.config["JOB_MANAGER"].get(job_id, playlist_id)
    if job is None or job.playlist_id != playlist_id:
        return _error(
            "No conversion has been started for that playlist.",
            ERROR_JOB_NOT_FOUND,
            404,
        )
    if job.status in {"queued", "running"}:
        return _error("That playlist is still being converted.", ERROR_JOB_RUNNING, 409)
    retried = job.retry()
    return jsonify({**_track_state(job), "retried": len(retried)})


@api.get("/jobs/<job_id>/playlists/<playlist_id>/result")
def playlist_result(job_id: str, playlist_id: str):
    """The finished (or failed) outcome of one playlist, with lyrics."""
    job_directory = _job_or_error(job_id)
    if job_directory is None:
        return _error(
            "That upload has expired. Please upload the ZIP again.", ERROR_JOB_EXPIRED, 404
        )
    if not _selection_matches(job_directory, playlist_id):
        return _error(
            "That playlist selection has expired. Please choose playlists again.",
            ERROR_SELECTION_EXPIRED,
            404,
        )
    job = current_app.config["JOB_MANAGER"].get(job_id, playlist_id)
    if job is None or job.playlist_id != playlist_id:
        return _error(
            "No conversion has been started for that playlist.",
            ERROR_JOB_NOT_FOUND,
            404,
        )
    state = _track_state(job)
    _annotate_lyrics(state)
    return jsonify(
        {
            **state,
            "m3u_url": url_for("api.download_playlist", job_id=job_id, playlist_id=playlist_id),
            "media_player_available": media_player_available(),
            "library_import_available": library_import_available(),
            "library_name": library_player_name(),
        }
    )


@api.get("/jobs/<job_id>/result")
def job_result(job_id: str):
    """The outcome of every selected playlist, for the batch result screen."""
    job_directory = _job_or_error(job_id)
    if job_directory is None:
        return _error(
            "That upload has expired. Please upload the ZIP again.", ERROR_JOB_EXPIRED, 404
        )
    playlist_ids = _scope(job_directory)
    if not playlist_ids:
        return _error(
            "That playlist selection has expired. Please choose playlists again.",
            ERROR_SELECTION_EXPIRED,
            404,
        )
    manager = current_app.config["JOB_MANAGER"]
    jobs = [manager.get(job_id, playlist_id) for playlist_id in playlist_ids]
    if any(job is None or job.status in {"queued", "running"} for job in jobs):
        return _error("Every playlist must finish converting first.", ERROR_JOB_RUNNING, 409)
    completed = [job for job in jobs if job is not None]
    payload = _batch_status(completed)
    # Lyrics are read from the files themselves, so they are only reported once
    # nothing is in flight, never on every poll.
    for state in payload["playlists"]:
        _annotate_lyrics(state)
    return jsonify(
        {
            "job_id": job_id,
            **payload,
            "media_player_available": media_player_available(),
            "library_import_available": library_import_available(),
            "library_name": library_player_name(),
        }
    )


@api.get("/jobs/<job_id>/playlists/<playlist_id>/m3u")
def download_playlist(job_id: str, playlist_id: str):
    """Download the generated playlist.

    A playlist whose referenced files were deleted by hand is refused with
    ``playlist_incomplete`` unless ``?confirm=1`` asks for it anyway, so the
    user is never handed a playlist that silently skips tracks.
    """
    manager = current_app.config["JOB_MANAGER"]
    job = manager.get(job_id, playlist_id)
    if job is None or job.playlist_id != playlist_id or job.m3u_path is None:
        return _error("That playlist is not ready to download.", ERROR_JOB_NOT_READY, 404)
    m3u_path = Path(job.m3u_path)
    if not m3u_path.is_file():
        return _error("That playlist is not ready to download.", ERROR_JOB_NOT_READY, 404)
    name = sanitize_filename_component(job.playlist_name) or job.playlist_id
    check = check_playlist(m3u_path)
    if not check.complete and request.args.get("confirm") != "1":
        missing = check.missing_names()
        return (
            jsonify(
                {
                    "error": (
                        f"{len(missing)} track(s) from this playlist are no longer in the "
                        "download folder. Save it anyway?"
                    ),
                    "code": ERROR_PLAYLIST_INCOMPLETE,
                    "missing": missing,
                    "total": check.total,
                    "confirm_url": url_for(
                        "api.download_playlist",
                        job_id=job_id,
                        playlist_id=playlist_id,
                        confirm=1,
                    ),
                }
            ),
            409,
        )
    # Served as octet-stream (not audio/x-mpegurl) so a WebView that ignores
    # ``Content-Disposition: attachment`` cannot "show" the playlist and open
    # its built-in media player; it can only offer a native save instead.
    return send_file(
        m3u_path,
        as_attachment=True,
        download_name=f"{name}.m3u",
        mimetype="application/octet-stream",
    )


@api.get("/jobs/<job_id>/playlists/<playlist_id>/artwork/<int:index>")
def playlist_artwork(job_id: str, playlist_id: str, index: int):
    """Serve locally cached artwork for one track, without network access."""
    job = current_app.config["JOB_MANAGER"].get(job_id, playlist_id)
    if job is None or job.playlist_id != playlist_id:
        return _error("No conversion has been started for that playlist.", ERROR_JOB_NOT_FOUND, 404)
    try:
        track = job.tracks[index]
    except IndexError:
        return _error("That track is not available.", ERROR_NOT_FOUND, 404)
    # No artwork for a download that is no longer on disk, even though its
    # release image is still cached.
    if job.output_missing(index):
        return _error("That track is not available.", ERROR_NOT_FOUND, 404)
    path = cached_artwork_path(job.output_dir, track)
    if path is None:
        return _error("No artwork is available for that track.", ERROR_NOT_FOUND, 404)
    return send_file(path, mimetype="image/jpeg", max_age=3600)


@api.post("/jobs/<job_id>/playlists/<playlist_id>/media-player")
def add_playlist_to_media_player(job_id: str, playlist_id: str):
    """Send one generated playlist to the platform's media player.

    The playlist written by the processing job is reused as-is; nothing is
    regenerated for this action, and the M3U download stays available.
    """
    if not media_player_available():
        return _error(
            "Adding to the media player is only available on macOS and Windows.",
            ERROR_MEDIA_PLAYER_UNAVAILABLE,
            404,
        )
    job = current_app.config["JOB_MANAGER"].get(job_id, playlist_id)
    if job is None or job.playlist_id != playlist_id or job.status != "completed":
        return _error("That playlist is not ready to import.", ERROR_JOB_NOT_READY, 404)
    state = _track_state(job)
    _annotate_lyrics(state)
    paths = [
        track["local_path"]
        for track in state["tracks"]
        if track["status"] == "complete" and track["local_path"]
    ]
    if not paths:
        return _error("There are no resolved tracks to add.", ERROR_NOTHING_TO_IMPORT, 409)
    unresolved = len(state["tracks"]) - len(paths)
    try:
        result = add_to_media_player(job.playlist_name, job.m3u_path, paths)
    except MediaPlayerError as error:
        current_app.logger.warning("Add to Media Player failed job=%s: %s", job_id, error)
        return _error(str(error), ERROR_MEDIA_PLAYER_FAILED, 502)
    return jsonify(
        {
            **state,
            "media_player": {
                "action": result.action,
                "message": result.message,
                "imported": result.imported,
                "failed": result.failed,
                "skipped": result.skipped,
                "cancelled": result.cancelled,
                "opened": result.opened,
                "unresolved": unresolved,
                "partial": bool(result.failed or unresolved),
            },
        }
    )


@api.post("/jobs/<job_id>/media-player")
def add_batch_to_media_player(job_id: str):
    """Import every converted playlist of the selection into the media library.

    One click for the whole batch: every playlist becomes its own library
    playlist - they are never merged into one - reusing the M3U and the
    resolved files the jobs already produced. A playlist that cannot be
    imported never stops the rest; the outcome is reported per playlist.
    """
    if not library_import_available():
        return _error(
            f"Adding every playlist at once needs {library_player_name()}, which is not "
            "available here.",
            ERROR_MEDIA_PLAYER_UNAVAILABLE,
            404,
        )
    job_directory = _job_or_error(job_id)
    if job_directory is None:
        return _error(
            "That upload has expired. Please upload the ZIP again.", ERROR_JOB_EXPIRED, 404
        )
    playlist_ids = _scope(job_directory)
    if not playlist_ids:
        return _error(
            "That playlist selection has expired. Please choose playlists again.",
            ERROR_SELECTION_EXPIRED,
            404,
        )
    manager = current_app.config["JOB_MANAGER"]
    jobs = [manager.get(job_id, playlist_id) for playlist_id in playlist_ids]
    if any(job is None or job.status in {"queued", "running"} for job in jobs):
        return _error("Every playlist must finish converting first.", ERROR_JOB_RUNNING, 409)
    return jsonify(_import_batch(current_app, [job for job in jobs if job is not None]))


@api.errorhandler(RequestEntityTooLarge)
def upload_too_large(error):
    """Answer an oversized request as JSON."""
    return _error("That file is too large to upload.", ERROR_UPLOAD_TOO_LARGE, 413)


def register_api(app: Flask) -> None:
    """Attach the JSON API and answer unknown API paths with JSON too.

    The fallbacks only take over under ``/api``; every other path keeps the
    default handling, so a WebView navigation that does not map to a route or a
    file still answers without a JSON body.
    """
    app.register_blueprint(api)

    @app.errorhandler(404)
    def _not_found(error):
        if request.path.startswith(f"{API_PREFIX}/") or request.path == API_PREFIX:
            return _error("That endpoint does not exist.", ERROR_NOT_FOUND, 404)
        return error

    @app.errorhandler(405)
    def _method_not_allowed(error):
        if request.path.startswith(f"{API_PREFIX}/") or request.path == API_PREFIX:
            return _error(
                "That method is not allowed for this endpoint.", ERROR_METHOD_NOT_ALLOWED, 405
            )
        return error
