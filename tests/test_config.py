"""Tests for optional config.toml loading."""

from pathlib import Path

import pytest

from spotm3u.app import create_app
from spotm3u.config import (
    Config,
    ConfigError,
    DEFAULT_MAX_JOB_AGE,
    DEFAULT_MAX_UPLOAD_SIZE,
    discover_config_path,
    load_config,
)


def write_config(directory: Path, content: str) -> Path:
    path = directory / "config.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_defaults_without_a_config_file(tmp_path) -> None:
    config = load_config(tmp_path / "missing.toml")

    assert config.port == 5001
    assert config.resolve_workers == 4
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
    path = write_config(tmp_path, '[nonsense]\nvalue = 1\n')
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
        ("[web]\nport = \"many\"\n", "web.port must be an integer"),
        ("[web]\nport = true\n", "web.port must be an integer"),
        ("[m3u]\nextended = \"yes\"\n", "m3u.extended must be a boolean"),
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
        download_dir = "{tmp_path / 'downloads'}"

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