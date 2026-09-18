"""Download validated online sources into a controlled MP3 directory."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from ..models import Track
from .audio_validation import validate_downloaded_audio

# Per-output-path lock so concurrent downloads targeting the same local file
# (same track duplicate or explicit overwrite) serialize rather than corrupt.
_OUTPUT_LOCKS: dict[str, threading.Lock] = {}
_OUTPUT_LOCKS_GUARD = threading.Lock()


class DownloadError(RuntimeError):
    """Raised when a source cannot be downloaded into a complete MP3 file."""


def download_track(
    track: Track,
    source_url: str,
    output_dir: str | Path,
    *,
    quality: str = "192",
    retries: int = 5,
    fragment_retries: int = 5,
    socket_timeout: int = 30,
    timeout: float | None = 600,
) -> Path:
    """Download ``source_url`` and return its verified local MP3 path.

    ``output_dir`` is the caller-owned job/library directory. All yt-dlp
    intermediate files and the final file are constrained to that directory.

    ``timeout`` bounds the entire download wall-clock time so a hung yt-dlp or
    ffmpeg run cannot occupy a worker forever. It is raised as a
    :class:`DownloadError`; ``socket_timeout`` remains the network-level
    fallback that bounds each socket operation.
    """
    _validate_source_url(source_url)
    destination = Path(output_dir).expanduser().resolve()
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError(f"cannot create download directory: {destination}") from exc

    output_path = destination / _output_name(track, source_url)
    if timeout is not None and timeout > 0:
        try:
            return _run_with_timeout(
                timeout,
                _download_guarded,
                track,
                source_url,
                destination,
                output_path,
                quality=quality,
                retries=retries,
                fragment_retries=fragment_retries,
                socket_timeout=socket_timeout,
            )
        except TimeoutError as exc:
            _prune_partial(output_path)
            raise DownloadError(
                f"download timed out after {timeout:.0f}s: {output_path.name}"
            ) from exc
    return _download_guarded(
        track,
        source_url,
        destination,
        output_path,
        quality=quality,
        retries=retries,
        fragment_retries=fragment_retries,
        socket_timeout=socket_timeout,
    )


def _download_guarded(
    track: Track,
    source_url: str,
    destination: Path,
    output_path: Path,
    *,
    quality: str,
    retries: int,
    fragment_retries: int,
    socket_timeout: int,
) -> Path:
    """Run :func:`_download_to` under the per-output-path lock."""
    with _output_lock(output_path):
        return _download_to(
            track,
            source_url,
            destination,
            output_path,
            quality=quality,
            retries=retries,
            fragment_retries=fragment_retries,
            socket_timeout=socket_timeout,
        )


def _run_with_timeout(
    timeout: float,
    fn: Callable[..., Path],
    /,
    *args: object,
    **kwargs: object,
) -> Path:
    """Run ``fn`` in a daemon thread and bound its wall-clock execution.

    The worker is a daemon so a genuinely hung yt-dlp/ffmpeg call cannot keep
    the job thread pool alive; the timeout surfaces as a raise here while any
    stuck underlying call finishes or dies on its own in the background.
    """
    outcome: list[Path] = []
    failure: list[BaseException] = []

    def target() -> None:
        try:
            outcome.append(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - re-raised for the caller
            failure.append(exc)

    worker = threading.Thread(
        target=target,
        name="spotm3u-download",
        daemon=True,
    )
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"operation exceeded {timeout}s")
    if failure:
        raise failure[0]
    return outcome[0]


def _prune_partial(output_path: Path) -> None:
    """Best-effort removal of a partially written download after a timeout."""
    try:
        output_path.unlink(missing_ok=True)
    except OSError:
        pass


def _output_lock(path: Path) -> threading.Lock:
    """Return a lock shared by all callers writing to the same output file."""
    key = str(path.resolve())
    with _OUTPUT_LOCKS_GUARD:
        lock = _OUTPUT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _OUTPUT_LOCKS[key] = lock
        return lock


def _download_to(
    track: Track,
    source_url: str,
    destination: Path,
    output_path: Path,
    *,
    quality: str,
    retries: int,
    fragment_retries: int,
    socket_timeout: int,
) -> Path:
    output_template = str(output_path.with_suffix(".%(ext)s"))
    try:
        output_path.unlink(missing_ok=True)
    except OSError as exc:
        raise DownloadError(f"cannot replace existing download: {output_path.name}") from exc

    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency is project-managed
        raise DownloadError("yt-dlp is not installed") from exc

    options = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": quality,
            },
        ],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": retries,
        "fragment_retries": fragment_retries,
        "socket_timeout": socket_timeout,
        "ignoreerrors": False,
        "continuedl": False,
        "overwrites": True,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            result = ydl.download([source_url])
    except Exception as exc:
        raise DownloadError(f"download failed for {source_url}") from exc

    if result not in (None, 0):
        raise DownloadError(f"download failed for {source_url} (exit code {result})")
    if not _is_complete_mp3(output_path, destination):
        raise DownloadError(f"download did not produce a complete MP3: {output_path.name}")
    validation = validate_downloaded_audio(track, output_path)
    if validation.status == "invalid":
        raise DownloadError(
            f"downloaded audio is invalid: {', '.join(validation.reasons)}"
        )
    embed_metadata(output_path, track)
    return output_path


def download_source(
    source_url: str,
    track: Track,
    output_dir: str | Path,
    *,
    quality: str = "192",
    timeout: float | None = 600,
) -> Path:
    """Compatibility-oriented source-first wrapper around :func:`download_track`."""
    return download_track(track, source_url, output_dir, quality=quality, timeout=timeout)


def _validate_source_url(source_url: str) -> None:
    if not isinstance(source_url, str) or not source_url.strip():
        raise DownloadError("source URL is required")
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DownloadError("source URL must be an HTTP(S) URL")


def _output_name(track: Track, source_url: str) -> str:
    title = _safe_component(track.title)
    artists = _safe_component(", ".join(track.artists))
    stem = " - ".join(part for part in (title, artists) if part) or _safe_component(source_url)
    return f"{stem}.mp3"


def _safe_component(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip(" -.")
    return value[:160].strip(" -.") or "_"


def embed_metadata(path: str | Path, track: Track) -> None:
    """Write Spotify-derived ID3 metadata (title, artist, album) into an MP3."""
    try:
        from mutagen.easyid3 import EasyID3  # type: ignore
        from mutagen.id3 import ID3NoHeaderError  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency is project-managed
        raise DownloadError("mutagen is not installed") from exc

    audio_path = Path(path)
    try:
        tags = EasyID3(str(audio_path))
    except ID3NoHeaderError:
        tags = EasyID3()
    if track.title:
        tags["title"] = track.title
    tags["artist"] = track.artists
    if track.album:
        tags["album"] = track.album
    try:
        tags.save(str(audio_path))
    except (OSError, ValueError, TypeError) as exc:
        raise DownloadError(f"cannot write metadata to {audio_path.name}") from exc


def _is_complete_mp3(output_path: Path, destination: Path) -> bool:
    try:
        resolved = output_path.resolve()
        resolved.relative_to(destination)
        return resolved.is_file() and resolved.stat().st_size > 0
    except (OSError, ValueError):
        return False


__all__ = ["DownloadError", "download_source", "download_track", "embed_metadata"]
