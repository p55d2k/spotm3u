"""Job, batch, and artwork helpers shared by the SpotM3U web routes.

These helpers are used by the Flask views in :mod:`spotm3u.app`; keeping them
here leaves the application factory focused on routing and request handling.
"""

from __future__ import annotations

import json
import re
from functools import partial
from pathlib import Path

from flask import Flask, request, session

from .artwork import cached_artwork_path
from .audio.resolver import LocalAudioResolver
from .fast import FastSourceSearcher, FastTrackResolver
from .ffmpeg import locate_ffmpeg_location
from .jobs import ProcessingJob
from .media_player import MediaPlayerError, add_to_media_player, library_player_name
from .metadata import embedded_lyrics_form
from .models import Playlist
from .online import OnlineSourceSearcher, download_track
from .online.cache import DownloadCache
from .resolution import TrackResolver
from .uploads import cleanup_jobs

JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _job_directory(upload_root: Path | str, job_id: str) -> Path | None:
    """Resolve a generated job identifier without accepting filesystem paths."""
    if not JOB_ID_PATTERN.fullmatch(job_id):
        return None
    root = Path(upload_root).resolve()
    directory = (root / f"job-{job_id}").resolve()
    if root not in directory.parents or not directory.is_dir():
        return None
    if not (
        (directory / "extracted").is_dir()
        and (directory / "state.json").is_file()
        and (directory / "output").is_dir()
    ):
        return None
    return directory


def _current_job_directory(app: Flask, job_id: str) -> Path | None:
    """Resolve a job only when it belongs to the current upload session."""
    if session.get("job_id") != job_id:
        return None
    return _job_directory(app.config["UPLOAD_ROOT"], job_id)


def _save_job_state(job_directory: Path, state: dict[str, str]) -> None:
    """Persist small workflow state inside the server-owned job directory."""
    state_path = job_directory / "state.json"
    temporary_path = job_directory / "state.json.tmp"
    temporary_path.write_text(json.dumps(state), encoding="utf-8")
    temporary_path.replace(state_path)


def _selection_matches(job_directory: Path, playlist_id: str) -> bool:
    if not playlist_id.isdigit():
        return False
    try:
        state = json.loads((job_directory / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if state.get("selected_playlist_id") == playlist_id:
        return True
    return playlist_id in state.get("selected_playlist_ids", [])


def _selected_playlist_ids(job_directory: Path | None) -> list[str]:
    if job_directory is None:
        return []
    try:
        state = json.loads((job_directory / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return state.get("selected_playlist_ids", [])


def _batch_status(jobs: list[ProcessingJob]) -> dict[str, object]:
    states = []
    for job in jobs:
        state = job.as_dict()
        _annotate_artwork(job, state)
        states.append(state)
    total = sum(int(state["playlist"]["total_tracks"]) for state in states)
    completed = sum(int(state["completed"]) for state in states)
    searched = sum(int(state["searched"]) for state in states)
    return {
        "status": (
            "completed"
            if states and all(state["status"] == "completed" for state in states)
            else "failed"
            if any(state["status"] == "failed" for state in states)
            else "running"
        ),
        "completed": completed,
        "searched": searched,
        "total": total,
        "successful": sum(int(state["successful"]) for state in states),
        "failed": sum(int(state["failed"]) for state in states),
        # Completed tracks whose audio was deleted by hand; the interface offers
        # a retry for them instead of treating them as still available.
        "stale_outputs": sum(int(state["stale_outputs"]) for state in states),
        "started_at": min(
            (int(state["started_at"]) for state in states if isinstance(state["started_at"], int)),
            default=None,
        ),
        "playlists": states,
    }


def _import_batch(app: Flask, jobs: list[ProcessingJob]) -> dict[str, object]:
    """Import every playlist of a batch into the media library, one at a time.

    Each playlist is handed over exactly like the single-playlist action, so the
    batch needs no second import implementation. A playlist that has nothing to
    import, is cancelled, or fails never aborts the rest: the user asked for all
    of them, so the playlists that do work still arrive and the outcome is
    reported per playlist.
    """
    playlists: list[dict[str, object]] = []
    imported = skipped = failed = cancelled = errors = 0
    for job in jobs:
        state = job.as_dict()
        paths = [
            track["local_path"]
            for track in state["tracks"]
            if track["status"] == "complete" and track["local_path"]
        ]
        entry: dict[str, object] = {
            "playlist_id": job.playlist_id,
            "name": job.playlist_name,
            "imported": 0,
            "skipped": 0,
            "failed": 0,
            "cancelled": False,
            "message": "",
            "error": "",
        }
        if not paths:
            entry["error"] = "There were no resolved tracks to add."
            errors += 1
            playlists.append(entry)
            continue
        try:
            result = add_to_media_player(job.playlist_name, job.m3u_path, paths)
        except MediaPlayerError as error:
            app.logger.warning(
                "Batch Add to Media Player failed playlist=%s: %s", job.playlist_name, error
            )
            entry["error"] = str(error)
            errors += 1
            playlists.append(entry)
            continue
        entry["message"] = result.message
        entry["imported"] = result.imported
        entry["skipped"] = result.skipped
        entry["failed"] = result.failed
        entry["cancelled"] = result.cancelled
        imported += result.imported
        skipped += result.skipped
        failed += result.failed
        cancelled += int(result.cancelled)
        playlists.append(entry)
    return {
        "playlists": playlists,
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "cancelled": cancelled,
        "errors": errors,
        "partial": bool(failed or cancelled or errors),
        "message": _batch_import_message(
            total=len(playlists),
            imported=imported,
            skipped=skipped,
            failed=failed,
            cancelled=cancelled,
            errors=errors,
        ),
    }


def _batch_import_message(
    *, total: int, imported: int, skipped: int, failed: int, cancelled: int, errors: int
) -> str:
    """Compose the user-facing summary for a whole-batch media-player import."""
    name = library_player_name()
    added = total - errors - cancelled
    if added <= 0:
        return f"None of the {total} playlist(s) could be added to {name}."
    parts = [f"Added {added} of {total} playlist(s) to {name}."]
    parts.append(f"{imported} track(s) were imported.")
    if skipped:
        parts.append(f"{skipped} already-present track(s) were skipped.")
    if failed:
        parts.append(f"{failed} track(s) could not be imported.")
    if cancelled:
        parts.append(f"{cancelled} playlist(s) were cancelled and left unchanged.")
    if errors:
        parts.append(f"{errors} playlist(s) could not be added at all.")
    return " ".join(parts)


def _annotate_artwork(job: ProcessingJob, state: dict[str, object]) -> None:
    """Mark each track snapshot with whether cached artwork can be served.

    A track whose audio file was deleted by hand shows no artwork even though
    the cached image for its release is still on disk: the row would otherwise
    claim a cover for a download that is no longer there.
    """
    tracks = state.get("tracks")
    if not isinstance(tracks, list):
        return
    for item in tracks:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(job.tracks):
            item["artwork"] = False
            continue
        if item.get("file_missing"):
            item["artwork"] = False
            continue
        item["artwork"] = cached_artwork_path(job.output_dir, job.tracks[index]) is not None


def _annotate_lyrics(state: dict[str, object]) -> None:
    """Mark each track snapshot with how its embedded lyrics are stored.

    ``"synced"`` when the lyrics frame carries timestamps, ``"plain"`` when it
    does not, and ``None`` when the track has no embedded lyrics. Each track's
    own ``local_path`` is read, so a track tagged by an earlier run or by hand is
    reported as it really is, and a download that was deleted reports nothing.
    Unlike artwork, no job lookup is needed for it.

    Only the result pages call this: it is not part of the live progress view,
    and the status endpoint is polled, so reading every file's tags on each poll
    would be wasted work.
    """
    tracks = state.get("tracks")
    if not isinstance(tracks, list):
        return
    for item in tracks:
        if not isinstance(item, dict):
            continue
        local_path = item.get("local_path")
        item["lyrics"] = (
            embedded_lyrics_form(Path(local_path))
            if isinstance(local_path, str) and not item.get("file_missing")
            else None
        )


def _valid_playlist_index(playlist_id: str, playlist_count: int) -> int | None:
    if not playlist_id.isdigit():
        return None
    index = int(playlist_id)
    if index < 0 or index >= playlist_count:
        return None
    return index


def _sweep_old_jobs(app: Flask, *, log: bool = False) -> None:
    """Remove abandoned upload job directories that are past their age limit."""
    try:
        removed = cleanup_jobs(
            app.config["UPLOAD_ROOT"],
            max_age_seconds=app.config["MAX_JOB_AGE"],
            active_job_ids=app.config["JOB_MANAGER"].active_job_ids(),
        )
        if log and removed:
            app.logger.info("Removed %d abandoned upload job(s)", removed)
    except (OSError, ValueError):
        app.logger.exception("Unable to sweep abandoned upload jobs")


def _requested_fast_mode(app: Flask) -> bool:
    """Return the fast-mode choice for a start request.

    The processing pages always submit the field (the checkbox plus a hidden
    ``0``), so a posted value wins and a caller that sends nothing falls back to
    the ``[fast] enabled`` configuration default. Every submitted value is
    considered so the hidden fallback can never mask a checked box.
    """
    values = request.form.getlist("fast_mode") if request.method == "POST" else []
    if not values:
        return bool(app.config.get("FAST_MODE", False))
    return any(str(value).strip().casefold() in {"1", "true", "on", "yes"} for value in values)


def _download_dir(app: Flask) -> Path:
    """Resolve the persistent directory for downloaded MP3s and the M3U.

    Defaults to a stable subfolder inside the music library so downloads
    survive and can be matched by the local resolver on later runs.
    """
    configured = app.config.get("DOWNLOAD_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(app.config["MUSIC_LIBRARY"]).expanduser() / "SpotM3U-downloads"


def _build_processing_job(
    *,
    app: Flask,
    job_id: str,
    playlist_id: str,
    playlist: Playlist,
    output_dir: Path,
    music_library: str | Path,
    m3u_filename: str = "playlist.m3u",
    fast_mode: bool = False,
) -> ProcessingJob:
    max_results = int(app.config.get("SEARCH_MAX_RESULTS", 8))
    max_search_workers = int(app.config.get("SEARCH_MAX_WORKERS", 4))
    search_socket_timeout = int(app.config.get("SEARCH_SOCKET_TIMEOUT", 30))
    quality = str(app.config.get("DOWNLOAD_QUALITY", "192"))
    max_download_workers = int(app.config.get("DOWNLOAD_MAX_WORKERS", 2))
    download_timeout = float(app.config.get("DOWNLOAD_TIMEOUT", 600))
    retries = int(app.config.get("DOWNLOAD_RETRIES", 5))
    fragment_retries = int(app.config.get("DOWNLOAD_FRAGMENT_RETRIES", 5))
    socket_timeout = int(app.config.get("DOWNLOAD_SOCKET_TIMEOUT", 30))
    cookies_from_browser = app.config.get("YTDLP_COOKIES_FROM_BROWSER")
    pot_provider_url = app.config.get("YTDLP_POT_PROVIDER_URL")
    pot_provider_home = app.config.get("YTDLP_POT_PROVIDER_HOME")

    def resolver_factory() -> TrackResolver:
        local_resolver = LocalAudioResolver(music_library)
        searcher = OnlineSourceSearcher(
            max_results=max_results,
            max_search_workers=max_search_workers,
            socket_timeout=search_socket_timeout,
        )
        downloader = partial(
            download_track,
            quality=quality,
            retries=retries,
            fragment_retries=fragment_retries,
            socket_timeout=socket_timeout,
            timeout=download_timeout,
            cookies_from_browser=cookies_from_browser,
            pot_provider_url=pot_provider_url,
            pot_provider_home=pot_provider_home,
            ffmpeg_location=locate_ffmpeg_location(),
            # Fast mode downloads audio only: the shared downloader skips its
            # post-download validation and metadata enrichment.
            verify=not fast_mode,
        )
        if fast_mode:
            return FastTrackResolver(
                local_resolver,
                output_dir,
                searcher=FastSourceSearcher(searcher),
                downloader=downloader,
            )
        return TrackResolver(
            local_resolver,
            output_dir,
            searcher=searcher,
            downloader=downloader,
            cache=DownloadCache(output_dir),
        )

    return ProcessingJob(
        job_id=job_id,
        playlist_id=playlist_id,
        playlist_name=playlist.name,
        tracks=playlist.tracks,
        output_dir=output_dir,
        resolver_factory=resolver_factory,
        max_workers=int(app.config.get("RESOLVE_WORKERS", 4)),
        max_download_workers=max_download_workers,
        m3u_extended=bool(app.config.get("M3U_EXTENDED", True)),
        m3u_relative=bool(app.config.get("M3U_RELATIVE", False)),
        m3u_filename=m3u_filename,
        fast_mode=fast_mode,
    )
