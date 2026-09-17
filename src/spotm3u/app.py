"""Flask application for the spotm3u web interface."""

from flask import Flask, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from .uploads import UploadError, default_upload_root, store_upload


DEFAULT_MAX_UPLOAD_SIZE = 50 * 1024 * 1024


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

        return (
            render_template("upload_received.html", job_id=job.job_id),
            201,
        )

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error):
        return render_template(
            "index.html",
            error="That file is too large to upload.",
        ), 413

    return app


app = create_app()


def run() -> None:
    """Run the development web server."""
    app.run(debug=True)
