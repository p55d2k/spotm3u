"""Flask application for the SpotM3U web interface."""

import json
import logging
import re
import secrets
from functools import partial
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from .audio.resolver import LocalAudioResolver
from .config import load_user_config
from .exportify import ExportifyParseError, parse_exportify
from .ffmpeg import locate_ffmpeg_location
from .jobs import JobManager, JobStartError, ProcessingJob
from .log import PACKAGE_LOGGER, configure_logging
from .media_player import MediaPlayerError, add_to_media_player, media_player_available
from .metadata import cached_artwork_path, set_artist_artwork_enabled, set_artwork_verify_local
from .models import Playlist
from .normalization import sanitize_filename_component
from .online import OnlineSourceSearcher, describe_youtube_setup, download_track
from .online.cache import DownloadCache
from .resolution import TrackResolver
from .update import check_for_updates
from .uploads import UploadError, cleanup_jobs, default_upload_root, store_upload

JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def create_app(config: dict | None = None) -> Flask:
    """Create and configure the Flask application.

    An optional ``config.toml`` is loaded first (see ``spotm3u.config``);
    anything passed in ``config`` overrides it. No configuration file is
    required and every value keeps a built-in default.
    """
    app = Flask(__name__)
    settings = load_user_config().to_app_config()
    configure_logging(settings.get("LOG_LEVEL"))
    app.config.from_mapping(
        {
            "SECRET_KEY": secrets.token_hex(32),
            "JOB_MANAGER": JobManager(),
            **settings,
        }
    )
    if not app.config.get("UPLOAD_ROOT"):
        app.config["UPLOAD_ROOT"] = default_upload_root()
    if not app.config.get("MUSIC_LIBRARY"):
        app.config["MUSIC_LIBRARY"] = str(Path.home() / "Music")
    if config:
        app.config.update(config)

    set_artwork_verify_local(bool(app.config.get("ARTWORK_VERIFY_LOCAL", True)))
    set_artist_artwork_enabled(bool(app.config.get("ARTWORK_ARTIST_ARTWORK", True)))

    report = describe_youtube_setup(
        cookies_from_browser=app.config.get("YTDLP_COOKIES_FROM_BROWSER"),
        pot_provider_url=app.config.get("YTDLP_POT_PROVIDER_URL"),
        pot_provider_home=app.config.get("YTDLP_POT_PROVIDER_HOME"),
    )
    logging.getLogger(PACKAGE_LOGGER).info("youtube setup: %s", json.dumps(report, sort_keys=True))

    @app.get("/update/check")
    def update_check():
        """Report whether a newer SpotM3U release is available.

        The check is cached server-side by ``[update] check_interval_hours``
        and degrades to "no update known" on any network problem, so the page
        never blocks or errors on the GitHub API.
        """
        from . import __version__

        if not app.config.get("UPDATE_CHECK", True):
            return jsonify(
                {
                    "update_available": False,
                    "latest_version": None,
                    "current_version": __version__,
                    "error": "Update checks are disabled.",
                }
            )
        interval = int(app.config.get("UPDATE_CHECK_INTERVAL_HOURS", 24)) * 60 * 60
        repo = str(app.config.get("UPDATE_REPO", "p55d2k/spotm3u"))
        info = check_for_updates(repo=repo, current_version=__version__, interval_seconds=interval)
        return jsonify(info.as_dict())

    @app.get("/")
    def index():
        return render_template("index.html", error=None)

    @app.post("/upload")
    def upload():
        uploaded_file = request.files.get("file")
        if uploaded_file is None or not uploaded_file.filename:
            return render_template(
                "index.html",
                error="Choose the Exportify ZIP file before uploading.",
            ), 400

        try:
            job = store_upload(
                uploaded_file,
                upload_root=app.config["UPLOAD_ROOT"],
                max_upload_size=app.config["MAX_CONTENT_LENGTH"],
                max_decompressed_size=app.config["MAX_DECOMPRESSED_SIZE"],
                max_archive_entries=app.config["MAX_ARCHIVE_ENTRIES"],
            )
        except UploadError as error:
            return render_template("index.html", error=str(error), workflow_stage=1), 400
        except (OSError, ValueError):
            app.logger.exception("Unable to store uploaded archive")
            return render_template(
                "index.html",
                error="The upload could not be stored. Please try again.",
                workflow_stage=1,
            ), 500

        _sweep_old_jobs(app)

        try:
            playlists = parse_exportify(job.extracted)
        except ExportifyParseError as error:
            app.logger.info("Uploaded archive is not a valid Exportify export: %s", error)
            return render_template("index.html", error=str(error), workflow_stage=1), 400
        except (OSError, UnicodeError):
            app.logger.exception("Unable to read uploaded Exportify archive")
            return render_template(
                "index.html",
                error="The uploaded export could not be read. Please try again.",
                workflow_stage=1,
            ), 400

        session["job_id"] = job.job_id
        return render_template(
            "playlists.html",
            job_id=job.job_id,
            playlists=playlists,
            workflow_stage=2,
        ), 201

    @app.get("/playlists/<job_id>")
    def playlists(job_id: str):
        job_directory = _current_job_directory(app, job_id)
        if job_directory is None:
            return render_template(
                "index.html",
                error="That upload could not be found. Please upload the ZIP again.",
            ), 404

        try:
            playlist_data = parse_exportify(job_directory / "extracted")
        except ExportifyParseError as error:
            app.logger.info("Unable to parse job %s: %s", job_id, error)
            return render_template("index.html", error=str(error)), 400
        except (OSError, UnicodeError):
            app.logger.exception("Unable to read playlist data for job %s", job_id)
            return render_template(
                "index.html",
                error="The uploaded export could not be read. Please upload it again.",
            ), 400

        return render_template(
            "playlists.html",
            job_id=job_id,
            playlists=playlist_data,
        )

    @app.post("/playlists/<job_id>/select")
    def select_playlist(job_id: str):
        job_directory = _current_job_directory(app, job_id)
        if job_directory is None:
            return render_template(
                "index.html",
                error="That upload has expired. Please upload the ZIP again.",
            ), 404

        try:
            playlist_data = parse_exportify(job_directory / "extracted")
        except (ExportifyParseError, OSError, UnicodeError) as error:
            app.logger.info("Unable to load playlists for job %s: %s", job_id, error)
            return render_template(
                "index.html",
                error="The playlist selection has expired. Please upload the ZIP again.",
            ), 400

        playlist_id = request.form.get("playlist_id", "")
        if not playlist_id.isdigit():
            return render_template(
                "playlists.html",
                job_id=job_id,
                playlists=playlist_data,
                error="Choose a playlist before continuing.",
            ), 400

        playlist_index = int(playlist_id)
        if playlist_index < 0 or playlist_index >= len(playlist_data):
            return render_template(
                "playlists.html",
                job_id=job_id,
                playlists=playlist_data,
                error="That playlist is not available for this upload.",
            ), 400

        _save_job_state(
            job_directory,
            {"selected_playlist_id": playlist_id},
        )
        return redirect(url_for("processing", job_id=job_id, playlist_id=playlist_id))

    @app.post("/playlists/<job_id>/batch-select")
    def select_playlists_batch(job_id: str):
        job_directory = _current_job_directory(app, job_id)
        if job_directory is None:
            return render_template(
                "index.html", error="That upload has expired. Please upload the ZIP again."
            ), 404
        try:
            playlist_data = parse_exportify(job_directory / "extracted")
        except (ExportifyParseError, OSError, UnicodeError):
            return render_template(
                "index.html",
                error="The playlist selection has expired. Please upload the ZIP again.",
            ), 400
        selected = request.form.getlist("playlist_id")
        if not selected or any(
            _valid_playlist_index(value, len(playlist_data)) is None for value in selected
        ):
            return render_template(
                "playlists.html",
                job_id=job_id,
                playlists=playlist_data,
                error="Choose at least one playlist before continuing.",
            ), 400
        selected = list(dict.fromkeys(selected))
        _save_job_state(job_directory, {"selected_playlist_ids": selected})
        return redirect(url_for("batch_processing", job_id=job_id))

    @app.get("/processing/<job_id>/batch")
    def batch_processing(job_id: str):
        job_directory = _current_job_directory(app, job_id)
        selected = _selected_playlist_ids(job_directory) if job_directory else None
        if not selected:
            return render_template(
                "index.html",
                error="That playlist selection has expired. Please upload the ZIP again.",
            ), 404
        playlists_data = parse_exportify(job_directory / "extracted")
        playlists = [playlists_data[int(index)] for index in selected]
        return render_template(
            "batch_processing.html",
            job_id=job_id,
            playlists=list(zip(selected, playlists, strict=True)),
        )

    @app.post("/processing/<job_id>/batch/start")
    def start_batch_processing(job_id: str):
        job_directory = _current_job_directory(app, job_id)
        selected = _selected_playlist_ids(job_directory) if job_directory else None
        if not selected:
            return jsonify({"error": "That playlist selection has expired."}), 404
        playlist_data = parse_exportify(job_directory / "extracted")
        manager = app.config["JOB_MANAGER"]
        jobs = []
        for playlist_id in selected:
            existing = manager.get(job_id, playlist_id)
            if existing is not None:
                jobs.append(existing)
                continue
            playlist = playlist_data[int(playlist_id)]
            job = _build_processing_job(
                app=app,
                job_id=job_id,
                playlist_id=playlist_id,
                playlist=playlist,
                output_dir=_download_dir(app),
                music_library=app.config["MUSIC_LIBRARY"],
                m3u_filename=f"playlist-{playlist_id}.m3u",
            )
            manager.submit(job)
            job.start()
            jobs.append(job)
        return jsonify({"job_id": job_id, **_batch_status(jobs)}), 202

    @app.get("/processing/<job_id>/batch/status")
    def batch_processing_status(job_id: str):
        job_directory = _current_job_directory(app, job_id)
        selected = _selected_playlist_ids(job_directory) if job_directory else None
        if not selected:
            return jsonify({"error": "That playlist selection has expired."}), 404
        jobs = [app.config["JOB_MANAGER"].get(job_id, playlist_id) for playlist_id in selected]
        return jsonify(
            {"job_id": job_id, **_batch_status([job for job in jobs if job is not None])}
        )

    @app.get("/processing/<job_id>/batch/result")
    def batch_processing_result(job_id: str):
        job_directory = _current_job_directory(app, job_id)
        selected = _selected_playlist_ids(job_directory) if job_directory else None
        if not selected:
            return render_template(
                "index.html",
                error="That playlist selection has expired. Please upload the ZIP again.",
            ), 404
        jobs = [app.config["JOB_MANAGER"].get(job_id, playlist_id) for playlist_id in selected]
        if not jobs or any(job is None or job.status in {"queued", "running"} for job in jobs):
            return redirect(url_for("batch_processing", job_id=job_id))
        return render_template(
            "batch_result.html",
            job_id=job_id,
            states=[job.as_dict() for job in jobs],
            media_player_available=media_player_available(),
        )

    @app.get("/processing/<job_id>/<playlist_id>")
    def processing(job_id: str, playlist_id: str):
        job_directory = _current_job_directory(app, job_id)
        if job_directory is None or not _selection_matches(job_directory, playlist_id):
            return render_template(
                "index.html",
                error="That playlist selection has expired. Please upload the ZIP again.",
            ), 404

        try:
            playlist_data = parse_exportify(job_directory / "extracted")
        except (ExportifyParseError, OSError, UnicodeError) as error:
            app.logger.info("Unable to load playlists for job %s: %s", job_id, error)
            return render_template(
                "index.html",
                error="That playlist selection has expired. Please upload the ZIP again.",
            ), 400

        playlist_index = _valid_playlist_index(playlist_id, len(playlist_data))
        if playlist_index is None:
            return render_template(
                "index.html",
                error="That playlist is not available for this upload.",
            ), 400

        playlist = playlist_data[playlist_index]
        return render_template(
            "processing.html",
            job_id=job_id,
            playlist_id=playlist_id,
            playlist_name=playlist.name,
            total_tracks=len(playlist.tracks),
        )

    @app.post("/processing/<job_id>/<playlist_id>/start")
    def start_processing(job_id: str, playlist_id: str):
        job_directory = _current_job_directory(app, job_id)
        if job_directory is None or not _selection_matches(job_directory, playlist_id):
            return render_template(
                "index.html",
                error="That playlist selection has expired. Please upload the ZIP again.",
            ), 404

        try:
            playlist_data = parse_exportify(job_directory / "extracted")
        except (ExportifyParseError, OSError, UnicodeError) as error:
            app.logger.info("Unable to load playlists for job %s: %s", job_id, error)
            return render_template(
                "index.html",
                error="The playlist selection has expired. Please upload the ZIP again.",
            ), 400

        playlist_index = _valid_playlist_index(playlist_id, len(playlist_data))
        if playlist_index is None:
            return render_template(
                "index.html",
                error="That playlist is not available for this upload.",
            ), 400

        manager = app.config["JOB_MANAGER"]
        existing = manager.get(job_id, playlist_id)
        if existing is not None and existing.status != "queued":
            return jsonify(existing.as_dict()), 409

        playlist = playlist_data[playlist_index]
        job = _build_processing_job(
            app=app,
            job_id=job_id,
            playlist_id=playlist_id,
            playlist=playlist,
            output_dir=_download_dir(app),
            music_library=app.config["MUSIC_LIBRARY"],
        )
        manager.submit(job)
        job.start()
        return jsonify(job.as_dict()), 202

    @app.get("/processing/<job_id>/<playlist_id>/status")
    def processing_status(job_id: str, playlist_id: str):
        job_directory = _current_job_directory(app, job_id)
        if job_directory is None or not _selection_matches(job_directory, playlist_id):
            return jsonify(
                {"error": "That playlist selection has expired. Please upload the ZIP again."}
            ), 404

        job = app.config["JOB_MANAGER"].get(job_id, playlist_id)
        if job is None or job.playlist_id != playlist_id:
            return jsonify({"error": "That processing job could not be found."}), 404
        state = job.as_dict()
        _annotate_artwork(job, state)
        return jsonify(state)

    @app.post("/processing/<job_id>/<playlist_id>/retry")
    def retry_processing(job_id: str, playlist_id: str):
        """Re-resolve the tracks that did not produce a usable local file."""
        job_directory = _current_job_directory(app, job_id)
        if job_directory is None or not _selection_matches(job_directory, playlist_id):
            return render_template(
                "index.html",
                error="That playlist selection has expired. Please upload the ZIP again.",
            ), 404

        job = app.config["JOB_MANAGER"].get(job_id, playlist_id)
        if job is None or job.playlist_id != playlist_id:
            return render_template(
                "index.html", error="That processing job could not be found."
            ), 404
        if job.status in {"queued", "running"}:
            return redirect(url_for("processing", job_id=job_id, playlist_id=playlist_id))
        try:
            retried = job.retry()
        except JobStartError:
            return redirect(url_for("processing", job_id=job_id, playlist_id=playlist_id))
        if not retried:
            return redirect(url_for("processing_result", job_id=job_id, playlist_id=playlist_id))
        app.logger.info(
            "Retrying %d unresolved track(s) job=%s playlist=%s",
            len(retried),
            job_id,
            playlist_id,
        )
        return redirect(url_for("processing", job_id=job_id, playlist_id=playlist_id))

    @app.get("/processing/<job_id>/<playlist_id>/result")
    def processing_result(job_id: str, playlist_id: str):
        job_directory = _current_job_directory(app, job_id)
        if job_directory is None or not _selection_matches(job_directory, playlist_id):
            return render_template(
                "index.html",
                error="That playlist selection has expired. Please upload the ZIP again.",
            ), 404

        job = app.config["JOB_MANAGER"].get(job_id, playlist_id)
        if job is None or job.playlist_id != playlist_id:
            return render_template(
                "index.html",
                error="That processing job could not be found.",
            ), 404
        if job.status == "running" or job.status == "queued":
            return redirect(url_for("processing", job_id=job_id, playlist_id=playlist_id))
        state = job.as_dict()
        _annotate_artwork(job, state)
        return render_template(
            "result.html",
            job_id=job_id,
            playlist_id=playlist_id,
            state=state,
            batch_back=bool(_selected_playlist_ids(job_directory)),
            media_player_available=media_player_available(),
        )

    @app.get("/processing/<job_id>/<playlist_id>/playlist.m3u")
    def download_m3u(job_id: str, playlist_id: str):
        job = app.config["JOB_MANAGER"].get(job_id, playlist_id)
        if job is None or job.playlist_id != playlist_id or job.m3u_path is None:
            return jsonify({"error": "That playlist is not ready to download."}), 404
        m3u_path = Path(job.m3u_path)
        if not m3u_path.is_file():
            return jsonify({"error": "That playlist is not ready to download."}), 404
        name = sanitize_filename_component(job.playlist_name) or job.playlist_id
        # Served as octet-stream (not audio/x-mpegurl) so a WebView that ignores
        # ``Content-Disposition: attachment`` cannot "show" the playlist and open
        # its built-in media player; it can only offer a native save instead.
        return send_file(
            m3u_path,
            as_attachment=True,
            download_name=f"{name}.m3u",
            mimetype="application/octet-stream",
        )

    @app.post("/processing/<job_id>/<playlist_id>/media-player")
    def add_playlist_to_media_player(job_id: str, playlist_id: str):
        """Send the generated playlist to the platform's media player.

        The playlist written by the processing job is reused as-is; nothing is
        regenerated for this action, and the M3U download stays available.
        """
        if not media_player_available():
            return jsonify(
                {"error": "Add to Media Player is only available on macOS and Windows."}
            ), 404
        job_directory = _current_job_directory(app, job_id)
        job = app.config["JOB_MANAGER"].get(job_id, playlist_id)
        if job is None or job.playlist_id != playlist_id or job.status != "completed":
            return jsonify({"error": "That playlist is not ready to import."}), 404
        state = job.as_dict()
        _annotate_artwork(job, state)
        paths = [
            track["local_path"]
            for track in state["tracks"]
            if track["status"] == "complete" and track["local_path"]
        ]
        if not paths:
            return jsonify({"error": "There are no resolved tracks to add."}), 409
        unresolved = len(state["tracks"]) - len(paths)
        try:
            result = add_to_media_player(job.playlist_name, job.m3u_path, paths)
        except MediaPlayerError as error:
            app.logger.warning("Add to Media Player failed job=%s: %s", job_id, error)
            return render_template(
                "result.html",
                job_id=job_id,
                playlist_id=playlist_id,
                state={**state, "media_player_error": str(error)},
                batch_back=bool(_selected_playlist_ids(job_directory)),
                media_player_available=True,
            ), 502
        return render_template(
            "result.html",
            job_id=job_id,
            playlist_id=playlist_id,
            state={
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
            },
            batch_back=bool(_selected_playlist_ids(job_directory)),
            media_player_available=True,
        )

    @app.get("/processing/<job_id>/<playlist_id>/artwork/<int:index>")
    def track_artwork(job_id: str, playlist_id: str, index: int):
        """Serve locally cached artwork for a single track, without network access."""
        job = app.config["JOB_MANAGER"].get(job_id, playlist_id)
        if job is None or job.playlist_id != playlist_id:
            return jsonify({"error": "That processing job could not be found."}), 404
        try:
            track = job.tracks[index]
        except IndexError:
            return jsonify({"error": "That track is not available."}), 404
        path = cached_artwork_path(job.output_dir, track)
        if path is None:
            return jsonify({"error": "No artwork is available for that track."}), 404
        return send_file(path, mimetype="image/jpeg", max_age=3600)

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error):
        return render_template(
            "index.html",
            error="That file is too large to upload.",
        ), 413

    return app


app = create_app()


def _job_directory(upload_root: Path | str, job_id: str) -> Path | None:
    """Resolve a generated job identifier without accepting filesystem paths."""
    if not JOB_ID_PATTERN.fullmatch(job_id):
        return None
    root = Path(upload_root).resolve()
    directory = (root / f"job-{job_id}").resolve()
    if root not in directory.parents or not directory.is_dir():
        return None
    if not (
        (directory / "extracted").is_dir()
        and (directory / "state.json").is_file()
        and (directory / "output").is_dir()
    ):
        return None
    return directory


def _current_job_directory(app: Flask, job_id: str) -> Path | None:
    """Resolve a job only when it belongs to the current upload session."""
    if session.get("job_id") != job_id:
        return None
    return _job_directory(app.config["UPLOAD_ROOT"], job_id)


def _save_job_state(job_directory: Path, state: dict[str, str]) -> None:
    """Persist small workflow state inside the server-owned job directory."""
    state_path = job_directory / "state.json"
    temporary_path = job_directory / "state.json.tmp"
    temporary_path.write_text(json.dumps(state), encoding="utf-8")
    temporary_path.replace(state_path)


def _selection_matches(job_directory: Path, playlist_id: str) -> bool:
    if not playlist_id.isdigit():
        return False
    try:
        state = json.loads((job_directory / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if state.get("selected_playlist_id") == playlist_id:
        return True
    return playlist_id in state.get("selected_playlist_ids", [])


def _selected_playlist_ids(job_directory: Path | None) -> list[str]:
    if job_directory is None:
        return []
    try:
        state = json.loads((job_directory / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return state.get("selected_playlist_ids", [])


def _batch_status(jobs: list[ProcessingJob]) -> dict[str, object]:
    states = []
    for job in jobs:
        state = job.as_dict()
        _annotate_artwork(job, state)
        states.append(state)
    total = sum(int(state["playlist"]["total_tracks"]) for state in states)
    completed = sum(int(state["completed"]) for state in states)
    searched = sum(int(state["searched"]) for state in states)
    return {
        "status": (
            "completed"
            if states and all(state["status"] == "completed" for state in states)
            else "failed"
            if any(state["status"] == "failed" for state in states)
            else "running"
        ),
        "completed": completed,
        "searched": searched,
        "total": total,
        "successful": sum(int(state["successful"]) for state in states),
        "failed": sum(int(state["failed"]) for state in states),
        "started_at": min(
            (int(state["started_at"]) for state in states if isinstance(state["started_at"], int)),
            default=None,
        ),
        "playlists": states,
    }


def _annotate_artwork(job: ProcessingJob, state: dict[str, object]) -> None:
    """Mark each track snapshot with whether cached artwork can be served."""
    tracks = state.get("tracks")
    if not isinstance(tracks, list):
        return
    for item in tracks:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(job.tracks):
            item["artwork"] = False
            continue
        item["artwork"] = cached_artwork_path(job.output_dir, job.tracks[index]) is not None


def _valid_playlist_index(playlist_id: str, playlist_count: int) -> int | None:
    if not playlist_id.isdigit():
        return None
    index = int(playlist_id)
    if index < 0 or index >= playlist_count:
        return None
    return index


def _sweep_old_jobs(app: Flask, *, log: bool = False) -> None:
    """Remove abandoned upload job directories that are past their age limit."""
    try:
        removed = cleanup_jobs(
            app.config["UPLOAD_ROOT"],
            max_age_seconds=app.config["MAX_JOB_AGE"],
            active_job_ids=app.config["JOB_MANAGER"].active_job_ids(),
        )
        if log and removed:
            app.logger.info("Removed %d abandoned upload job(s)", removed)
    except (OSError, ValueError):
        app.logger.exception("Unable to sweep abandoned upload jobs")


def _download_dir(app: Flask) -> Path:
    """Resolve the persistent directory for downloaded MP3s and the M3U.

    Defaults to a stable subfolder inside the music library so downloads
    survive and can be matched by the local resolver on later runs.
    """
    configured = app.config.get("DOWNLOAD_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(app.config["MUSIC_LIBRARY"]).expanduser() / "SpotM3U-downloads"


def _build_processing_job(
    *,
    app: Flask,
    job_id: str,
    playlist_id: str,
    playlist: Playlist,
    output_dir: Path,
    music_library: str | Path,
    m3u_filename: str = "playlist.m3u",
) -> ProcessingJob:
    max_results = int(app.config.get("SEARCH_MAX_RESULTS", 8))
    max_search_workers = int(app.config.get("SEARCH_MAX_WORKERS", 4))
    search_socket_timeout = int(app.config.get("SEARCH_SOCKET_TIMEOUT", 30))
    quality = str(app.config.get("DOWNLOAD_QUALITY", "192"))
    max_download_workers = int(app.config.get("DOWNLOAD_MAX_WORKERS", 2))
    download_timeout = float(app.config.get("DOWNLOAD_TIMEOUT", 600))
    retries = int(app.config.get("DOWNLOAD_RETRIES", 5))
    fragment_retries = int(app.config.get("DOWNLOAD_FRAGMENT_RETRIES", 5))
    socket_timeout = int(app.config.get("DOWNLOAD_SOCKET_TIMEOUT", 30))
    cookies_from_browser = app.config.get("YTDLP_COOKIES_FROM_BROWSER")
    pot_provider_url = app.config.get("YTDLP_POT_PROVIDER_URL")
    pot_provider_home = app.config.get("YTDLP_POT_PROVIDER_HOME")

    def resolver_factory() -> TrackResolver:
        local_resolver = LocalAudioResolver(music_library)
        searcher = OnlineSourceSearcher(
            max_results=max_results,
            max_search_workers=max_search_workers,
            socket_timeout=search_socket_timeout,
        )
        downloader = partial(
            download_track,
            quality=quality,
            retries=retries,
            fragment_retries=fragment_retries,
            socket_timeout=socket_timeout,
            timeout=download_timeout,
            cookies_from_browser=cookies_from_browser,
            pot_provider_url=pot_provider_url,
            pot_provider_home=pot_provider_home,
            ffmpeg_location=locate_ffmpeg_location(),
        )
        return TrackResolver(
            local_resolver,
            output_dir,
            searcher=searcher,
            downloader=downloader,
            cache=DownloadCache(output_dir),
        )

    return ProcessingJob(
        job_id=job_id,
        playlist_id=playlist_id,
        playlist_name=playlist.name,
        tracks=playlist.tracks,
        output_dir=output_dir,
        resolver_factory=resolver_factory,
        max_workers=int(app.config.get("RESOLVE_WORKERS", 4)),
        max_download_workers=max_download_workers,
        m3u_extended=bool(app.config.get("M3U_EXTENDED", True)),
        m3u_relative=bool(app.config.get("M3U_RELATIVE", False)),
        m3u_filename=m3u_filename,
    )


def run() -> None:
    """Run the development web server."""
    _sweep_old_jobs(app, log=True)
    app.run(port=app.config.get("PORT", 5001), debug=True)
