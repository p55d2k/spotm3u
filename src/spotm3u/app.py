"""Flask application for the spotm3u web interface."""

import json
import re
import secrets
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from .audio.resolver import LocalAudioResolver
from .exportify import ExportifyParseError, parse_exportify
from .jobs import JobManager, ProcessingJob
from .models import Playlist
from .normalization import sanitize_filename_component
from .online import OnlineSourceSearcher
from .resolution import TrackResolver
from .uploads import UploadError, default_upload_root, store_upload


DEFAULT_MAX_UPLOAD_SIZE = 50 * 1024 * 1024
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def create_app(config: dict | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config.from_mapping(
        MAX_CONTENT_LENGTH=DEFAULT_MAX_UPLOAD_SIZE,
        UPLOAD_ROOT=default_upload_root(),
        SECRET_KEY=secrets.token_hex(32),
        MUSIC_LIBRARY=str(Path.home() / "Music"),
        JOB_MANAGER=JobManager(),
    )
    if config:
        app.config.update(config)

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
            )
        except UploadError as error:
            return render_template("index.html", error=str(error)), 400
        except (OSError, ValueError):
            app.logger.exception("Unable to store uploaded archive")
            return render_template(
                "index.html",
                error="The upload could not be stored. Please try again.",
            ), 500

        try:
            playlists = parse_exportify(job.extracted)
        except ExportifyParseError as error:
            app.logger.info("Uploaded archive is not a valid Exportify export: %s", error)
            return render_template("index.html", error=str(error)), 400
        except (OSError, UnicodeError):
            app.logger.exception("Unable to read uploaded Exportify archive")
            return render_template(
                "index.html",
                error="The uploaded export could not be read. Please try again.",
            ), 400

        session["job_id"] = job.job_id
        return render_template(
            "playlists.html",
            job_id=job.job_id,
            playlists=playlists,
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
        return redirect(
            url_for("processing", job_id=job_id, playlist_id=playlist_id)
        )

    @app.get("/processing/<job_id>/<playlist_id>")
    def processing(job_id: str, playlist_id: str):
        job_directory = _current_job_directory(app, job_id)
        if job_directory is None or not _selection_matches(
            job_directory, playlist_id
        ):
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
        if job_directory is None or not _selection_matches(
            job_directory, playlist_id
        ):
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
        existing = manager.get(job_id)
        if existing is not None and existing.status != "queued":
            return jsonify(existing.as_dict()), 409

        playlist = playlist_data[playlist_index]
        job = _build_processing_job(
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
        if job_directory is None or not _selection_matches(
            job_directory, playlist_id
        ):
            return jsonify(
                {"error": "That playlist selection has expired. Please upload the ZIP again."}
            ), 404

        job = app.config["JOB_MANAGER"].get(job_id)
        if job is None or job.playlist_id != playlist_id:
            return jsonify(
                {"error": "That processing job could not be found."}
            ), 404
        return jsonify(job.as_dict())

    @app.get("/processing/<job_id>/<playlist_id>/playlist.m3u")
    def download_m3u(job_id: str, playlist_id: str):
        job = app.config["JOB_MANAGER"].get(job_id)
        if job is None or job.playlist_id != playlist_id or job.m3u_path is None:
            return jsonify(
                {"error": "That playlist is not ready to download."}
            ), 404
        m3u_path = Path(job.m3u_path)
        if not m3u_path.is_file():
            return jsonify(
                {"error": "That playlist is not ready to download."}
            ), 404
        name = sanitize_filename_component(job.playlist_name) or job.playlist_id
        return send_file(m3u_path, as_attachment=True, download_name=f"{name}.m3u")

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
    """Resolve a job only when it belongs to the current browser session."""
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
    return state.get("selected_playlist_id") == playlist_id


def _valid_playlist_index(playlist_id: str, playlist_count: int) -> int | None:
    if not playlist_id.isdigit():
        return None
    index = int(playlist_id)
    if index < 0 or index >= playlist_count:
        return None
    return index


def _download_dir(app: Flask) -> Path:
    """Resolve the persistent directory for downloaded MP3s and the M3U.

    Defaults to a stable subfolder inside the music library so downloads
    survive and can be matched by the local resolver on later runs.
    """
    configured = app.config.get("DOWNLOAD_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(app.config["MUSIC_LIBRARY"]).expanduser() / "spotm3u-downloads"


def _build_processing_job(
    *,
    job_id: str,
    playlist_id: str,
    playlist: Playlist,
    output_dir: Path,
    music_library: str | Path,
) -> ProcessingJob:
    def resolver_factory() -> TrackResolver:
        local_resolver = LocalAudioResolver(music_library)
        return TrackResolver(local_resolver, output_dir, searcher=OnlineSourceSearcher())

    return ProcessingJob(
        job_id=job_id,
        playlist_id=playlist_id,
        playlist_name=playlist.name,
        tracks=playlist.tracks,
        output_dir=output_dir,
        resolver_factory=resolver_factory,
    )


def run() -> None:
    """Run the development web server."""
    app.run(debug=True)
