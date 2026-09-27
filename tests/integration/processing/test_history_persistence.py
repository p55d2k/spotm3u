"""Tests that a conversion writes its processing state down as it happens.

The job is the backend's owner of processing state, so these exercise the store
from the job rather than the API: what a real run records, what a failure
records, what a retry adds, and that a job with no store still behaves exactly
as it did before.
"""

from pathlib import Path

from spotm3u.history import HistoryStore
from spotm3u.jobs import JOB_FAILURE_MESSAGE, ProcessingJob
from spotm3u.models import ResolvedTrack, Track
from spotm3u.resolution import TrackResolution


def _track(title: str = "Song", artist: str = "Artist") -> Track:
    return Track(title, [artist], duration_ms=200_000)


class FakeResolver:
    """Hands out the prepared outcomes, walking a track through its stages."""

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)

    def resolve(self, track, *, stage_callback=None, exclude_urls=()):
        if stage_callback is not None:
            for stage in ("resolving-local", "searching", "downloading", "validating-audio"):
                stage_callback(stage)
        return self.outcomes.pop(0)


def _store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(tmp_path / "state" / "history.db")


def _job(tmp_path: Path, store: HistoryStore | None, outcomes, tracks) -> ProcessingJob:
    return ProcessingJob(
        job_id="job-abc",
        playlist_id="0",
        playlist_name="Road trip",
        tracks=tracks,
        output_dir=tmp_path / "downloads",
        resolver_factory=lambda: FakeResolver(outcomes),
        history=store,
    )


def _resolution(track: Track, status: str, *, path: Path | None = None) -> TrackResolution:
    if path is not None:
        resolved = ResolvedTrack(track, path, status=status)
        return TrackResolution(track, status, resolved=resolved, reasons=(status,))
    return TrackResolution(track, status, reasons=("no source for this track",))


def test_a_finished_conversion_is_recorded_track_by_track(tmp_path) -> None:
    store = _store(tmp_path)
    tracks = [_track("A"), _track("B")]
    outputs = {"A": tmp_path / "downloads" / "A.mp3", "B": tmp_path / "downloads" / "B.mp3"}
    for path in outputs.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
    job = _job(
        tmp_path,
        store,
        [
            _resolution(tracks[0], "downloaded", path=outputs["A"]),
            _resolution(tracks[1], "missing"),
        ],
        tracks,
    )

    job.start()
    job.wait(timeout=30)

    runs = store.list_runs()
    assert len(runs) == 1
    run = store.get_run(runs[0]["id"])
    assert run["status"] == "completed"
    assert run["playlist_name"] == "Road trip"
    assert run["m3u_path"].endswith("playlist.m3u")
    assert run["finished_at"] is not None
    assert run["counts"] == {
        "queued": 0,
        "processing": 0,
        "completed": 1,
        "failed": 1,
        "cancelled": 0,
        "skipped": 0,
    }
    recorded = {entry["title"]: entry for entry in run["tracks"]}
    assert recorded["A"]["status"] == "completed"
    assert recorded["A"]["resolution"] == "downloaded"
    assert recorded["A"]["output_path"] == str(outputs["A"])
    assert recorded["A"]["source_url"] is None
    assert recorded["B"]["status"] == "failed"
    assert recorded["B"]["resolution"] == "missing"
    assert recorded["B"]["reason"] == "no source for this track"
    assert recorded["B"]["output_path"] is None
    # The outcome does not overwrite the stage the pipeline walked through, so
    # an unresolved track says where it got to.
    assert recorded["B"]["stage"]
    assert recorded["A"]["stage"] == "enriching-metadata"


def test_an_ambiguous_track_is_recorded_as_skipped(tmp_path) -> None:
    store = _store(tmp_path)
    tracks = [_track("A")]
    job = _job(tmp_path, store, [_resolution(tracks[0], "ambiguous")], tracks)

    job.start()
    job.wait(timeout=30)

    run = store.get_run(store.list_runs()[0]["id"])
    assert run["tracks"][0]["status"] == "skipped"
    assert run["tracks"][0]["resolution"] == "ambiguous"


def test_a_conversion_that_dies_records_the_run_as_failed(tmp_path) -> None:
    store = _store(tmp_path)
    tracks = [_track("A")]

    def explode(_track_arg):
        raise RuntimeError("the resolver fell over")

    job = ProcessingJob(
        job_id="job-abc",
        playlist_id="0",
        playlist_name="Road trip",
        tracks=tracks,
        output_dir=tmp_path / "downloads",
        resolver_factory=explode,
        history=store,
    )

    job.start()
    job.wait(timeout=30)

    run = store.get_run(store.list_runs()[0]["id"])
    assert run["status"] == "failed"
    assert run["error"]
    assert run["finished_at"] is not None
    # The track never reached a decision of its own, so it is recorded as
    # failed with the job's reason: a track left queued would never be
    # retryable, and would sit in the history as processing until a restart.
    assert run["tracks"][0]["status"] == "failed"
    assert run["tracks"][0]["reason"] == JOB_FAILURE_MESSAGE
    assert run["tracks"][0]["finished_at"] is not None


def test_a_retry_is_recorded_as_another_attempt_on_the_same_run(tmp_path) -> None:
    store = _store(tmp_path)
    tracks = [_track("A"), _track("B")]
    outputs = {"A": tmp_path / "downloads" / "A.mp3", "B": tmp_path / "downloads" / "B.mp3"}
    for path in outputs.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
    job = _job(
        tmp_path,
        store,
        [
            _resolution(tracks[0], "downloaded", path=outputs["A"]),
            _resolution(tracks[1], "missing"),
        ],
        tracks,
    )
    job.start()
    job.wait(timeout=30)
    run_ref = store.list_runs()[0]["id"]
    assert job.retry() == (1,)

    job.wait(timeout=30)

    runs = store.list_runs()
    assert len(runs) == 1, "a retry is not a second conversion"
    run = store.get_run(run_ref)
    recorded = {entry["title"]: entry for entry in run["tracks"]}
    # Only the unresolved track was picked up, and it kept the same row.
    assert recorded["A"]["retry_count"] == 0
    assert recorded["B"]["retry_count"] == 1
    assert run["status"] == "completed"


def test_a_job_without_a_store_still_processes(tmp_path) -> None:
    tracks = [_track("A")]
    job = _job(tmp_path, None, [_resolution(tracks[0], "missing")], tracks)

    job.start()
    job.wait(timeout=30)

    assert job.status == "completed"
    assert job.failed == 1


def test_an_unavailable_store_never_fails_a_conversion(tmp_path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    store = HistoryStore(blocker / "history.db")
    tracks = [_track("A")]
    job = _job(tmp_path, store, [_resolution(tracks[0], "missing")], tracks)

    job.start()
    job.wait(timeout=30)

    assert job.status == "completed"
    assert job.failed == 1
    assert store.list_runs() == []
