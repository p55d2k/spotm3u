"""Windows media-player handoff for a generated playlist.

Windows has no Apple-Music-equivalent playlist-import API, so the platform
integration stays standards-based: the generated ``.m3u`` (the exact file the
user can also download) is opened with its default file association. Any
installed player that understands M3U -- VLC, foobar2000, Windows Media Player,
MusicBee, and so on -- picks it up from there.

Paths are handled with :mod:`pathlib` and passed to the association as one
argument, so drive letters, backslashes, spaces, and Unicode characters are
preserved. No shell is involved and no user input is interpolated into a
command line.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from .result import MediaPlayerError, MediaPlayerResult

WINDOWS_PLATFORM = "win32"


def windows_media_player_available() -> bool:
    """Return whether the current host supports the Windows handoff."""
    return sys.platform == WINDOWS_PLATFORM


def add_to_windows_media_player(
    playlist_path: str | Path | None,
    *,
    opener: Callable[[Path], None] | None = None,
) -> MediaPlayerResult:
    """Open the generated playlist with the default associated media player.

    The playlist is never regenerated here: ``playlist_path`` is the M3U the
    processing job already wrote. A missing file association (or an unreadable
    playlist) is reported as a failure so the UI can suggest the manual
    download, which keeps working regardless of media-player integration.
    """
    if not windows_media_player_available():
        raise MediaPlayerError("Add to Media Player is only available on Windows here.")
    if playlist_path is None:
        raise MediaPlayerError("The generated playlist is not available.")
    path = Path(playlist_path).expanduser()
    if not path.is_file():
        raise MediaPlayerError("The generated playlist is no longer available.")
    resolved = path.resolve()
    open_playlist = opener or _default_opener
    try:
        open_playlist(resolved)
    except OSError as exc:
        raise MediaPlayerError(
            "Windows could not open the playlist in a media player. "
            "Download the playlist and open it with a player that supports M3U."
        ) from exc
    return MediaPlayerResult(
        action="opened",
        message="Playlist opened in your default media player.",
    )


def _default_opener(path: Path) -> None:
    """Open ``path`` with its default association through ``os.startfile``.

    ``os.startfile`` only exists on Windows, and it takes a quoted path rather
    than a command line, so paths with spaces or non-ASCII characters are
    handed to the association verbatim.
    """
    startfile = getattr(os, "startfile", None)
    if startfile is None:  # pragma: no cover - only reachable off Windows
        raise OSError("os.startfile is only available on Windows.")
    startfile(str(path))


__all__ = [
    "add_to_windows_media_player",
    "windows_media_player_available",
]
