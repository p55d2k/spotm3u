"""macOS Apple Music integration for resolved local playlist tracks.

On macOS "Add to Media Player" means adding the resolved files to an Apple
Music user playlist through ``osascript``. The generated M3U is not imported
directly because Music has no supported playlist-file import API; instead every
resolved file already referenced by the playlist is added in playlist order, and
Music reads the title, artist, and album from each file's own metadata.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .result import IMPORT_ACTIONS, MediaPlayerError, MediaPlayerResult

MACOS_PLATFORM = "darwin"


def apple_music_available() -> bool:
    """Return whether the current host supports the Apple Music integration."""
    return sys.platform == MACOS_PLATFORM


def add_to_apple_music(
    playlist_name: str,
    paths: list[str | Path] | tuple[str | Path, ...],
    *,
    runner=subprocess.run,
) -> MediaPlayerResult:
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
        raise MediaPlayerError("Add to Media Player is only available on macOS here.")
    if not playlist_name.strip():
        raise MediaPlayerError("Apple Music playlist name cannot be empty.")

    files = [Path(path).expanduser().resolve() for path in paths]
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise MediaPlayerError(f"{len(missing)} resolved audio file(s) are no longer available.")
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
        raise MediaPlayerError(f"Apple Music import failed: {detail}") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaPlayerError(f"Apple Music import failed: {exc}") from exc

    parts = completed.stdout.strip().split("|")
    if len(parts) == 2:
        imported_text, failed_text = parts
        skipped_text, action = "0", "created"
    elif len(parts) == 4:
        imported_text, failed_text, skipped_text, action = parts
    else:
        raise MediaPlayerError("Apple Music returned an invalid import result.")
    try:
        imported = int(imported_text)
        failed = int(failed_text)
        skipped = int(skipped_text)
    except ValueError:
        raise MediaPlayerError("Apple Music returned an invalid import result.") from None
    if action not in IMPORT_ACTIONS:
        raise MediaPlayerError("Apple Music returned an invalid import result.")
    return MediaPlayerResult(
        action=action,
        imported=imported,
        failed=failed,
        skipped=skipped,
        message=_apple_music_message(playlist_name, action, imported, failed, skipped),
    )


def _apple_music_message(
    playlist_name: str, action: str, imported: int, failed: int, skipped: int
) -> str:
    """Compose the user-facing result text for an Apple Music import."""
    if action == "cancelled":
        return (
            f'Add to Media Player was cancelled. A playlist named "{playlist_name}" '
            "already exists in Apple Music."
        )
    parts = [f"Added to Media Player. {imported} track(s) were imported into Apple Music."]
    if skipped:
        parts.append(f"{skipped} already-present track(s) were skipped.")
    if failed:
        parts.append(f"{failed} track(s) could not be imported.")
    parts.append(
        f'To see the playlist, open Apple Music and look for "{playlist_name}" '
        "in your Library sidebar."
    )
    return " ".join(parts)


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
    "add_to_apple_music",
    "apple_music_available",
]
