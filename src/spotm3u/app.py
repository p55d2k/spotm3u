"""Flask application for the spotm3u web interface."""

from flask import Flask, render_template


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template("index.html")

    return app


app = create_app()


def run() -> None:
    """Run the development web server."""
    app.run(debug=True)
