"""Development entry point behind ``uv run dev``.

Starts the same Flask application with the debug reloader so frontend and
backend changes are picked up without rebuilding anything. The UI is used in a
normal browser with full developer tools; the native WebView window is a
production-only shell and is never started here.
"""

from __future__ import annotations

from .app import create_app
from .launcher import DEFAULT_HOST, DEFAULT_PORT, select_port


def main() -> None:
    """Run the Flask development server bound to the loopback interface."""
    app = create_app()
    preferred_port = int(app.config.get("PORT", DEFAULT_PORT))
    port = select_port(preferred_port, DEFAULT_HOST)
    if port != preferred_port:
        app.logger.info("port %d is in use; listening on port %d instead", preferred_port, port)
    app.logger.info("SpotM3U dev server: http://%s:%d/", DEFAULT_HOST, port)
    app.run(host=DEFAULT_HOST, port=port, debug=True, use_reloader=True)
