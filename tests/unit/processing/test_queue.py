"""Tests for the bounded download queue.

The queue's job is to accept more work than the machine can take right now and
still run only a fixed number of conversions at a time, so these check the bound
itself (never more than ``max_active`` running), that waiting work keeps its
place and can be dropped, that a slot is handed on when one finishes, and that
a retry takes a slot like anything else.
"""

import threading
import time
from pathlib import Path

import pytest

from spotm3u.history import HistoryStore
from spotm3u.jobs import ProcessingJob
from spotm3u.models import ResolvedTrack, Track
from spotm3u.queue import (
    QUEUE_JOB_CANCELLED,
    ConversionQueue,
    QueueClosedError,
    QueueJobConflictError,
    QueueJobNotCancellableError,
    UnknownQueueEntryError,
)
from spotm3u.resolution import TrackResolution


def _track(title: str = "Song") -> Track:
    return Track(title, ["Artist"], duration_ms=200_000)


class GateResolver:
    """A resolver that holds every track until the test lets it through.

    A conversion only finishes when its resolver returns, so a test can keep
    conversions in flight for as long as it needs and observe exactly how many
    are running at once.
    """

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.peak = 0
        self.running = 0
        self._lock = threading.Lock()
        self._release = threading.Event()
        self.calls = 0

    def release(self) -> None:
        self._release.set()

    def resolve(self, track, *, stage_callback=None, exclude_urls=()):
        with self._lock:
            self.calls += 1
            self.running += 1
            self.peak = max(self.peak, self.running)
            # Report the running count so a test can wait for exactly as many
            # conversions as it means to have started.
            self.entered.set()
        try:
            self._release.wait(timeout=10)
            path = Path(track.title) / f"{track.title}.mp3"
            return TrackResolution(
                track,
                "downloaded",
                resolved=ResolvedTrack(track, path, status="downloaded"),
                reasons=("downloaded",),
            )
        finally:
            with self._lock:
                self.running -= 1


def _job(
    tmp_path: Path,
    playlist_id: str,
    resolver: GateResolver,
    *,
    history: HistoryStore | None = None,
    job_id: str = "job-1",
) -> ProcessingJob:
    # The resolver factory is called once per attempt, so a retry gets a fresh
    # gate and a test can hold that attempt without also stalling the first.
    return ProcessingJob(
        job_id=job_id,
        playlist_id=playlist_id,
        playlist_name=f"Playlist {playlist_id}",
        tracks=[_track(f"{playlist_id}-track")],
        output_dir=tmp_path / "downloads",
        resolver_factory=lambda: resolver,
        history=history,
    )


def _wait_for(predicate, timeout: float = 10.0) -> bool:
    """Wait for a condition the queue reaches on its own threads."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_the_queue_runs_no_more_than_its_limit_at_once(tmp_path) -> None:
    """Five playlists, one slot: only one runs and the rest wait their turn."""
    resolver = GateResolver()
    queue = ConversionQueue(max_active=1)
    jobs = [_job(tmp_path, str(index), resolver) for index in range(5)]

    for job in jobs:
        queue.submit(job)

    assert _wait_for(lambda: resolver.running == 1), "the first conversion should start"
    assert queue.active_count == 1
    assert queue.waiting_count == 4
    # Nothing else may start while the one slot is taken, however long we wait.
    time.sleep(0.2)
    assert resolver.peak == 1
    assert queue.active_count == 1

    resolver.release()
    assert queue.wait_for_idle(timeout=30)
    assert queue.active_count == 0
    assert queue.waiting_count == 0
    assert all(job.status == "completed" for job in jobs)
    # Every conversion did run, one after another, in the order they were added.
    assert resolver.peak == 1


def test_several_playlists_run_together_when_the_limit_allows(tmp_path) -> None:
    """With room for two, two convert at once and the third waits."""
    resolver = GateResolver()
    queue = ConversionQueue(max_active=2)
    jobs = [_job(tmp_path, str(index), resolver) for index in range(3)]

    for job in jobs:
        queue.submit(job)

    assert _wait_for(lambda: resolver.running == 2), "both slots should fill"
    assert queue.active_count == 2
    assert queue.waiting_count == 1
    assert resolver.peak <= 2

    resolver.release()
    assert queue.wait_for_idle(timeout=30)
    assert resolver.peak == 2, "the third conversion should have used a freed slot"
    assert all(job.status == "completed" for job in jobs)


def test_a_waiting_conversion_keeps_its_place_in_line(tmp_path) -> None:
    """Positions are counted from one in the order work was submitted."""
    resolver = GateResolver()
    queue = ConversionQueue(max_active=1)
    jobs = [_job(tmp_path, str(index), resolver) for index in range(3)]

    for job in jobs:
        queue.submit(job)

    snapshot = queue.snapshot()
    assert snapshot["max_active"] == 1
    assert snapshot["active"] == 1
    assert snapshot["waiting"] == 2
    waiting = [entry for entry in snapshot["entries"] if entry["state"] == "queued"]
    assert [entry["playlist_id"] for entry in waiting] == ["1", "2"]
    assert [entry["position"] for entry in waiting] == [1, 2]

    resolver.release()
    assert queue.wait_for_idle(timeout=30)
    snapshot = queue.snapshot()
    assert snapshot["completed"] == 3
    assert snapshot["waiting"] == 0
    assert all(entry["position"] is None for entry in snapshot["entries"])


def test_cancelling_a_waiting_conversion_drops_it_without_starting(tmp_path) -> None:
    """Dropping queued work is not a failure and never downloads anything."""
    resolver = GateResolver()
    queue = ConversionQueue(max_active=1)
    first = _job(tmp_path, "0", resolver)
    second = _job(tmp_path, "1", resolver)
    queue.submit(first)
    queue.submit(second)

    assert _wait_for(lambda: resolver.running == 1)

    entry = queue.cancel("job-1", "1")

    assert entry.state == "cancelled"
    assert second.status == "cancelled"
    assert second.error == QUEUE_JOB_CANCELLED
    assert queue.snapshot()["cancelled"] == 1
    resolver.release()
    assert queue.wait_for_idle(timeout=30)
    assert first.status == "completed"
    # The cancelled one was never handed to a resolver at all.
    assert second.as_dict()["resolved"] == 0


def test_a_running_conversion_cannot_be_dropped_from_the_queue(tmp_path) -> None:
    """Cancelling work that has begun is refused rather than quietly ignored."""
    resolver = GateResolver()
    queue = ConversionQueue(max_active=1)
    job = _job(tmp_path, "0", resolver)
    queue.submit(job)

    assert _wait_for(lambda: resolver.running == 1)
    with pytest.raises(QueueJobNotCancellableError) as raised:
        queue.cancel("job-1", "0")

    assert raised.value.state == "active"
    resolver.release()
    assert queue.wait_for_idle(timeout=30)
    assert job.status == "completed"


def test_cancelling_something_the_queue_never_had_is_an_error(tmp_path) -> None:
    queue = ConversionQueue(max_active=1)
    with pytest.raises(UnknownQueueEntryError):
        queue.cancel("job-1", "9")


def test_a_retry_waits_for_a_slot_rather_than_overrunning_the_limit(tmp_path) -> None:
    """A retry is work like any other, so it must not bypass the bound."""
    queue = ConversionQueue(max_active=1)
    first_resolver = GateResolver()
    running = _job(tmp_path, "0", first_resolver)
    queue.submit(running)
    assert _wait_for(lambda: first_resolver.running == 1)
    first_resolver.release()
    assert queue.wait_for_idle(timeout=30)

    # Its own gate, so the retry's attempt is held in flight on its own terms.
    retry_resolver = GateResolver()
    retrying = _job(tmp_path, "1", retry_resolver)
    queue.submit(retrying)
    assert _wait_for(lambda: retry_resolver.running == 1)
    retry_resolver.release()
    assert queue.wait_for_idle(timeout=30)

    # Take the only slot with an unrelated conversion, so the retry is left with
    # nowhere to go: which is the case that would be missed by a limit checked
    # only when a conversion starts.
    blocker_resolver = GateResolver()
    queue.submit(_job(tmp_path, "2", blocker_resolver))
    assert _wait_for(lambda: blocker_resolver.running == 1)

    assert queue.submit_retry(retrying) == (0,)
    assert _wait_for(lambda: queue.waiting_count == 1), "a retry must wait for a slot"
    assert retrying.status == "queued", "a retry must not start immediately"
    assert queue.active_count == 1, "the slot belongs to the other conversion"

    blocker_resolver.release()
    assert queue.wait_for_idle(timeout=30)
    assert retrying.status == "completed"
    assert queue.count_in("completed") == 3


def test_a_retry_of_a_running_conversion_is_refused(tmp_path) -> None:
    resolver = GateResolver()
    queue = ConversionQueue(max_active=1)
    job = _job(tmp_path, "0", resolver)
    queue.submit(job)
    assert _wait_for(lambda: resolver.running == 1)

    with pytest.raises(QueueJobConflictError):
        queue.submit_retry(job)

    resolver.release()
    assert queue.wait_for_idle(timeout=30)


def test_queued_work_is_recorded_before_it_starts(tmp_path) -> None:
    """A waiting conversion is written to the history, not only once it runs.

    This is what makes a queued job survive the interface being closed: the run
    already exists, in the ``queued`` state, before any of it has happened.
    """
    resolver = GateResolver()
    store = HistoryStore(tmp_path / "state" / "history.db")
    queue = ConversionQueue(max_active=1)
    first = _job(tmp_path, "0", resolver, history=store)
    second = _job(tmp_path, "1", resolver, history=store)
    queue.submit(first)
    queue.submit(second)

    runs = store.list_runs()
    assert len(runs) == 2, "both conversions should be recorded, not only the running one"
    by_playlist = {run["playlist_name"]: run for run in runs}
    assert by_playlist["Playlist 1"]["status"] == "queued"

    resolver.release()
    assert queue.wait_for_idle(timeout=30)
    assert {run["status"] for run in store.list_runs()} == {"completed"}


def test_a_cancelled_conversion_is_recorded_as_cancelled_not_failed(tmp_path) -> None:
    resolver = GateResolver()
    store = HistoryStore(tmp_path / "state" / "history.db")
    queue = ConversionQueue(max_active=1)
    queue.submit(_job(tmp_path, "0", resolver, history=store))
    queue.submit(_job(tmp_path, "1", resolver, history=store))
    assert _wait_for(lambda: resolver.running == 1)

    queue.cancel("job-1", "1")

    resolver.release()
    assert queue.wait_for_idle(timeout=30)
    runs = {run["playlist_name"]: run for run in store.list_runs()}
    assert runs["Playlist 1"]["status"] == "cancelled"
    # Cancellation is not failure, so the track is recorded as stopped too.
    cancelled = store.get_run(runs["Playlist 1"]["id"])
    assert cancelled["tracks"][0]["status"] == "cancelled"


def test_a_closed_queue_takes_no_more_work(tmp_path) -> None:
    """Shutdown refuses new work but lets what is already running finish."""
    resolver = GateResolver()
    queue = ConversionQueue(max_active=1)
    running = _job(tmp_path, "0", resolver)
    queue.submit(running)
    assert _wait_for(lambda: resolver.running == 1)

    queue.close()

    with pytest.raises(QueueClosedError):
        queue.submit(_job(tmp_path, "1", resolver))
    with pytest.raises(QueueClosedError):
        queue.submit_retry(running)
    resolver.release()
    assert queue.wait_for_idle(timeout=30)
    assert running.status == "completed"


def test_the_limit_is_never_below_one(tmp_path) -> None:
    """A limit of zero would deadlock every conversion, so it is raised to one."""
    resolver = GateResolver()
    queue = ConversionQueue(max_active=0)
    assert queue.max_active == 1
    queue.submit(_job(tmp_path, "0", resolver))
    assert _wait_for(lambda: resolver.running == 1)
    resolver.release()
    assert queue.wait_for_idle(timeout=30)
