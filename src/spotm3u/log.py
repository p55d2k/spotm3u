"""Diagnostic logging for the track processing pipeline.

Every per-track log record carries a job and track identifier so a failed
track can be traced through its processing stages. Credentials, authentication
secrets, and private data are never logged; the pipeline only logs metadata
that already exists in the playlist models.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .models import Track

PACKAGE_LOGGER = "spotm3u"
DEFAULT_LEVEL = "INFO"
LOG_LEVEL_ENV = "SPOTM3U_LOG_LEVEL"

DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str | int | None = None) -> None:
    """Configure the ``spotm3u`` package logger for developer diagnostics.

    The level comes from ``level``, falling back to the ``SPOTM3U_LOG_LEVEL``
    environment variable, then ``INFO``. Handler installation is idempotent so
    repeated app creation or calls never stack duplicate handlers.
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

    def with_track(self, track: Track) -> "TrackLogger":
        """Return a copy of this logger bound to a specific track."""
        return TrackLogger(self._logger, job_id=self._job_id, track=track)

    def with_job(self, job_id: str | None) -> "TrackLogger":
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
    "PACKAGE_LOGGER",
    "TrackLogger",
    "attach_job_logging",
    "configure_logging",
    "track_identifier",
]