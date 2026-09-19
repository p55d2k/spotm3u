"""Production entry point for the spotm3u Flask application.

``main()`` is the console-script and PyInstaller target. It starts the same
Flask app served by the development entry point but without the debug reloader,
binds to the loopback interface, and adapts config discovery when the
application runs from a packaged bundle instead of a virtualenv.
"""

from __future__ import annotations

import os

from .app import create_app
from .runtime import bundle_roots, is_frozen

DEFAULT_HOST = "127.0.0.1"


def configure_config_path_for_bundle() -> None:
    """Point config discovery at a ``config.toml`` bundled with the app.

    In development the working directory is the checkout, so ``config.toml``
    is found there. A packaged executable runs from an arbitrary directory, so
    honor an explicit ``SPOTM3U_CONFIG`` first and otherwise look for a
    ``config.toml`` in the bundle, preferring one next to the executable over
    collected data locations. No-op in development.
    """
    if not is_frozen():
        return
    if os.environ.get("SPOTM3U_CONFIG"):
        return
    for candidate in reversed(bundle_roots()):
        bundled = candidate / "config.toml"
        if bundled.is_file():
            os.environ["SPOTM3U_CONFIG"] = str(bundled)
            return


def main() -> None:
    """Run the application server until interrupted."""
    configure_config_path_for_bundle()
    app = create_app()
    host = DEFAULT_HOST
    port = int(app.config.get("PORT", 5001))
    app.logger.info("spotm3u listening on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
