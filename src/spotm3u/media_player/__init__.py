"""Platform-aware "Add to Media Player" integration for generated playlists.

The web UI calls :func:`add_to_media_player` once and never contains
operating-system-specific logic. The platform modules decide how the playlist
reaches a media player:

- macOS: the resolved tracks are added to an Apple Music user playlist
  (:mod:`spotm3u.media_player.macos`).
- Windows: the generated ``.m3u`` is opened with its default file association,
  so any installed player that supports M3U can import it
  (:mod:`spotm3u.media_player.windows`).

Other platforms have no equivalent integration. The feature degrades
gracefully there: the action is not offered and the manual M3U download keeps
working on every platform.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

from .macos import add_to_apple_music, apple_music_available
from .result import MediaPlayerError, MediaPlayerResult
from .windows import add_to_windows_media_player, windows_media_player_available


def media_player_available() -> bool:
    """Return whether the current host supports "Add to Media Player"."""
    return apple_music_available() or windows_media_player_available()


def add_to_media_player(
    playlist_name: str,
    playlist_path: str | Path | None = None,
    paths: Iterable[str | Path] = (),
    *,
    runner=None,
    opener=None,
) -> MediaPlayerResult:
    """Hand the already-generated playlist to the platform's media player.

    ``playlist_path`` is the exact M3U the processing job wrote; it is never
    regenerated for this action. ``paths`` are the resolved local files that M3U
    references, which macOS imports into the Music library. ``runner`` and
    ``opener`` exist so tests can observe the platform call without starting a
    real media player.
    """
    if apple_music_available():
        return add_to_apple_music(
            playlist_name,
            paths,
            runner=subprocess.run if runner is None else runner,
        )
    if windows_media_player_available():
        return add_to_windows_media_player(playlist_path, opener=opener)
    raise MediaPlayerError("Add to Media Player is only available on macOS and Windows.")


__all__ = [
    "MediaPlayerError",
    "MediaPlayerResult",
    "add_to_apple_music",
    "add_to_media_player",
    "add_to_windows_media_player",
    "apple_music_available",
    "media_player_available",
    "windows_media_player_available",
]
