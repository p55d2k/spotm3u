"""Optional TOML configuration for the spotm3u app.

A ``config.toml`` file is never required. Every setting has a built-in
default, so the app works without one. When a file exists it is loaded from
the ``SPOTM3U_CONFIG`` environment variable, else ``./config.toml`` in the
current working directory, and its values are merged over the defaults. The
file exists purely for user customization.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MAX_UPLOAD_SIZE = 50 * 1024 * 1024
DEFAULT_MAX_DECOMPRESSED_SIZE = 512 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_ENTRIES = 10_000
DEFAULT_MAX_JOB_AGE = 24 * 60 * 60
SUPPORTED_COOKIE_BROWSERS = frozenset(
    {"brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale"}
)


class ConfigError(ValueError):
    """Raised when a config.toml value is invalid."""


@dataclass(frozen=True)
class Config:
    """Optional user-configurable settings with built-in defaults."""

    # [web]
    port: int = 5001
    upload_root: str | None = None
    music_library: str | None = None
    download_dir: str | None = None
    resolve_workers: int = 4
    log_level: str | None = None
    # [upload]
    max_upload_size: int = DEFAULT_MAX_UPLOAD_SIZE
    max_decompressed_size: int = DEFAULT_MAX_DECOMPRESSED_SIZE
    max_archive_entries: int = DEFAULT_MAX_ARCHIVE_ENTRIES
    max_job_age: int = DEFAULT_MAX_JOB_AGE
    # [search]
    max_results: int = 8
    max_search_workers: int = 4
    search_socket_timeout: int = 30
    # [download]
    audio_quality: str = "192"
    max_download_workers: int = 2
    download_timeout: int = 600
    retries: int = 5
    fragment_retries: int = 5
    socket_timeout: int = 30
    cookies_from_browser: str | None = None
    pot_provider_url: str | None = None
    pot_provider_home: str | None = None
    # [m3u]
    m3u_extended: bool = True
    m3u_relative: bool = False

    def to_app_config(self) -> dict[str, Any]:
        """Return the Flask-friendly mapping for these settings."""
        values: dict[str, Any] = {
            "PORT": self.port,
            "MAX_CONTENT_LENGTH": self.max_upload_size,
            "MAX_DECOMPRESSED_SIZE": self.max_decompressed_size,
            "MAX_ARCHIVE_ENTRIES": self.max_archive_entries,
            "MAX_JOB_AGE": self.max_job_age,
            "RESOLVE_WORKERS": self.resolve_workers,
            "SEARCH_MAX_RESULTS": self.max_results,
            "SEARCH_MAX_WORKERS": self.max_search_workers,
            "SEARCH_SOCKET_TIMEOUT": self.search_socket_timeout,
            "DOWNLOAD_QUALITY": self.audio_quality,
            "DOWNLOAD_MAX_WORKERS": self.max_download_workers,
            "DOWNLOAD_TIMEOUT": self.download_timeout,
            "DOWNLOAD_RETRIES": self.retries,
            "DOWNLOAD_FRAGMENT_RETRIES": self.fragment_retries,
            "DOWNLOAD_SOCKET_TIMEOUT": self.socket_timeout,
            "YTDLP_COOKIES_FROM_BROWSER": self.cookies_from_browser,
            "YTDLP_POT_PROVIDER_URL": self.pot_provider_url,
            "YTDLP_POT_PROVIDER_HOME": self.pot_provider_home,
            "M3U_EXTENDED": self.m3u_extended,
            "M3U_RELATIVE": self.m3u_relative,
        }
        if self.log_level:
            values["LOG_LEVEL"] = self.log_level
        if self.upload_root:
            values["UPLOAD_ROOT"] = str(Path(self.upload_root).expanduser())
        if self.music_library:
            values["MUSIC_LIBRARY"] = str(Path(self.music_library).expanduser())
        if self.download_dir:
            values["DOWNLOAD_DIR"] = str(Path(self.download_dir).expanduser())
        return values


_FIELD_ATTRIBUTES: dict[str, str] = {
    "web.port": "port",
    "web.upload_root": "upload_root",
    "web.music_library": "music_library",
    "web.download_dir": "download_dir",
    "web.resolve_workers": "resolve_workers",
    "web.log_level": "log_level",
    "upload.max_upload_size": "max_upload_size",
    "upload.max_decompressed_size": "max_decompressed_size",
    "upload.max_archive_entries": "max_archive_entries",
    "upload.max_job_age": "max_job_age",
    "search.max_results": "max_results",
    "search.max_search_workers": "max_search_workers",
    "search.socket_timeout": "search_socket_timeout",
    "download.audio_quality": "audio_quality",
    "download.workers": "max_download_workers",
    "download.timeout": "download_timeout",
    "download.retries": "retries",
    "download.fragment_retries": "fragment_retries",
    "download.socket_timeout": "socket_timeout",
    "download.cookies_from_browser": "cookies_from_browser",
    "download.pot_provider_url": "pot_provider_url",
    "download.pot_provider_home": "pot_provider_home",
    "m3u.extended": "m3u_extended",
    "m3u.relative": "m3u_relative",
}

_FIELD_TYPES: dict[str, type] = {
    attribute: str if fields.default is None else type(fields.default)
    for attribute, fields in Config.__dataclass_fields__.items()
}


def load_config(path: str | Path | None = None) -> Config:
    """Load an optional config file, or return built-in defaults."""
    if path is None:
        return Config()
    config_path = Path(path).expanduser()
    if not config_path.is_file():
        return Config()
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"cannot read config file {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config file must contain TOML tables")

    merged: dict[str, Any] = {}
    for file_key, attribute in _FIELD_ATTRIBUTES.items():
        section, name = file_key.split(".")
        raw_section = data.get(section)
        if not isinstance(raw_section, dict) or name not in raw_section:
            continue
        expected = _FIELD_TYPES[attribute]
        merged[attribute] = _coerce(expected, file_key, raw_section[name])
    return Config(**merged)


def _coerce(expected: type, key: str, value: Any) -> Any:
    if expected is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{key} must be a boolean")
        return value
    if expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{key} must be an integer")
        return value
    if not isinstance(value, expected):
        raise ConfigError(f"{key} must be a {expected.__name__}")
    if key == "download.cookies_from_browser":
        browser = value.casefold()
        if browser not in SUPPORTED_COOKIE_BROWSERS:
            supported = ", ".join(sorted(SUPPORTED_COOKIE_BROWSERS))
            raise ConfigError(f"{key} must be one of: {supported}")
    return value


def discover_config_path() -> Path | None:
    """Return the config.toml path to load, if one exists."""
    env_path = os.environ.get("SPOTM3U_CONFIG")
    if env_path:
        candidate = Path(env_path).expanduser()
        if not candidate.is_file():
            raise ConfigError(
                f"SPOTM3U_CONFIG points to a missing file: {candidate}"
            )
        return candidate
    candidate = Path.cwd() / "config.toml"
    return candidate if candidate.is_file() else None


def load_user_config() -> Config:
    """Load the discovered optional config file, or return defaults."""
    config = load_config(discover_config_path())
    browser = os.environ.get("SPOTM3U_YTDLP_BROWSER")
    if browser is None:
        return config
    if browser == "":
        return Config(**{**config.__dict__, "cookies_from_browser": None})
    value = _coerce(str, "download.cookies_from_browser", browser)
    return Config(**{**config.__dict__, "cookies_from_browser": value.casefold()})


__all__ = [
    "Config",
    "ConfigError",
    "DEFAULT_MAX_ARCHIVE_ENTRIES",
    "DEFAULT_MAX_DECOMPRESSED_SIZE",
    "DEFAULT_MAX_JOB_AGE",
    "DEFAULT_MAX_UPLOAD_SIZE",
    "discover_config_path",
    "load_config",
    "load_user_config",
]