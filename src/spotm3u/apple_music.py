"""macOS Apple Music integration for resolved local playlist tracks."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class AppleMusicError(RuntimeError):
    """Apple Music could not be controlled."""


@dataclass(frozen=True)
class AppleMusicResult:
    """Outcome of importing resolved files into an Apple Music playlist."""

    imported: int
    failed: int

    @property
    def complete(self) -> bool:
        return self.failed == 0


def apple_music_available() -> bool:
    """Return whether the current host supports the Apple Music integration."""
    return sys.platform == "darwin"


def add_to_apple_music(
    playlist_name: str,
    paths: list[str | Path] | tuple[str | Path, ...],
    *,
    runner=subprocess.run,
) -> AppleMusicResult:
    """Add resolved local files to a Music user playlist on macOS.

    Each path is added separately so intentional duplicate playlist entries
    remain duplicates where Music permits them. The AppleScript catches an
    individual import failure and returns aggregate counts.
    """
    if not apple_music_available():
        raise AppleMusicError("Apple Music integration is only available on macOS.")
    if not playlist_name.strip():
        raise AppleMusicError("Apple Music playlist name cannot be empty.")

    files = [Path(path).expanduser().resolve() for path in paths]
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise AppleMusicError(f"{len(missing)} resolved audio file(s) are no longer available.")
    script = _apple_script(playlist_name, files)
    try:
        completed = runner(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppleMusicError(f"Apple Music import failed: {exc}") from exc

    try:
        imported_text, failed_text = completed.stdout.strip().split("|", 1)
        return AppleMusicResult(int(imported_text), int(failed_text))
    except (AttributeError, ValueError):
        raise AppleMusicError("Apple Music returned an invalid import result.") from None


def _apple_script(playlist_name: str, paths: list[Path]) -> str:
    playlist = _as_script_string(playlist_name)
    additions = "\n".join(
        f"        try\n"
        f'            add POSIX file "{_as_script_string(path)}" to targetPlaylist\n'
        f"            set importedCount to importedCount + 1\n"
        f"        on error\n"
        f"            set failedCount to failedCount + 1\n"
        f"        end try"
        for path in paths
    )
    return f'''tell application "Music"
    if not (exists user playlist "{playlist}") then
        make new user playlist with properties {{name:"{playlist}"}}
    end if
    set targetPlaylist to user playlist "{playlist}"
    set importedCount to 0
    set failedCount to 0
{additions}
    return (importedCount as text) & "|" & (failedCount as text)
end tell'''


def _as_script_string(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


__all__ = [
    "AppleMusicError",
    "AppleMusicResult",
    "add_to_apple_music",
    "apple_music_available",
]
