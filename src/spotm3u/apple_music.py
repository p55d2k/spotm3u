"""macOS Apple Music integration for resolved local playlist tracks."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

VALID_MODES = frozenset({"created", "appended", "skipped-duplicates", "cancelled"})


class AppleMusicError(RuntimeError):
    """Apple Music could not be controlled."""


@dataclass(frozen=True)
class AppleMusicResult:
    """Outcome of importing resolved files into an Apple Music playlist.

    ``mode`` describes how the import proceeded:

    - ``created``: the playlist did not exist and was created fresh.
    - ``appended``: the playlist already existed, the user chose "Add all",
      so every file was appended (duplicates allowed).
    - ``skipped-duplicates``: the playlist already existed, the user chose
      "Skip duplicates", so files already present were left out.
    - ``cancelled``: the playlist already existed and the user cancelled,
      so nothing was changed.
    """

    imported: int
    failed: int
    skipped: int = 0
    mode: str = "created"
    cancelled: bool = False

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

    If a user playlist with the same name already exists, the script asks the
    user whether to add all files again, to skip files that are already in the
    playlist, or to cancel. On success the playlist is revealed in Music as a
    best effort; the app itself may not come to the foreground, so the web UI
    tells the user where to find the playlist.
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
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise AppleMusicError(f"Apple Music import failed: {detail}") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppleMusicError(f"Apple Music import failed: {exc}") from exc

    parts = completed.stdout.strip().split("|")
    if len(parts) == 2:
        imported_text, failed_text = parts
        skipped_text, mode = "0", "created"
    elif len(parts) == 4:
        imported_text, failed_text, skipped_text, mode = parts
    else:
        raise AppleMusicError("Apple Music returned an invalid import result.")
    try:
        imported = int(imported_text)
        failed = int(failed_text)
        skipped = int(skipped_text)
    except ValueError:
        raise AppleMusicError("Apple Music returned an invalid import result.") from None
    if mode not in VALID_MODES:
        raise AppleMusicError("Apple Music returned an invalid import result.")
    return AppleMusicResult(
        imported=imported,
        failed=failed,
        skipped=skipped,
        mode=mode,
        cancelled=mode == "cancelled",
    )


def _apple_script(playlist_name: str, paths: list[Path]) -> str:
    playlist = _as_script_string(playlist_name)
    count = len(paths)
    additions = "\n".join(
        f"        try\n"
        f"            if skipDuplicates then\n"
        f'                set theURL to POSIX file "{_as_script_string(path)}" as text\n'
        f"                set dupes to (every track of targetPlaylist whose location is theURL)\n"
        f"                if (count of dupes) > 0 then\n"
        f"                    set skippedCount to skippedCount + 1\n"
        f"                else\n"
        f'                    add POSIX file "{_as_script_string(path)}" to targetPlaylist\n'
        f"                    set importedCount to importedCount + 1\n"
        f"                end if\n"
        f"            else\n"
        f'                add POSIX file "{_as_script_string(path)}" to targetPlaylist\n'
        f"                set importedCount to importedCount + 1\n"
        f"            end if\n"
        f"        on error\n"
        f"            set failedCount to failedCount + 1\n"
        f"        end try"
        for path in paths
    )
    dialog_message = (
        f' "Playlist \\"{playlist}\\" already exists in Apple Music."'
        f' & linefeed & "Some or all of its tracks may already be imported."'
        f' & linefeed & linefeed & "How do you want to import these {count} file(s)?"'
    )
    return f'''tell application "Music"
    if not (exists user playlist "{playlist}") then
        make new user playlist with properties {{name:"{playlist}"}}
        set targetPlaylist to user playlist "{playlist}"
        set mode to "created"
    else
        set targetPlaylist to user playlist "{playlist}"
        set mode to "appended"
        set dialogMessage to {dialog_message}
        try
            set userChoice to button returned of (display dialog dialogMessage buttons \
                {{"Skip duplicates", "Add all", "Cancel"}} default button "Skip duplicates")
        on error errorMessage number errorNumber
            if errorNumber is -128 then
                return "0|0|0|cancelled"
            end if
            error "Could not ask how to import into the existing playlist: " & errorMessage
        end try
        if userChoice is "Skip duplicates" then
            set mode to "skipped-duplicates"
        end if
    end if
    set importedCount to 0
    set failedCount to 0
    set skippedCount to 0
    set skipDuplicates to (mode is "skipped-duplicates")
{additions}
    reveal targetPlaylist
    return (importedCount as text) & "|" & (failedCount as text) & "|" \
        & (skippedCount as text) & "|" & mode
end tell'''


def _as_script_string(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


__all__ = [
    "AppleMusicError",
    "AppleMusicResult",
    "add_to_apple_music",
    "apple_music_available",
]
