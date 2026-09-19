"""Locating the FFmpeg used for audio extraction.

The app never invokes FFmpeg directly; yt-dlp's ``FFmpegExtractAudio``
postprocessor runs it. Centralizing the lookup here keeps development and the
packaged distribution consistent: a PyInstaller bundle prefers its own FFmpeg
binaries, everything else relies on yt-dlp's own discovery (``PATH`` plus
known install locations), and a missing FFmpeg raises one actionable error.

Bundled binaries live in an ``ffmpeg`` subdirectory of the bundle (next to
the executable and, in a one-folder build, under ``_internal``), with
``ffprobe`` expected beside ``ffmpeg``. The whole directory is handed to
yt-dlp as ``ffmpeg_location`` so neither binary needs to be on ``PATH``.
"""

from __future__ import annotations

from pathlib import Path

from .runtime import bundle_root, bundle_roots, is_frozen

BUNDLED_SUBDIR = "ffmpeg"

_FFMPEG_NAMES = ("ffmpeg", "ffmpeg.exe")
_FFPROBE_NAMES = ("ffprobe", "ffprobe.exe")


class FFmpegMissingError(RuntimeError):
    """Raised when no usable FFmpeg could be located."""


def bundled_directory() -> Path:
    """The primary directory where a bundle is expected to carry FFmpeg."""
    return bundle_root() / BUNDLED_SUBDIR


def _candidate_directories() -> tuple[Path, ...]:
    """All bundle locations that may hold FFmpeg, in preference order."""
    return tuple(dict.fromkeys(root / BUNDLED_SUBDIR for root in bundle_roots()))


def has_bundled_ffmpeg() -> bool:
    """True when a packaged bundle ships an FFmpeg binary with the app."""
    return any(has_executable(directory) for directory in _candidate_directories())


def has_executable(directory: Path) -> bool:
    """True when ``directory`` contains an ``ffmpeg``/``ffprobe`` binary."""
    if not directory.is_dir():
        return False
    return any((directory / name).is_file() for name in (*_FFMPEG_NAMES, *_FFPROBE_NAMES))


def locate_ffmpeg_location() -> str | None:
    """Return the ``ffmpeg_location`` value for yt-dlp, or ``None``.

    A packaged release returns its bundled ``ffmpeg`` directory so yt-dlp finds
    both ``ffmpeg`` and ``ffprobe`` without any ``PATH`` help. Development
    returns ``None``, letting yt-dlp resolve FFmpeg through its default lookup
    (``PATH`` then standard install locations), preserving current behavior.
    """
    if not is_frozen():
        return None
    for directory in _candidate_directories():
        if has_executable(directory):
            return str(directory)
    return None


def require_ffmpeg_location() -> str:
    """Return the ``ffmpeg_location`` for yt-dlp or raise an actionable error.

    The error includes every supported way to make FFmpeg available: install it
    and put it on ``PATH`` (development) or drop the prepared ``ffmpeg``
    directory next to the application (packaged release).
    """
    location = locate_ffmpeg_location()
    if location:
        return location
    raise FFmpegMissingError(
        "FFmpeg is required for audio extraction and conversion. Install "
        "FFmpeg and place ffmpeg on PATH, or place a bundled 'ffmpeg' "
        f"directory next to the application (expected: {bundled_directory()})."
    )
