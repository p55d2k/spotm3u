"""Flask application for the spotm3u web interface."""

import re
from pathlib import Path

from flask import Flask, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from .exportify import ExportifyParseError, parse_exportify
from .uploads import UploadError, default_upload_root, store_upload


DEFAULT_MAX_UPLOAD_SIZE = 50 * 1024 * 1024
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def create_app(config: dict | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config.from_mapping(
        MAX_CONTENT_LENGTH=DEFAULT_MAX_UPLOAD_SIZE,
        UPLOAD_ROOT=default_upload_root(),
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

        return render_template(
            "playlists.html",
            job_id=job.job_id,
            playlists=playlists,
        ), 201

    @app.get("/playlists/<job_id>")
    def playlists(job_id: str):
        job_directory = _job_directory(app.config["UPLOAD_ROOT"], job_id)
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
    if not (directory / "extracted").is_dir():
        return None
    return directory


def run() -> None:
    """Run the development web server."""
    app.run(debug=True)
