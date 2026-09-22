"""Persistent reuse of previously validated downloads.

A recording that has already been resolved successfully is recorded in a
manifest inside the download directory together with normalized identity
metadata (artist, title core, version markers, duration, source identity).
Later runs reuse that file instead of downloading the recording again.

Reuse is identity-based, never filename-based: a file is only returned when
its manifest record shows a strong association with the requested recording
and the file itself still passes audio validation.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..models import Track
from ..normalization import normalize_artists, normalize_cjk
from .audio_validation import validate_downloaded_audio
from .ranking import split_title

MANIFEST_NAME = ".spotm3u-cache.json"

# Mirrors the duration tolerance used by audio validation.
_DURATION_TOLERANCE_S = 3.0
_DURATION_TOLERANCE_RATIO = 0.08

_YT_VIDEO_RE = re.compile(
    r"(?:youtube\.com|music\.youtube\.com|m\.youtube\.com)/"
    r"(?:watch\?.*?\bv=|shorts/|embed/|live/|v/)([A-Za-z0-9_-]{6,})",
    re.IGNORECASE,
)
_YT_SHORT_RE = re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,})")

_MANIFEST_LOCK = threading.Lock()


def source_identity(url: str) -> str:
    """Return a stable identity for an online source URL.

    YouTube video IDs identify the same recording across URL forms. For other
    sites the normalized host and path are used.
    """
    if not url:
        return ""
    match = _YT_VIDEO_RE.search(url) or _YT_SHORT_RE.search(url)
    if match:
        return f"youtube:{match.group(1)}"
    parsed = urlparse(url)
    return f"{parsed.netloc.casefold()}:{parsed.path.rstrip('/').casefold()}"


def cache_metadata_key(track: Track) -> str:
    """Build the recording identity key from normalized track metadata."""
    core, versions = split_title(track.title)
    artists = normalize_cjk(normalize_artists(track.artists))
    version_text = " ".join(sorted(versions))
    parts = [part for part in (artists, core, version_text) if part]
    return " || ".join(parts)


class DownloadCache:
    """Persist and reuse validated downloads inside a download directory."""

    def __init__(self, download_dir: str | Path) -> None:
        self.download_dir = Path(download_dir)
        self._manifest_path = self.download_dir / MANIFEST_NAME

    def lookup(self, track: Track, url: str) -> Path | None:
        """Return a validated cached file strongly associated with the recording.

        ``url`` is the online source being considered. A matching stored source
        identity is the strongest signal; otherwise the cached entry must share
        the requested artist/title-core/version identity and a compatible
        duration. The cached file must still pass audio validation against
        ``track``.
        """
        if not self._manifest_path.is_file():
            return None
        requested_key = cache_metadata_key(track)
        requested_source = source_identity(url)
        stale_entry = False
        for entry in self._load_entries():
            source_hit = source_identity(_text(entry.get("source_url"))) == requested_source
            metadata_hit = _text(entry.get("key")) == requested_key and _duration_compatible(
                track, entry
            )
            if not (source_hit or metadata_hit):
                continue
            path = self._resolve_cached_file(entry)
            if path is None:
                # The file was deleted outside SpotM3U: remember that so the
                # manifest is healed once, then keep looking for a usable entry.
                stale_entry = True
                continue
            if validate_downloaded_audio(track, path).status == "valid":
                if stale_entry:
                    self.prune()
                return path
        if stale_entry:
            self.prune()
        return None

    def prune(self) -> int:
        """Drop manifest entries whose audio file is missing; return the count.

        Users delete downloads by hand (emptying the download folder), which
        leaves the manifest pointing at files that no longer exist. Pruning
        keeps the cache an accurate record of what is actually on disk, so a
        later run re-downloads instead of trusting a stale entry.
        """
        with _MANIFEST_LOCK:
            entries = self._load_entries()
            if not entries:
                return 0
            kept = [entry for entry in entries if self._resolve_cached_file(entry) is not None]
            removed = len(entries) - len(kept)
            if removed:
                self._write_entries(kept)
            return removed

    def store(self, track: Track, url: str, path: str | Path) -> None:
        """Record a successfully validated download for later reuse."""
        resolved_dir = self.download_dir.resolve()
        file_path = Path(path)
        try:
            file_path = file_path.resolve()
            relative = file_path.relative_to(resolved_dir)
        except (OSError, ValueError):
            relative = Path(file_path.name)

        core, versions = split_title(track.title)
        entry: dict[str, Any] = {
            "key": cache_metadata_key(track),
            "title": track.title,
            "artists": list(track.artists),
            "core": core,
            "version": " ".join(sorted(versions)) or None,
            "duration_s": _seconds(track.duration_ms),
            "source_url": url,
            "file": str(relative),
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        with _MANIFEST_LOCK:
            identity = source_identity(url)
            entries = [
                existing
                # Replace this source's record and forget entries whose file
                # was deleted by hand, so the manifest never outlives the audio
                # it describes.
                for existing in self._load_entries()
                if source_identity(_text(existing.get("source_url"))) != identity
                and self._resolve_cached_file(existing) is not None
            ]
            entries.append(entry)
            self._write_entries(entries)

    def _load_entries(self) -> list[dict[str, Any]]:
        try:
            raw = self._manifest_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            return []
        return [entry for entry in data["entries"] if isinstance(entry, dict)]

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        payload = {"version": 1, "entries": entries}
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
            temporary = self._manifest_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self._manifest_path)
        except OSError:
            return

    def _resolve_cached_file(self, entry: dict[str, Any]) -> Path | None:
        try:
            path = (self.download_dir / entry["file"]).resolve()
            path.relative_to(self.download_dir.resolve())
        except (KeyError, TypeError, ValueError, OSError):
            return None
        return path if path.is_file() else None


def _duration_compatible(track: Track, entry: dict[str, Any]) -> bool:
    requested = _seconds(track.duration_ms)
    cached = _number(entry.get("duration_s"))
    if requested is None or cached is None:
        return True
    return abs(cached - requested) <= max(
        _DURATION_TOLERANCE_S, requested * _DURATION_TOLERANCE_RATIO
    )


def _seconds(value: int | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value) / 1000
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value) if value is not None else ""


__all__ = [
    "DownloadCache",
    "MANIFEST_NAME",
    "cache_metadata_key",
    "source_identity",
]
