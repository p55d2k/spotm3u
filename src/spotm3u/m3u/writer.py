"""Write source-independent resolved tracks as UTF-8 M3U playlists."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..models import ResolvedTrack


def _iter_tracks(tracks: Iterable[Any] | Any) -> Iterable[Any]:
    if hasattr(tracks, "results"):
        return tracks.results
    return tracks


def _extract_resolved(item: Any) -> Any | None:
    if item is None:
        return None
    if hasattr(item, "successful"):
        if not item.successful or getattr(item, "resolved", None) is None:
            return None
        item = item.resolved
    if isinstance(item, ResolvedTrack):
        if item.status in {"missing", "ambiguous", "rejected", "failed", "uncertain"}:
            return None
        if item.local_path is None:
            return None
        return item
    if hasattr(item, "local_path") and hasattr(item, "track") and item.local_path is not None:
        status = getattr(item, "status", "local")
        if status in {"missing", "ambiguous", "rejected", "failed", "uncertain"}:
            return None
        return item
    return None


def m3u_text(
    tracks: Iterable[ResolvedTrack | Any] | Any,
    *,
    relative_to: str | Path | None = None,
    extended: bool = True,
) -> str:
    """Return M3U content while preserving input order and duplicates."""
    base = Path(relative_to).expanduser() if relative_to is not None else None
    lines = ["#EXTM3U"] if extended else []
    for item in _iter_tracks(tracks):
        resolved = _extract_resolved(item)
        if resolved is None:
            continue
        path = resolved.local_path
        if base is not None:
            try:
                entry = Path(os.path.relpath(path, base)).as_posix()
            except ValueError:
                entry = Path(path).as_posix()
        else:
            entry = Path(path).as_posix()
        if extended:
            track = resolved.track
            duration = (
                (track.duration_ms or 0) // 1000 if track and track.duration_ms is not None else 0
            )
            artists = ", ".join(track.artists) if track and track.artists else ""
            title = track.title if track and track.title else ""
            display = f"{artists} - {title}" if artists else title
            lines.append(f"#EXTINF:{duration},{display}")
        lines.append(entry)
    return "\n".join(lines) + ("\n" if lines else "")


def write_m3u(
    output: str | Path,
    tracks: Iterable[ResolvedTrack | Any] | Any,
    *,
    relative_to: str | Path | None = None,
    extended: bool = True,
) -> int:
    """Write an M3U file and return the number of entries written."""
    text = m3u_text(tracks, relative_to=relative_to, extended=extended)
    out_path = Path(output).expanduser()
    if out_path.parent:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return sum(1 for item in _iter_tracks(tracks) if _extract_resolved(item) is not None)
