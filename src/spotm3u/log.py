"""Diagnostic logging for the track processing pipeline.

Every per-track log record carries a job and track identifier so a failed
track can be traced through its processing stages. Credentials, authentication
secrets, and private data are never logged; the pipeline only logs metadata
that already exists in the playlist models.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .models import Track
from .runtime import is_frozen

PACKAGE_LOGGER = "spotm3u"
DEFAULT_LEVEL = "INFO"
LOG_LEVEL_ENV = "SPOTM3U_LOG_LEVEL"
LOG_FILE_ENV = "SPOTM3U_LOG_FILE"
LOG_FILE_NAME = "spotm3u.log"
LOG_FILE_MAX_BYTES = 1_000_000
LOG_FILE_BACKUP_COUNT = 3

DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str | int | None = None) -> None:
    """Configure the ``spotm3u`` package logger for developer diagnostics.

    The level comes from ``level``, falling back to the ``SPOTM3U_LOG_LEVEL``
    environment variable, then ``INFO``. Handler installation is idempotent so
    repeated app creation or calls never stack duplicate handlers. Records are
    also mirrored to the process file log by :func:`configure_file_logging`
    whenever one is active.
    """
    logger = logging.getLogger(PACKAGE_LOGGER)
    requested = level if level is not None else os.environ.get(LOG_LEVEL_ENV, DEFAULT_LEVEL)
    if isinstance(requested, str):
        requested = requested.strip().upper() or DEFAULT_LEVEL
    logger.setLevel(requested)
    if not getattr(logger, "_spotm3u_handlers_configured", False):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATE_FORMAT))
        logger.addHandler(handler)
        logger._spotm3u_handlers_configured = True  # type: ignore[attr-defined]
    logger.propagate = True
    configure_file_logging(requested)


def log_file_path() -> Path:
    """Return the persistent per-user log file for this platform.

    ``SPOTM3U_LOG_FILE`` overrides the default. Below that, logs land in the
    application log directory -- ``%LOCALAPPDATA%\\SpotM3U`` on Windows,
    ``~/Library/Logs/SpotM3U`` on macOS, and ``$XDG_STATE_HOME/spotm3u`` (or
    ``~/.local/state/spotm3u``) on Linux -- so a packaged app logs somewhere
    writable and findable even when installed under Program Files.
    """
    environment = os.environ.get(LOG_FILE_ENV)
    if environment:
        return Path(environment).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "SpotM3U" / "logs" / LOG_FILE_NAME
        return Path.home() / "AppData" / "Local" / "SpotM3U" / "logs" / LOG_FILE_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "SpotM3U" / LOG_FILE_NAME
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "spotm3u" / "logs" / LOG_FILE_NAME


def _coerce_level(level: str | int | None) -> int:
    if isinstance(level, int):
        return level
    name = str(level or DEFAULT_LEVEL).strip().upper() or DEFAULT_LEVEL
    return getattr(logging, name)


def configure_file_logging(
    level: str | int | None = None, *, path: Path | str | None = None
) -> Path | None:
    """Mirror the whole process's log records into a rotating file.

    The handler is attached to the root logger so every logger -- the
    ``spotm3u`` package, Flask's ``app.logger``, and Werkzeug -- shares one
    file. Packaged builds enable it by default because a windowed Windows
    executable has no console; an explicit ``path`` or ``SPOTM3U_LOG_FILE``
    requests a file in any launch mode, and the platform's per-user log
    location is used otherwise. Reconfiguring with a different file replaces
    the handler; calling with the same file is a no-op. Returns the active
    file path, or ``None`` when no file logging is wanted (further falling
    back to the temporary directory if the preferred location is unusable).
    """
    if path is None and os.environ.get(LOG_FILE_ENV) is None and not is_frozen():
        return None
    target = Path(path).expanduser() if path is not None else log_file_path()
    if target.is_dir():
        target = target / LOG_FILE_NAME
    root = logging.getLogger()
    if getattr(root, "_spotm3u_file_log_path", None) == target:
        return target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            target,
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        target = Path(tempfile.gettempdir()) / LOG_FILE_NAME
        try:
            handler = RotatingFileHandler(
                target,
                maxBytes=LOG_FILE_MAX_BYTES,
                backupCount=LOG_FILE_BACKUP_COUNT,
                encoding="utf-8",
            )
        except OSError:
            return None
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATE_FORMAT))
    handler._spotm3u_file_log = True  # type: ignore[attr-defined]
    for old in list(root.handlers):
        if getattr(old, "_spotm3u_file_log", False):
            root.removeHandler(old)
            old.close()
    root.addHandler(handler)
    root._spotm3u_file_log_path = target  # type: ignore[attr-defined]
    requested = _coerce_level(level)
    if root.level == logging.NOTSET or root.level > requested:
        root.setLevel(requested)
    return target


def track_identifier(track: Track) -> str:
    """Return a stable, short identifier for a track in diagnostics.

    Prefers the Spotify track ID when available and falls back to a
    ``title - artists`` label so every track has a traceable name.
    """
    if track is None:
        return "track=?"
    if track.spotify_id:
        return track.spotify_id
    artists = ", ".join(artists for artists in (track.artists or []) if artists)
    return f"{track.title} - {artists}" if artists else track.title or "untitled"


class TrackLogger:
    """Logger that prefixes messages with a job and track processing context.

    Resolvers create one context per track via :meth:`with_track` so every
    stage, search, download, and validation record carries the identifiers
    needed to trace a failed track through its pipeline.
    """

    def __init__(
        self,
        logger: logging.Logger,
        *,
        job_id: str | None = None,
        track: Track | None = None,
    ) -> None:
        self._logger = logger
        self._job_id = job_id
        self._track = track

    def with_track(self, track: Track) -> TrackLogger:
        """Return a copy of this logger bound to a specific track."""
        return TrackLogger(self._logger, job_id=self._job_id, track=track)

    def with_job(self, job_id: str | None) -> TrackLogger:
        """Return a copy of this logger bound to a background job."""
        return TrackLogger(self._logger, job_id=job_id, track=self._track)

    def _context(self) -> str:
        parts: list[str] = []
        if self._job_id:
            parts.append(f"job={self._job_id}")
        if self._track is not None:
            parts.append(f"track={track_identifier(self._track)}")
        return f"[{' '.join(parts)}] " if parts else ""

    def log(self, level: int, msg: str, *args: Any, **kwargs: Any) -> None:
        if not self._logger.isEnabledFor(level):
            return
        self._logger.log(level, f"{self._context()}{msg}", *args, **kwargs)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.log(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.log(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.log(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.log(logging.ERROR, msg, *args, **kwargs)

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("exc_info", True)
        self.log(logging.ERROR, msg, *args, **kwargs)


def attach_job_logging(resolver: object, *, job_id: str | None) -> None:
    """Give a resolver's log records a job context when it supports logging."""
    setter = getattr(resolver, "set_log_context", None)
    if callable(setter):
        logger = getattr(resolver, "_log", None)
        context = (
            logger.with_job(job_id)
            if isinstance(logger, TrackLogger)
            else TrackLogger(logging.getLogger(resolver.__class__.__module__), job_id=job_id)
        )
        setter(context)


__all__ = [
    "DEFAULT_FORMAT",
    "LOG_FILE_ENV",
    "PACKAGE_LOGGER",
    "TrackLogger",
    "attach_job_logging",
    "configure_file_logging",
    "configure_logging",
    "log_file_path",
    "track_identifier",
]
