"""Write source-independent resolved tracks as UTF-8 M3U playlists."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable
import os

from ..models import ResolvedTrack


def m3u_text(
    tracks: Iterable[ResolvedTrack],
    *,
    relative_to: str | Path | None = None,
    extended: bool = True,
) -> str:
    """Return M3U content while preserving input order and duplicates."""
    base = Path(relative_to).expanduser() if relative_to is not None else None
    lines = ["#EXTM3U"] if extended else []
    for resolved in tracks:
        path = resolved.local_path
        entry = str(path if base is None else os.path.relpath(path, base))
        if extended:
            duration = (resolved.track.duration_ms or 0) // 1000
            artists = ", ".join(resolved.track.artists)
            lines.append(f"#EXTINF:{duration},{artists} - {resolved.track.title}")
        lines.append(entry)
    return "\n".join(lines) + ("\n" if lines else "")


def write_m3u(
    output: str | Path,
    tracks: Iterable[ResolvedTrack],
    *,
    relative_to: str | Path | None = None,
    extended: bool = True,
) -> int:
    """Write an M3U file and return the number of entries written."""
    entries = list(tracks)
    Path(output).expanduser().write_text(
        m3u_text(entries, relative_to=relative_to, extended=extended),
        encoding="utf-8",
    )
    return len(entries)
