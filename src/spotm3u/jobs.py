"""Background job abstraction for processing a playlist without blocking a request."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .log import TrackLogger, attach_job_logging
from .m3u.writer import write_m3u
from .metadata import artwork_artist, prune_missing_artwork
from .models import Track
from .resolution import (
    PreparedTrack,
    TrackResolution,
    TrackResolver,
    TrackStage,
)

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "completed", "failed"]
TrackProcessingStatus = Literal[
    "queued",
    "resolving-local",
    "searching",
    "searched",
    "validating-source",
    "downloading",
    "validating-audio",
    "complete",
    "failed",
    "ambiguous",
    "skipped",
]
ResolverFactory = Callable[[], TrackResolver]


def _missing_file(path: str | None) -> bool:
    """Return True when a recorded output path no longer exists on disk."""
    if not path:
        return False
    try:
        return not Path(path).is_file()
    except (OSError, ValueError):
        return True


def _missing_output(result: TrackResolution | None) -> bool:
    """Return True when a previously resolved track's audio file is gone.

    Users delete the download folder by hand, and the cache manifest or the
    in-memory results must never claim a track is still there after that. A
    resolution that never produced a file (failed, missing, ambiguous) is not
    "stale": it is unresolved and already retried.
    """
    if result is None or not result.successful:
        return False
    local_path = result.local_path
    return local_path is None or _missing_file(str(local_path))


TRACK_STATUS_TERMINAL = {
    "local": "complete",
    "downloaded": "complete",
    "ambiguous": "ambiguous",
    "missing": "failed",
    "rejected": "failed",
    "failed": "failed",
    "uncertain": "failed",
}


class JobStartError(RuntimeError):
    """A job has already started and cannot be started again."""


class JobTimeoutError(RuntimeError):
    """The overall wall-clock time budget for a job was exceeded."""


@dataclass(frozen=True)
class TrackJobState:
    """The observable processing state of a single playlist track."""

    index: int
    title: str
    artists: tuple[str, ...]
    status: TrackProcessingStatus
    reason: str = ""
    local_path: str | None = None
    source_url: str | None = None
    resolution: str = ""
    # Monotonic-epoch time (seconds) the current status began; lets the UI
    # estimate how long the in-flight track has been stuck in this stage.
    stage_started_at: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "title": self.title,
            "artists": list(self.artists),
            "status": self.status,
            "reason": self.reason,
            "local_path": self.local_path,
            "source_url": self.source_url,
            "resolution": self.resolution,
            "stage_started_at": (
                int(self.stage_started_at * 1000) if self.stage_started_at is not None else None
            ),
        }


class ProcessingJob:
    """Process playlist tracks in a background thread while exposing state."""

    def __init__(
        self,
        *,
        job_id: str,
        playlist_id: str,
        playlist_name: str,
        tracks: list[Track],
        output_dir: str | Path,
        resolver_factory: ResolverFactory,
        max_workers: int = 1,
        max_download_workers: int | None = None,
        timeout: float | None = None,
        m3u_extended: bool = True,
        m3u_relative: bool = False,
        m3u_filename: str = "playlist.m3u",
        fast_mode: bool = False,
    ) -> None:
        self.job_id = job_id
        self.playlist_id = playlist_id
        self.playlist_name = playlist_name
        self.tracks = tuple(tracks)
        self.output_dir = Path(output_dir)
        self.resolver_factory = resolver_factory
        self.max_workers = max(1, int(max_workers))
        self.max_download_workers = max(1, int(max_download_workers or self.max_workers))
        self.timeout = timeout
        self.m3u_extended = m3u_extended
        self.m3u_relative = m3u_relative
        self.m3u_filename = m3u_filename
        # The resolver the factory builds for this job decides what fast mode
        # actually skips; the flag is kept here for the UI and diagnostics.
        self.fast_mode = fast_mode
        self.manager: JobManager | None = None

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status: JobStatus = "queued"
        self._current_index: int | None = None
        self._searched: int = 0
        self._error: str | None = None
        self._m3u_path: Path | None = None
        self._deadline: float | None = time.monotonic() + timeout if timeout is not None else None
        self._started_at: float | None = None
        self._completed_at: float | None = None
        self._track_states: list[TrackJobState] = [
            TrackJobState(index, track.title, tuple(track.artists), "queued")
            for index, track in enumerate(self.tracks)
        ]
        self._results: list[TrackResolution | None] = [None] * len(self.tracks)
        # Set while a retry is running: the tracks the progress belongs to.
        self._pending: tuple[int, ...] | None = None

    @property
    def status(self) -> JobStatus:
        with self._lock:
            return self._status

    @property
    def completed(self) -> int:
        with self._lock:
            track_states = self._track_states
            pending = self._pending
        return sum(
            track_states[index].status in {"complete", "failed", "ambiguous"}
            for index in self._progress_indices(pending, len(track_states))
        )

    @staticmethod
    def _progress_indices(pending: tuple[int, ...] | None, total: int) -> Sequence[int]:
        """Indices the progress view counts; just the retried tracks during a retry."""
        return range(total) if pending is None else pending

    @property
    def searched(self) -> int:
        with self._lock:
            return self._searched

    @property
    def successful(self) -> int:
        with self._lock:
            return sum(state.status == "complete" for state in self._track_states)

    @property
    def failed(self) -> int:
        with self._lock:
            return sum(state.status == "failed" for state in self._track_states)

    @property
    def ambiguous(self) -> int:
        with self._lock:
            return sum(state.status == "ambiguous" for state in self._track_states)

    @property
    def m3u_path(self) -> Path | None:
        with self._lock:
            return self._m3u_path

    def start(self) -> None:
        """Begin processing in a daemon thread."""
        with self._lock:
            if self._status != "queued":
                raise JobStartError("job has already started")
            self._status = "running"
            self._started_at = time.time()
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run,
                name=f"spotm3u-job-{self.job_id}",
                daemon=True,
            )
        self._thread.start()

    def wait(self, timeout: float | None = None) -> None:
        """Block until the background job finishes."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def retry(self) -> tuple[int, ...]:
        """Re-resolve every track that has no usable audio file on disk.

        Tracks that still have their resolved file keep their result and are
        never processed again, so a retry costs only the unresolved tracks. A
        track whose file was deleted by hand counts as unresolved too, so
        deleting the download folder and retrying re-downloads the playlist
        instead of quietly reporting it as complete. The M3U is rewritten from
        the merged results once the retry settles.
        """
        with self._lock:
            if self._status in {"queued", "running"}:
                raise JobStartError("job is still running")
            self._started_at = self._started_at or time.time()
            indices = tuple(
                index
                for index, result in enumerate(self._results)
                if result is None or not result.successful or _missing_output(result)
            )
            if not indices:
                return ()
            # A track that is about to be downloaded again must not keep the
            # artwork of the file the user deleted: the release entry is dropped
            # and re-fetched with the fresh download. Artists that still have a
            # track on disk (or another track in this retry) keep their shared
            # profile image.
            self._forget_artwork_for_missing_outputs()
            self._pending = indices
            self._searched = 0
            self._error = None
            self._status = "running"
            self._deadline = time.monotonic() + self.timeout if self.timeout is not None else None
            for index in indices:
                current = self._track_states[index]
                self._track_states[index] = TrackJobState(
                    current.index, current.title, current.artists, "queued"
                )
            self._thread = threading.Thread(
                target=self._process,
                args=(indices,),
                name=f"spotm3u-job-{self.job_id}-retry",
                daemon=True,
            )
        TrackLogger(logger, job_id=self.job_id).info(
            "retrying %d unresolved track(s)", len(indices)
        )
        self._thread.start()
        return indices

    def _forget_artwork_for_missing_outputs(self) -> None:
        """Prune cached artwork for deleted downloads, never for live ones."""
        stale = [index for index, result in enumerate(self._results) if _missing_output(result)]
        if not stale:
            return
        stale_set = set(stale)
        keep_artists = {
            artist
            for index, track in enumerate(self.tracks)
            if index not in stale_set and (artist := artwork_artist(track)) is not None
        }
        try:
            removed = prune_missing_artwork(
                self.output_dir,
                (self.tracks[index] for index in stale),
                keep_artists=keep_artists,
            )
        except OSError as error:
            logger.debug("Artwork cache prune failed job=%s: %s", self.job_id, error)
            return
        if removed:
            logger.info(
                "Removed %d cached artwork file(s) for %d deleted download(s) job=%s",
                removed,
                len(stale),
                self.job_id,
            )

    def _run(self) -> None:
        self._process(None)

    def _process(self, indices: tuple[int, ...] | None) -> None:
        """Resolve ``indices`` (or every track) and rewrite the M3U."""
        total = len(self.tracks) if indices is None else len(indices)
        track_log = TrackLogger(logger, job_id=self.job_id)
        track_log.info(
            "job started playlist=%s tracks=%d output=%s fast_mode=%s",
            self.playlist_name,
            total,
            self.output_dir,
            self.fast_mode,
        )
        try:
            resolver = self.resolver_factory()
            attach_job_logging(resolver, job_id=self.job_id)
            output_dir = self.output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            self._resolve_all(resolver, indices)

            m3u_path = output_dir / self.m3u_filename
            write_m3u(
                m3u_path,
                [result for result in self._results if result is not None],
                extended=self.m3u_extended,
                relative_to=self.output_dir if self.m3u_relative else None,
            )
            with self._lock:
                self._m3u_path = m3u_path
                self._current_index = None
                self._pending = None
                self._status = "completed"
                self._completed_at = time.time()
            track_log.info(
                "job completed m3u_path=%s successful=%d failed=%d",
                m3u_path,
                self.successful,
                self.failed,
            )
        except Exception as exc:  # pragma: no cover - defensive final state
            with self._lock:
                self._current_index = None
                self._pending = None
                self._error = str(exc)
                self._status = "failed"
                self._completed_at = time.time()
            track_log.exception("job failed: %s", exc)

    def _resolve_all(
        self,
        resolver: TrackResolver,
        indices: tuple[int, ...] | None = None,
    ) -> None:
        """Resolve the selected tracks, optionally in parallel, keeping playlist order.

        Parallel jobs run a two-phase pipeline: every track is first matched
        locally, searched, and ranked concurrently (fast), then the tracks that
        need a download are processed in a second concurrent pass. Searches
        therefore run ahead while downloads from phase two are still in
        progress, instead of each worker doing search-and-download in a chunk.

        Downloads use a separate, smaller concurrency cap
        (``max_download_workers``) so the per-track yt-dlp/ffmpeg work can
        never spawn an uncontrolled number of processes. ``timeout`` bounds
        the whole job so a runaway playlist fails instead of running forever.
        """
        self._check_deadline()
        tracks = self.tracks
        selected = tuple(range(len(tracks))) if indices is None else indices
        if self.max_workers <= 1 or len(selected) <= 1:
            for index in selected:
                with self._lock:
                    self._current_index = index
                self._check_deadline()
                result = resolver.resolve(tracks[index], stage_callback=self._stage_reporter(index))
                self._finalize_track(index, result)
                self._mark_searched(index)
            return

        def phase_one(index):
            return resolver.prepare(tracks[index], stage_callback=self._stage_reporter(index))

        prepared: list[TrackResolution | PreparedTrack | None] = [None] * len(tracks)
        with ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix=f"spotm3u-search-{self.job_id}",
        ) as pool:
            future_to_index = {pool.submit(phase_one, index): index for index in selected}
            for future in as_completed(future_to_index):
                self._check_deadline()
                index = future_to_index[future]
                outcome = future.result()
                prepared[index] = outcome
                if isinstance(outcome, PreparedTrack):
                    self._mark_searched(index, status="searched")
                else:
                    self._mark_searched(index)

        download_plans = [
            (index, prepared[index])
            for index in selected
            if isinstance(prepared[index], PreparedTrack)
        ]
        for index in selected:
            plan = prepared[index]
            if not isinstance(plan, PreparedTrack):
                self._finalize_track(index, plan)

        if download_plans:
            self._check_deadline()

            def phase_two(index_plan):
                return resolver.complete(
                    index_plan[1], stage_callback=self._stage_reporter(index_plan[0])
                )

            with ThreadPoolExecutor(
                max_workers=self.max_download_workers,
                thread_name_prefix=f"spotm3u-download-{self.job_id}",
            ) as pool:
                future_to_index = {
                    pool.submit(phase_two, index_plan): index_plan[0]
                    for index_plan in download_plans
                }
                for future in as_completed(future_to_index):
                    self._check_deadline()
                    index = future_to_index[future]
                    self._finalize_track(index, future.result())

    def _check_deadline(self) -> None:
        """Raise when the whole job has exceeded its ``timeout`` budget."""
        if self._deadline is None:
            return
        if time.monotonic() >= self._deadline:
            raise JobTimeoutError(f"job timed out after {self.timeout} seconds")

    def _stage_reporter(self, index: int) -> Callable[[TrackStage], None]:
        def report(stage: TrackStage) -> None:
            self._set_track_status(index, stage)

        return report

    def _set_track_status(self, index: int, status: TrackProcessingStatus) -> None:
        with self._lock:
            self._current_index = index
            current = self._track_states[index]
            self._track_states[index] = TrackJobState(
                current.index,
                current.title,
                current.artists,
                status,
                current.reason,
                current.local_path,
                current.source_url,
                current.resolution,
                time.time(),
            )
        log = TrackLogger(logger, job_id=self.job_id, track=self.tracks[index])
        log.debug("stage=%s index=%d", status, index)

    def _mark_searched(self, index: int, status: TrackProcessingStatus | None = None) -> None:
        with self._lock:
            self._searched += 1
            self._current_index = index
            if status is not None:
                current = self._track_states[index]
                self._track_states[index] = TrackJobState(
                    current.index,
                    current.title,
                    current.artists,
                    status,
                    current.reason,
                    current.local_path,
                    current.source_url,
                    current.resolution,
                    time.time(),
                )
        TrackLogger(logger, job_id=self.job_id, track=self.tracks[index]).debug(
            "searched index=%d", index
        )

    def _finalize_track(self, index: int, result: TrackResolution) -> None:
        status = TRACK_STATUS_TERMINAL.get(result.status, "failed")
        with self._lock:
            self._current_index = index
            self._results[index] = result
            current = self._track_states[index]
            self._track_states[index] = TrackJobState(
                current.index,
                current.title,
                current.artists,
                status,
                result.reason,
                str(result.local_path) if result.local_path is not None else None,
                result.source_url,
                result.status,
                current.stage_started_at,
            )
        log = TrackLogger(logger, job_id=self.job_id, track=self.tracks[index])
        if result.successful:
            log.info(
                "finalized index=%d status=%s resolution=%s path=%s",
                index,
                result.status,
                result.status,
                result.local_path,
            )
        else:
            log.warning(
                "finalized index=%d status=%s resolution=%s reason=%s",
                index,
                status,
                result.status,
                result.reason or "no reason given",
            )

    def output_missing(self, index: int) -> bool:
        """Return True when a completed track's audio file is no longer on disk."""
        with self._lock:
            if index < 0 or index >= len(self._track_states):
                return False
            state = self._track_states[index]
            return state.status == "complete" and _missing_file(state.local_path)

    def snapshot(self) -> dict[str, object]:
        """Return a consistent, JSON-compatible view of the job state."""
        with self._lock:
            track_states = [state.as_dict() for state in self._track_states]
            stale_outputs = 0
            for state in self._track_states:
                # A completed track whose file is gone is reported as such so the
                # interface can offer a retry instead of showing a working track.
                state_dict = track_states[state.index]
                missing = state.status == "complete" and _missing_file(state.local_path)
                state_dict["file_missing"] = missing
                stale_outputs += int(missing)
            current = track_states[self._current_index] if self._current_index is not None else None
            resolutions = [state.resolution for state in self._track_states]
            counts = {
                resolution: resolutions.count(resolution)
                for resolution in (
                    "local",
                    "downloaded",
                    "missing",
                    "ambiguous",
                    "rejected",
                    "failed",
                    "uncertain",
                    "",
                )
            }
            counts["total"] = len(self._track_states)
            counts["successful"] = counts["local"] + counts["downloaded"]
            progress = self._progress_indices(self._pending, len(self._track_states))
            return {
                "job_id": self.job_id,
                "playlist": {
                    "id": self.playlist_id,
                    "name": self.playlist_name,
                    "total_tracks": len(self.tracks),
                },
                "current_track": current,
                "completed": sum(
                    self._track_states[index].status in {"complete", "failed", "ambiguous"}
                    for index in progress
                ),
                # During a retry only the retried tracks are in flight, so the
                # progress bar is measured against the retry set, not the playlist.
                "progress_total": len(progress),
                "searched": self._searched,
                "successful": sum(state.status == "complete" for state in self._track_states),
                "failed": sum(state.status == "failed" for state in self._track_states),
                "ambiguous": sum(state.status == "ambiguous" for state in self._track_states),
                "stale_outputs": stale_outputs,
                "counts": counts,
                "status": self._status,
                "error": self._error,
                "output_dir": str(self.output_dir),
                "fast_mode": self.fast_mode,
                "m3u_path": (str(self._m3u_path) if self._m3u_path is not None else None),
                "tracks": track_states,
                "started_at": (
                    int(self._started_at * 1000) if self._started_at is not None else None
                ),
                "completed_at": (
                    int(self._completed_at * 1000) if self._completed_at is not None else None
                ),
            }

    def as_dict(self) -> dict[str, object]:
        return self.snapshot()


class JobManager:
    """Registry of active processing jobs scoped to an application instance."""

    def __init__(self) -> None:
        self._jobs: dict[str, ProcessingJob] = {}
        self._lock = threading.Lock()

    def submit(self, job: ProcessingJob) -> None:
        with self._lock:
            self._jobs[self._key(job.job_id, job.playlist_id)] = job
            job.manager = self

    @staticmethod
    def _key(job_id: str, playlist_id: str) -> str:
        return f"{job_id}:{playlist_id}"

    def get(self, job_id: str, playlist_id: str | None = None) -> ProcessingJob | None:
        with self._lock:
            if playlist_id is not None:
                return self._jobs.get(self._key(job_id, playlist_id))
            matches = [job for job in self._jobs.values() if job.job_id == job_id]
            return matches[0] if len(matches) == 1 else None

    def active_job_ids(self) -> set[str]:
        """Return ids of jobs that are still queued or running."""
        with self._lock:
            return {
                job.job_id for job in self._jobs.values() if job.status in {"queued", "running"}
            }


__all__ = [
    "JobManager",
    "JobStartError",
    "JobTimeoutError",
    "ProcessingJob",
    "TrackJobState",
    "TrackProcessingStatus",
]
