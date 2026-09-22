"""Inspect a written M3U playlist.

The writer produces playlists that reference local audio files. This module
reads one back and reports which of those files are still on disk, so the
application can warn before handing out a playlist that would silently skip
tracks (for example after the user deleted the downloads by hand).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlaylistCheck:
    """The result of checking a playlist's referenced files."""

    entries: tuple[str, ...]
    missing: tuple[Path, ...]

    @property
    def total(self) -> int:
        """Number of file entries in the playlist, duplicates included."""
        return len(self.entries)

    @property
    def missing_count(self) -> int:
        """Number of entries whose file is not on disk."""
        return len(self.missing)

    @property
    def complete(self) -> bool:
        """True when every referenced file still exists."""
        return not self.missing

    def missing_names(self, *, limit: int = 10) -> tuple[str, ...]:
        """Unique missing file names, in playlist order, capped for display."""
        return tuple(dict.fromkeys(path.name for path in self.missing))[:limit]


def m3u_entries(path: str | Path) -> tuple[str, ...]:
    """Return the file entries of an M3U, in order, ignoring comments/blanks."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    entries: list[str] = []
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        entries.append(entry)
    return tuple(entries)


def check_playlist(path: str | Path) -> PlaylistCheck:
    """Check which of a playlist's referenced files are still on disk.

    Relative entries are resolved against the playlist's own directory, which is
    where the writer places the audio when ``m3u.relative`` is enabled. A
    playlist that cannot be read is reported as empty rather than raising: the
    check protects the download, it never blocks it.
    """
    playlist = Path(path)
    base = playlist.parent
    entries = m3u_entries(playlist)
    missing: list[Path] = []
    for entry in entries:
        candidate = Path(entry)
        resolved = candidate if candidate.is_absolute() else base / candidate
        try:
            if not resolved.is_file():
                missing.append(resolved)
        except (OSError, ValueError):
            missing.append(resolved)
    return PlaylistCheck(entries=entries, missing=tuple(missing))


__all__ = ["PlaylistCheck", "check_playlist", "m3u_entries"]
