""""Tests for the background processing job abstraction."""

import threading
from pathlib import Path

from spotm3u.jobs import JobManager, JobStartError, ProcessingJob
from spotm3u.models import ResolvedTrack, Track
from spotm3u.resolution import TrackResolution


def _track(title: str = "Song", artist: str = "Artist") -> Track:
    return Track(title, [artist], duration_ms=200_000)


def _resolution(track: Track, status: str, *, path: Path | None = None) -> TrackResolution:
    if path is not None:
        resolved = ResolvedTrack(track, path, status=status)
        return TrackResolution(track, status, resolved=resolved, reasons=(status,))
    return TrackResolution(track, status, reasons=(status,))


class FakeResolver:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.stage_sequences: list[list[str]] = []

    def resolve(self, track, *, stage_callback=None):
        stages: list[str] = []
        if stage_callback is None:
            stage_callback = lambda _stage: None
        for stage in (
            "resolving-local",
            "searching",
            "validating-source",
            "downloading",
            "validating-audio",
        ):
            stage_callback(stage)
            stages.append(stage)
        self.stage_sequences.append(stages)
        return self.outcomes.pop(0)


def _job(tracks, outcomes, output: Path, *, job_id: str = "abc") -> ProcessingJob:
    fake = FakeResolver(outcomes)
    job = ProcessingJob(
        job_id=job_id,
        playlist_id="0",
        playlist_name="Playlist",
        tracks=tracks,
        output_dir=output,
        resolver_factory=lambda: fake,
    )
    job._fake = fake
    return job


def test_job_tracks_state_counts_and_m3u(tmp_path: Path) -> None:
    tracks = [_track("A"), _track("B"), _track("C")]
    local_file = tmp_path / "Artist - A.mp3"
    local_file.write_bytes(b"audio")
    outcomes = [
        _resolution(tracks[0], "local", path=local_file),
        _resolution(tracks[1], "ambiguous"),
        _resolution(tracks[2], "failed"),
    ]

    job = _job(tracks, outcomes, tmp_path / "output")
    job.start()
    job.wait(timeout=5)

    snapshot = job.as_dict()
    assert snapshot["status"] == "completed"
    assert snapshot["playlist"]["id"] == "0"
    assert snapshot["playlist"]["name"] == "Playlist"
    assert snapshot["playlist"]["total_tracks"] == 3
    assert snapshot["completed"] == 3
    assert snapshot["successful"] == 1
    assert snapshot["failed"] == 1
    assert snapshot["ambiguous"] == 1
    assert snapshot["output_dir"] == str(tmp_path / "output")
    assert snapshot["m3u_path"] == str(tmp_path / "output" / "playlist.m3u")
    assert [state["status"] for state in snapshot["tracks"]] == [
        "complete",
        "ambiguous",
        "failed",
    ]
    assert snapshot["tracks"][0]["local_path"] == str(local_file)

    m3u = (tmp_path / "output" / "playlist.m3u").read_text(encoding="utf-8")
    assert str(local_file) in m3u
    assert "B" not in m3u
    assert "C" not in m3u


def test_job_exposes_intermediate_stage_reporting(tmp_path: Path) -> None:
    tracks = [_track("A")]
    local_file = tmp_path / "Artist - A.mp3"
    local_file.write_bytes(b"audio")

    job = _job(
        tracks,
        [_resolution(tracks[0], "local", path=local_file)],
        tmp_path / "output",
    )
    job.start()
    job.wait(timeout=5)

    fake = job._fake
    assert fake.stage_sequences == [
        ["resolving-local", "searching", "validating-source", "downloading", "validating-audio"]
    ]


def test_job_reports_running_state_while_processing(tmp_path: Path) -> None:
    release = threading.Event()
    started = threading.Event()
    track_a = _track("A")
    track_b = _track("B")

    class BlockingResolver:
        def __init__(self):
            self.first = True

        def resolve(self, track, *, stage_callback=None):
            if stage_callback is not None:
                stage_callback("resolving-local")
            if self.first:
                self.first = False
                started.set()
                release.wait(timeout=5)
            return _resolution(track, "missing")

    job = ProcessingJob(
        job_id="running",
        playlist_id="0",
        playlist_name="Playlist",
        tracks=[track_a, track_b],
        output_dir=tmp_path / "output",
        resolver_factory=BlockingResolver,
    )
    job.start()
    try:
        assert started.wait(timeout=5)
        snapshot = job.as_dict()
        assert snapshot["status"] == "running"
        assert snapshot["current_track"]["title"] == "A"
        assert snapshot["current_track"]["status"] == "resolving-local"
        assert snapshot["completed"] == 0
    finally:
        release.set()
        job.wait(timeout=5)
    assert job.as_dict()["status"] == "completed"


def test_job_cannot_be_started_twice(tmp_path: Path) -> None:
    track = _track()
    job = _job([track], [_resolution(track, "missing")], tmp_path / "output")
    job.start()
    try:
        job.start()
        assert False, "expected JobStartError"
    except JobStartError:
        pass
    job.wait(timeout=5)


def test_job_failure_sets_error_and_failed_status(tmp_path: Path) -> None:
    track = _track()

    class BrokenResolver:
        def resolve(self, track, *, stage_callback=None):
            raise RuntimeError("boom")

    job = ProcessingJob(
        job_id="broken",
        playlist_id="0",
        playlist_name="Playlist",
        tracks=[track],
        output_dir=tmp_path / "output",
        resolver_factory=lambda: BrokenResolver(),
    )
    job.start()
    job.wait(timeout=5)

    snapshot = job.as_dict()
    assert snapshot["status"] == "failed"
    assert snapshot["error"] == "boom"


def test_job_manager_registers_and_returns_jobs(tmp_path: Path) -> None:
    manager = JobManager()
    track = _track()
    job = _job([track], [_resolution(track, "missing")], tmp_path / "output", job_id="managed")

    manager.submit(job)

    assert manager.get("managed") is job
    assert job.manager is manager
    assert manager.get("unknown") is None