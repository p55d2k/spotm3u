"""Tests for optional config.toml loading."""

import re
from pathlib import Path

import pytest

from spotm3u.app import create_app
from spotm3u.config import (
    _FIELD_ATTRIBUTES,
    DEFAULT_MAX_JOB_AGE,
    DEFAULT_MAX_UPLOAD_SIZE,
    Config,
    ConfigError,
    discover_config_path,
    load_config,
)

# The repository's own ``config.toml`` is a documented, entirely optional
# sample: its header promises that deleting it changes nothing, and it is what
# runs from the project root pick up. Both promises are asserted below, so a
# new setting that never reaches the sample -- or a sample value that quietly
# differs from the built-in default -- fails here instead of shipping.
SAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


def _documented_settings(path: Path) -> set[str]:
    """Return the ``section.field`` names the sample config mentions."""
    documented: set[str] = set()
    section = None
    for line in path.read_text(encoding="utf-8").splitlines():
        header = re.fullmatch(r"\[([a-z_][a-z0-9_]*)\]", line)
        if header:
            section = header.group(1)
            continue
        # Only whole ``field = value`` lines count, commented or not, so prose
        # and the indented shell examples in the header are ignored.
        field = re.fullmatch(r"#?\s*([a-z_][a-z0-9_]*)\s*=\s*\S+\s*", line)
        if section and field:
            documented.add(f"{section}.{field.group(1)}")
    return documented


def test_sample_config_only_documents_real_settings() -> None:
    assert _documented_settings(SAMPLE_CONFIG) == set(_FIELD_ATTRIBUTES)


def test_sample_config_matches_the_built_in_defaults() -> None:
    assert load_config(SAMPLE_CONFIG) == Config()


def write_config(directory: Path, content: str) -> Path:
    path = directory / "config.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_defaults_without_a_config_file(tmp_path) -> None:
    config = load_config(tmp_path / "missing.toml")

    assert config.port == 5001
    assert config.resolve_workers == 3
    assert config.max_upload_size == DEFAULT_MAX_UPLOAD_SIZE
    assert config.max_job_age == DEFAULT_MAX_JOB_AGE
    assert config.max_search_workers == 4
    assert config.search_socket_timeout == 30
    assert config.max_download_workers == 2
    assert config.download_timeout == 600
    assert config.cookies_from_browser is None
    assert config.pot_provider_url is None
    assert config.pot_provider_home is None
    assert config.m3u_extended is True
    assert config.m3u_relative is False
    assert config.fast_mode is False
    assert config.metadata_enabled is True
    assert config.metadata_tags is True
    assert config.artwork_verify_local is True
    assert config.artwork_album_artwork is True
    assert config.artwork_artist_artwork is True
    assert config.lyrics_enabled is True


def test_load_config_parses_known_values(tmp_path) -> None:
    path = write_config(
        tmp_path,
        """
        [web]
        port = 9000
        music_library = "~/Songs"
        download_dir = "~/Songs/spotm3u"
        resolve_workers = 2

        [upload]
        max_upload_size = 10485760
        max_job_age = 3600

        [search]
        max_results = 12
        max_search_workers = 2
        socket_timeout = 15

        [download]
        audio_quality = "320"
        workers = 1
        timeout = 300
        retries = 3
        fragment_retries = 2
        socket_timeout = 60
        cookies_from_browser = "Firefox"
        pot_provider_url = "http://127.0.0.1:8080"

        [m3u]
        extended = true
        relative = true

        [fast]
        enabled = true

        [metadata]
        enabled = false
        tags = false

        [artwork]
        verify_local = false
        album_artwork = false
        artist_artwork = false

        [lyrics]
        enabled = false
        """,
    )

    config = load_config(path)

    assert config.port == 9000
    assert config.music_library == "~/Songs"
    assert config.download_dir == "~/Songs/spotm3u"
    assert config.resolve_workers == 2
    assert config.max_upload_size == 10485760
    assert config.max_job_age == 3600
    assert config.max_results == 12
    assert config.max_search_workers == 2
    assert config.search_socket_timeout == 15
    assert config.audio_quality == "320"
    assert config.max_download_workers == 1
    assert config.download_timeout == 300
    assert config.retries == 3
    assert config.fragment_retries == 2
    assert config.socket_timeout == 60
    assert config.cookies_from_browser == "Firefox"
    assert config.pot_provider_url == "http://127.0.0.1:8080"
    assert config.m3u_extended is True
    assert config.m3u_relative is True
    assert config.fast_mode is True
    assert config.metadata_enabled is False
    assert config.metadata_tags is False
    assert config.artwork_verify_local is False
    assert config.artwork_album_artwork is False
    assert config.artwork_artist_artwork is False
    assert config.lyrics_enabled is False


def test_unknown_keys_are_ignored(tmp_path) -> None:
    path = write_config(tmp_path, '[web]\nmade_up_option = "x"\n')
    config = load_config(path)
    assert config == Config()


def test_invalid_cookie_browser_is_rejected(tmp_path) -> None:
    path = write_config(
        tmp_path,
        '[download]\ncookies_from_browser = "my-browser"\n',
    )
    with pytest.raises(ConfigError, match="cookies_from_browser"):
        load_config(path)


def test_unexposed_sections_are_ignored(tmp_path) -> None:
    path = write_config(tmp_path, "[nonsense]\nvalue = 1\n")
    assert load_config(path) == Config()


def test_partial_file_keeps_other_defaults(tmp_path) -> None:
    path = write_config(tmp_path, "[web]\nport = 8080\n")
    config = load_config(path)
    assert config.port == 8080
    assert config.max_upload_size == DEFAULT_MAX_UPLOAD_SIZE
    assert config.m3u_relative is False


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('[web]\nport = "many"\n', "web.port must be an integer"),
        ("[web]\nport = true\n", "web.port must be an integer"),
        ('[m3u]\nextended = "yes"\n', "m3u.extended must be a boolean"),
        ("[artwork]\nverify_local = 1\n", "artwork.verify_local must be a boolean"),
        ("[artwork]\nartist_artwork = 1\n", "artwork.artist_artwork must be a boolean"),
        ("[search]\nmax_results = []\n", "search.max_results must be an integer"),
    ],
)
def test_invalid_types_raise_config_error(tmp_path, content, message) -> None:
    path = write_config(tmp_path, content)
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_malformed_toml_raises_config_error(tmp_path) -> None:
    path = write_config(tmp_path, "[web\nport = 8080\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_to_app_config_expands_user_paths(tmp_path) -> None:
    config = load_config(
        write_config(
            tmp_path,
            '[web]\nmusic_library = "~/Music"\nupload_root = "~/uploads"\n',
        )
    )
    mapping = config.to_app_config()
    assert mapping["MUSIC_LIBRARY"] == str(Path.home() / "Music")
    assert mapping["UPLOAD_ROOT"] == str(Path.home() / "uploads")


def test_discover_config_path_reads_cwd(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert discover_config_path() is None

    write_config(tmp_path, "[web]\nport = 9000\n")
    assert discover_config_path() == tmp_path / "config.toml"


def test_discover_config_path_uses_env_var(tmp_path, monkeypatch) -> None:
    custom = tmp_path / "custom.toml"
    custom.write_text("[web]\nport = 9001\n", encoding="utf-8")
    monkeypatch.setenv("SPOTM3U_CONFIG", str(custom))
    monkeypatch.chdir(tmp_path)
    assert discover_config_path() == custom


def test_discover_config_path_rejects_missing_env_target(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPOTM3U_CONFIG", str(tmp_path / "nope.toml"))
    with pytest.raises(ConfigError, match="missing file"):
        discover_config_path()


def test_create_app_loads_config_toml(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    write_config(
        tmp_path,
        f"""
        [web]
        music_library = "{music}"
        port = 8080
        download_dir = "{tmp_path / "downloads"}"

        [m3u]
        relative = true
        """,
    )

    app = create_app()

    assert app.config["MUSIC_LIBRARY"] == str(music)
    assert app.config["PORT"] == 8080
    assert app.config["DOWNLOAD_DIR"] == str(tmp_path / "downloads")
    assert app.config["M3U_RELATIVE"] is True
    assert app.config["MAX_CONTENT_LENGTH"] == DEFAULT_MAX_UPLOAD_SIZE


def test_create_app_config_argument_overrides_file(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    write_config(tmp_path, f'[web]\nmusic_library = "{tmp_path / "from-file"}"\n')

    app = create_app({"MUSIC_LIBRARY": str(music)})

    assert app.config["MUSIC_LIBRARY"] == str(music)


def test_create_app_wires_artwork_verify_local(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork

    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    create_app()
    assert artwork._ARTWORK_VERIFY_LOCAL is True

    create_app({"ARTWORK_VERIFY_LOCAL": False})
    assert artwork._ARTWORK_VERIFY_LOCAL is False

    create_app()
    assert artwork._ARTWORK_VERIFY_LOCAL is True


def test_create_app_wires_artist_artwork(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork

    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    create_app()
    assert artwork._ARTIST_ARTWORK_ENABLED is True

    create_app({"ARTWORK_ARTIST_ARTWORK": False})
    assert artwork._ARTIST_ARTWORK_ENABLED is False

    create_app()
    assert artwork._ARTIST_ARTWORK_ENABLED is True


def test_create_app_wires_embedding_options(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork, metadata

    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    create_app()
    assert metadata.metadata_enabled() is True
    assert metadata.id3_tags_enabled() is True
    assert artwork.album_artwork_enabled() is True

    create_app({"METADATA_ENABLED": False, "METADATA_TAGS": False, "ARTWORK_ALBUM_ARTWORK": False})
    assert metadata.metadata_enabled() is False
    assert metadata.id3_tags_enabled() is False
    assert artwork.album_artwork_enabled() is False

    create_app()
    assert metadata.metadata_enabled() is True
    assert metadata.id3_tags_enabled() is True
    assert metadata.album_artwork_enabled() is True


def test_create_app_wires_lyrics_enabled(tmp_path, monkeypatch) -> None:
    from spotm3u import lyrics

    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    create_app()
    assert lyrics.lyrics_enabled() is True

    create_app({"LYRICS_ENABLED": False})
    assert lyrics.lyrics_enabled() is False

    create_app()
    assert lyrics.lyrics_enabled() is True


def test_create_app_wires_artwork_request_timeout(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork_sources

    monkeypatch.setattr(artwork_sources, "_REQUEST_TIMEOUT", artwork_sources._REQUEST_TIMEOUT)
    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    create_app()
    assert artwork_sources._REQUEST_TIMEOUT == 15

    create_app({"ARTWORK_REQUEST_TIMEOUT": 5})
    assert artwork_sources._REQUEST_TIMEOUT == 5


def test_create_app_wires_pot_provider_timeout(tmp_path, monkeypatch) -> None:
    from spotm3u.online import youtube_setup

    monkeypatch.setattr(youtube_setup, "_POT_PROVIDER_TIMEOUT", youtube_setup._POT_PROVIDER_TIMEOUT)
    monkeypatch.delenv("SPOTM3U_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    create_app()
    assert youtube_setup._POT_PROVIDER_TIMEOUT == 5.0

    create_app({"YTDLP_POT_PROVIDER_TIMEOUT": 2})
    assert youtube_setup._POT_PROVIDER_TIMEOUT == 2.0


def test_config_file_sets_timeout_settings(tmp_path) -> None:
    path = write_config(
        tmp_path,
        "[artwork]\nrequest_timeout = 5\n\n[update]\nrequest_timeout = 2\n"
        "\n[download]\npot_provider_timeout = 3\n",
    )

    config = load_config(path)
    mapping = config.to_app_config()

    assert config.artwork_request_timeout == 5
    assert config.update_request_timeout == 2
    assert config.pot_provider_timeout == 3
    assert mapping["ARTWORK_REQUEST_TIMEOUT"] == 5
    assert mapping["UPDATE_REQUEST_TIMEOUT"] == 2
    assert mapping["YTDLP_POT_PROVIDER_TIMEOUT"] == 3
