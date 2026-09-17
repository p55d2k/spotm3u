"""Download validated online sources into a controlled MP3 directory."""

from __future__ import annotations

from hashlib import sha256
import re
from pathlib import Path
from urllib.parse import urlparse

from ..models import Track


class DownloadError(RuntimeError):
    """Raised when a source cannot be downloaded into a complete MP3 file."""


def download_track(
    track: Track,
    source_url: str,
    output_dir: str | Path,
    *,
    quality: str = "192",
) -> Path:
    """Download ``source_url`` and return its verified local MP3 path.

    ``output_dir`` is the caller-owned job/library directory. All yt-dlp
    intermediate files and the final file are constrained to that directory.
    """
    _validate_source_url(source_url)
    destination = Path(output_dir).expanduser().resolve()
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError(f"cannot create download directory: {destination}") from exc

    output_path = destination / _output_name(track, source_url)
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
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
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
    return output_path


def download_source(
    source_url: str,
    track: Track,
    output_dir: str | Path,
    *,
    quality: str = "192",
) -> Path:
    """Compatibility-oriented source-first wrapper around :func:`download_track`."""
    return download_track(track, source_url, output_dir, quality=quality)


def _validate_source_url(source_url: str) -> None:
    if not isinstance(source_url, str) or not source_url.strip():
        raise DownloadError("source URL is required")
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DownloadError("source URL must be an HTTP(S) URL")


def _output_name(track: Track, source_url: str) -> str:
    identity = track.spotify_id or source_url
    digest = sha256(identity.encode("utf-8")).hexdigest()[:12]
    title = _safe_component(track.title)
    artists = _safe_component("-".join(track.artists))
    stem = "-".join(part for part in (title, artists, digest) if part) or digest
    return f"{stem}.mp3"


def _safe_component(value: str) -> str:
    value = re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip("-. ")
    return value[:80].strip("-. ")


def _is_complete_mp3(output_path: Path, destination: Path) -> bool:
    try:
        resolved = output_path.resolve()
        resolved.relative_to(destination)
        return resolved.is_file() and resolved.stat().st_size > 0
    except (OSError, ValueError):
        return False


__all__ = ["DownloadError", "download_source", "download_track"]
