"""Flask application for the SpotM3U web interface."""

import json
import logging
import secrets
from pathlib import Path

from flask import Flask

from .api import register_api
from .artwork import (
    set_album_artwork_enabled,
    set_artist_artwork_enabled,
    set_artwork_verify_local,
)
from .artwork_cache import set_artwork_memory_limit
from .artwork_sources import (
    set_artist_search_limit,
    set_artist_verification_limit,
    set_artwork_request_timeout,
    set_musicbrainz_artist_limit,
)
from .config import load_user_config
from .frontend import register_frontend
from .jobs import JobManager
from .log import PACKAGE_LOGGER, configure_logging
from .lyrics import set_lyrics_enabled
from .metadata import (
    set_id3_tags_enabled,
    set_metadata_enabled,
)
from .online import describe_youtube_setup
from .online.youtube_setup import set_pot_provider_timeout
from .uploads import default_upload_root


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

    set_metadata_enabled(bool(app.config.get("METADATA_ENABLED", True)))
    set_id3_tags_enabled(bool(app.config.get("METADATA_TAGS", True)))
    set_artwork_verify_local(bool(app.config.get("ARTWORK_VERIFY_LOCAL", True)))
    set_artwork_request_timeout(int(app.config.get("ARTWORK_REQUEST_TIMEOUT", 15)))
    set_artist_search_limit(int(app.config.get("ARTWORK_ARTIST_SEARCH_LIMIT", 25)))
    set_musicbrainz_artist_limit(int(app.config.get("ARTWORK_MUSICBRAINZ_ARTIST_LIMIT", 10)))
    set_artist_verification_limit(int(app.config.get("ARTWORK_ARTIST_VERIFICATION_LIMIT", 3)))
    set_artwork_memory_limit(int(app.config.get("ARTWORK_MEMORY_CACHE_SIZE", 1024)))
    set_album_artwork_enabled(bool(app.config.get("ARTWORK_ALBUM_ARTWORK", True)))
    set_artist_artwork_enabled(bool(app.config.get("ARTWORK_ARTIST_ARTWORK", True)))
    set_lyrics_enabled(bool(app.config.get("LYRICS_ENABLED", True)))
    set_pot_provider_timeout(int(app.config.get("YTDLP_POT_PROVIDER_TIMEOUT", 5)))

    report = describe_youtube_setup(
        cookies_from_browser=app.config.get("YTDLP_COOKIES_FROM_BROWSER"),
        pot_provider_url=app.config.get("YTDLP_POT_PROVIDER_URL"),
        pot_provider_home=app.config.get("YTDLP_POT_PROVIDER_HOME"),
    )
    logging.getLogger(PACKAGE_LOGGER).info("youtube setup: %s", json.dumps(report, sort_keys=True))

    # The JSON API the React frontend uses (see spotm3u.api and docs/api.md).
    register_api(app)

    # The built React application, served at ``/`` (see spotm3u.frontend).
    register_frontend(app)

    return app
