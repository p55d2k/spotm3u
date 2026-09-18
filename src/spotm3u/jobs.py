"""Background job abstraction for processing a playlist without blocking a request."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .m3u.writer import write_m3u
from .models import Track
from .resolution import TrackResolution, TrackResolver, TrackStage

JobStatus = Literal["queued", "running", "completed", "failed"]
TrackProcessingStatus = Literal[
    "queued",
    "resolving-local",
    "searching",
    "validating-source",
    "downloading",
    "validating-audio",
    "complete",
    "failed",
    "ambiguous",
    "skipped",
]
ResolverFactory = Callable[[], TrackResolver]

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
    ) -> None:
        self.job_id = job_id
        self.playlist_id = playlist_id
        self.playlist_name = playlist_name
        self.tracks = tuple(tracks)
        self.output_dir = Path(output_dir)
        self.resolver_factory = resolver_factory
        self.manager: JobManager | None = None

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status: JobStatus = "queued"
        self._current_index: int | None = None
        self._error: str | None = None
        self._m3u_path: Path | None = None
        self._track_states: list[TrackJobState] = [
            TrackJobState(index, track.title, tuple(track.artists), "queued")
            for index, track in enumerate(self.tracks)
        ]

    @property
    def status(self) -> JobStatus:
        with self._lock:
            return self._status

    @property
    def completed(self) -> int:
        with self._lock:
            return sum(
                state.status in {"complete", "failed", "ambiguous"}
                for state in self._track_states
            )

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

    def _run(self) -> None:
        try:
            resolver = self.resolver_factory()
            output_dir = self.output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            results: list[TrackResolution] = []
            for index, track in enumerate(self.tracks):
                with self._lock:
                    self._current_index = index
                result = resolver.resolve(
                    track, stage_callback=self._stage_reporter(index)
                )
                self._finalize_track(index, result)
                results.append(result)

            m3u_path = output_dir / "playlist.m3u"
            write_m3u(m3u_path, results)
            with self._lock:
                self._m3u_path = m3u_path
                self._current_index = None
                self._status = "completed"
        except Exception as exc:  # pragma: no cover - defensive final state
            with self._lock:
                self._current_index = None
                self._error = str(exc)
                self._status = "failed"

    def _stage_reporter(self, index: int) -> Callable[[TrackStage], None]:
        def report(stage: TrackStage) -> None:
            self._set_track_status(index, stage)

        return report

    def _set_track_status(self, index: int, status: TrackProcessingStatus) -> None:
        with self._lock:
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
            )

    def _finalize_track(self, index: int, result: TrackResolution) -> None:
        status = TRACK_STATUS_TERMINAL.get(result.status, "failed")
        with self._lock:
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
            )

    def snapshot(self) -> dict[str, object]:
        """Return a consistent, JSON-compatible view of the job state."""
        with self._lock:
            track_states = [state.as_dict() for state in self._track_states]
            current = (
                track_states[self._current_index]
                if self._current_index is not None
                else None
            )
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
            return {
                "job_id": self.job_id,
                "playlist": {
                    "id": self.playlist_id,
                    "name": self.playlist_name,
                    "total_tracks": len(self.tracks),
                },
                "current_track": current,
                "completed": sum(
                    state.status in {"complete", "failed", "ambiguous"}
                    for state in self._track_states
                ),
                "successful": sum(
                    state.status == "complete" for state in self._track_states
                ),
                "failed": sum(state.status == "failed" for state in self._track_states),
                "ambiguous": sum(
                    state.status == "ambiguous" for state in self._track_states
                ),
                "counts": counts,
                "status": self._status,
                "error": self._error,
                "output_dir": str(self.output_dir),
                "m3u_path": (
                    str(self._m3u_path) if self._m3u_path is not None else None
                ),
                "tracks": track_states,
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
            self._jobs[job.job_id] = job
            job.manager = self

    def get(self, job_id: str) -> ProcessingJob | None:
        with self._lock:
            return self._jobs.get(job_id)


__all__ = [
    "JobManager",
    "JobStartError",
    "ProcessingJob",
    "TrackJobState",
    "TrackProcessingStatus",
]