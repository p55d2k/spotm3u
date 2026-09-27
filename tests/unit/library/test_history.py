"""Tests for the persistent processing history: the state it records and reads."""

import sqlite3
import threading

from spotm3u.history import (
    DEFAULT_MAX_RUNS,
    INTERRUPTED_ERROR,
    MAX_TEXT_LENGTH,
    RUN_STATUSES,
    HistoryStore,
    RunIdentity,
    history_db_path,
    persisted_run_status,
    persisted_track_status,
)
from spotm3u.models import Track


def track(title: str = "Song", artist: str = "Artist") -> Track:
    return Track(
        title,
        [artist],
        album=f"{title} album",
        spotify_id=f"id-{title}",
    )


def store_at(tmp_path, **kwargs) -> HistoryStore:
    return HistoryStore(tmp_path / "state" / "history.db", **kwargs)


def run_ref(store: HistoryStore, *, key: str = "job-1:0", name: str = "Playlist") -> int:
    reference = store.begin_run(
        RunIdentity(
            job_id="job-1",
            playlist_id="0",
            playlist_name=name,
            total_tracks=1,
            output_dir="/downloads",
        ),
        [track()],
    )
    assert reference is not None
    return reference


def test_the_state_model_covers_the_six_states() -> None:
    assert RUN_STATUSES == ("queued", "processing", "completed", "failed", "cancelled", "skipped")


def test_a_job_stage_is_stored_as_processing_with_the_stage_kept() -> None:
    for stage in ("resolving-local", "searching", "downloading", "enriching-metadata"):
        assert persisted_track_status(stage) == "processing"
    assert persisted_track_status("complete") == "completed"
    assert persisted_track_status("failed") == "failed"
    # A track the job could not decide about wrote no audio, so nothing was
    # processed: it is stored as skipped rather than as a failure.
    assert persisted_track_status("ambiguous") == "skipped"
    assert persisted_track_status("skipped") == "skipped"
    assert persisted_track_status("queued") == "queued"
    # A stage a later version adds still records something usable.
    assert persisted_track_status("brand-new-stage") == "processing"


def test_a_job_status_is_stored_under_the_same_names() -> None:
    assert persisted_run_status("queued") == "queued"
    assert persisted_run_status("running") == "processing"
    assert persisted_run_status("completed") == "completed"
    assert persisted_run_status("failed") == "failed"


def test_the_file_and_its_schema_are_created_without_being_asked(tmp_path) -> None:
    store = store_at(tmp_path)

    assert store.available
    assert store.path.is_file()
    assert store.path.parent == tmp_path / "state"
    tables = {
        name
        for (name,) in sqlite3.connect(store.path).execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"runs", "tracks"} <= tables


def test_the_history_lives_in_the_per_user_data_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SPOTM3U_STATE_DIR", raising=False)
    monkeypatch.setattr("spotm3u.runtime.user_data_dir", lambda: tmp_path / "userdata")

    assert history_db_path() == tmp_path / "userdata" / "history.db"


def test_a_started_run_queues_its_tracks_with_their_identity(tmp_path) -> None:
    store = store_at(tmp_path)

    reference = store.begin_run(
        RunIdentity("job-1", "0", "Road trip", 2, "/downloads", fast_mode=True),
        [track("First", "A"), track("Second", "B")],
    )

    assert reference is not None
    run = store.get_run(reference)
    assert run["playlist_name"] == "Road trip"
    assert run["status"] == "processing"
    assert run["fast_mode"] is True
    assert run["total_tracks"] == 2
    assert run["m3u_path"] is None
    assert run["finished_at"] is None
    assert [entry["title"] for entry in run["tracks"]] == ["First", "Second"]
    assert run["tracks"][0]["artists"] == "A"
    assert run["tracks"][0]["album"] == "First album"
    assert run["tracks"][0]["spotify_id"] == "id-First"
    assert all(entry["status"] == "queued" for entry in run["tracks"])
    assert run["counts"] == {
        "queued": 2,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "skipped": 0,
    }


def test_a_track_records_its_stage_and_then_its_outcome(tmp_path) -> None:
    store = store_at(tmp_path)
    reference = run_ref(store)

    store.set_track_stage(reference, 0, status="processing", stage="downloading")
    in_flight = store.get_run(reference)["tracks"][0]
    assert in_flight["status"] == "processing"
    assert in_flight["stage"] == "downloading"
    assert in_flight["started_at"] is not None

    store.finish_track(
        reference,
        0,
        status="completed",
        stage="downloaded",
        resolution="downloaded",
        source_url="https://example.com/song",
        output_path="/downloads/Artist - Song.mp3",
    )
    finished = store.get_run(reference)["tracks"][0]
    assert finished["status"] == "completed"
    assert finished["resolution"] == "downloaded"
    assert finished["source_url"] == "https://example.com/song"
    assert finished["output_path"] == "/downloads/Artist - Song.mp3"
    assert finished["finished_at"] is not None
    assert store.get_run(reference)["counts"]["completed"] == 1


def test_a_failure_records_its_reason_and_nothing_large(tmp_path) -> None:
    store = store_at(tmp_path)
    reference = run_ref(store)

    store.finish_track(
        reference,
        0,
        status="failed",
        stage="rejected",
        resolution="rejected",
        reason="x" * (MAX_TEXT_LENGTH * 2),
        error="y" * (MAX_TEXT_LENGTH * 2),
    )
    entry = store.get_run(reference)["tracks"][0]
    assert entry["status"] == "failed"
    assert entry["reason"] == "x" * (MAX_TEXT_LENGTH - 1) + "…"
    assert len(entry["error"]) == MAX_TEXT_LENGTH


def test_an_ambiguous_track_is_stored_as_skipped(tmp_path) -> None:
    store = store_at(tmp_path)
    reference = run_ref(store)

    store.finish_track(
        reference, 0, status=persisted_track_status("ambiguous"), resolution="ambiguous"
    )

    assert store.get_run(reference)["tracks"][0]["status"] == "skipped"
    assert store.get_run(reference)["counts"]["skipped"] == 1


def test_a_retry_requeues_only_its_tracks_and_counts_the_attempt(tmp_path) -> None:
    store = store_at(tmp_path)
    reference = store.begin_run(
        RunIdentity("job-1", "0", "Playlist", 2), [track("First"), track("Second")]
    )
    store.finish_track(reference, 0, status="completed", resolution="local", output_path="/a.mp3")
    store.finish_track(reference, 1, status="failed", resolution="missing", reason="no source")
    store.finish_run(reference, status="completed", m3u_path="/playlist-0.m3u")

    store.requeue_tracks(reference, [1])

    kept, retried = store.get_run(reference)["tracks"]
    assert kept["status"] == "completed"
    assert kept["retry_count"] == 0
    assert kept["output_path"] == "/a.mp3"
    assert retried["status"] == "queued"
    assert retried["retry_count"] == 1
    # The failure that sent it back to queued is kept, not erased: it is why the
    # track is queued again, and the attempt row holds the full record.
    assert retried["reason"] == "no source"
    assert retried["output_path"] is None
    assert retried["finished_at"] is None
    # The run is in flight again while the retry runs.
    assert store.get_run(reference)["status"] == "processing"
    assert store.get_run(reference)["finished_at"] is None


def test_a_retry_keeps_the_finished_attempt_of_the_track_it_replaces(tmp_path) -> None:
    store = store_at(tmp_path)
    reference = run_ref(store)
    store.finish_track(
        reference,
        0,
        status="failed",
        resolution="rejected",
        source_url="https://example.invalid/a",
        reason="no candidate passed source validation",
    )
    store.finish_run(reference, status="completed")

    store.requeue_tracks(reference, [0])
    store.finish_track(
        reference, 0, status="completed", resolution="downloaded", output_path="/a.mp3"
    )

    track = store.get_run(reference)["tracks"][0]
    first, second = track["attempts"]
    assert (first["attempt"], first["status"]) == (1, "failed")
    assert first["reason"] == "no candidate passed source validation"
    assert first["source_url"] == "https://example.invalid/a"
    assert first["finished_at"] is not None
    assert (second["attempt"], second["status"]) == (2, "completed")
    assert second["output_path"] == "/a.mp3"
    # The track row itself reports the attempt that is current.
    assert track["status"] == "completed"
    assert track["retry_count"] == 1


def test_a_retry_of_a_track_that_never_finished_closes_its_open_attempt(tmp_path) -> None:
    store = store_at(tmp_path)
    reference = run_ref(store)
    store.set_track_stage(reference, 0, status="processing", stage="downloading")

    store.requeue_tracks(reference, [0])

    attempts = store.get_run(reference)["tracks"][0]["attempts"]
    assert [attempt["attempt"] for attempt in attempts] == [1, 2]
    assert attempts[0]["status"] == "cancelled"
    assert attempts[0]["error"] == INTERRUPTED_ERROR
    assert attempts[0]["finished_at"] is not None
    assert attempts[1]["status"] == "queued"


def test_a_history_written_before_attempts_gets_its_state_backfilled(tmp_path) -> None:
    """An older database has tracks that were retried with nothing to show for it.

    The attempts it lost cannot be recovered, so the track is given the one
    attempt that is still known, numbered with the retry count it reached. This
    is why the two are reported separately rather than derived from each other.
    """
    path = tmp_path / "state" / "history.db"
    store = store_at(tmp_path)
    reference = run_ref(store)
    store.finish_track(reference, 0, status="failed", resolution="missing", reason="no source")
    store.finish_run(reference, status="completed")
    store.close()

    # Stand in for a database written before the attempts table existed.
    legacy = sqlite3.connect(path)
    legacy.execute("DROP TABLE attempts")
    legacy.execute("UPDATE tracks SET retry_count = 2")
    legacy.commit()
    legacy.close()

    reopened = HistoryStore(path)
    attempts = reopened.get_run(reference)["tracks"][0]["attempts"]

    assert [attempt["attempt"] for attempt in attempts] == [3]
    assert attempts[0]["status"] == "failed"
    assert attempts[0]["reason"] == "no source"
    assert reopened.get_run(reference)["tracks"][0]["retry_count"] == 2
    reopened.close()


def test_attempts_are_dropped_with_the_run_that_keeps_them(tmp_path) -> None:
    store = store_at(tmp_path, max_runs=1)
    first = run_ref(store)
    store.finish_track(first, 0, status="failed", reason="no source")
    store.finish_run(first, status="completed")
    store.requeue_tracks(first, [0])
    store.finish_track(first, 0, status="completed", output_path="/a.mp3")
    store.finish_run(first, status="completed")

    store.begin_run(RunIdentity("job-2", "0", "Other", 1), [track("Second")])
    store.finish_run(store.list_runs(limit=1)[0]["id"], status="completed")

    # Retention drops the oldest run, and the attempt rows go with their track
    # rather than being left behind as orphans.
    assert store.get_run(first) is None
    remaining = store.get_run(store.list_runs(limit=1)[0]["id"])
    assert [entry["attempt"] for entry in remaining["tracks"][0]["attempts"]] == [1]


def test_a_finished_run_records_its_playlist_and_its_error(tmp_path) -> None:
    store = store_at(tmp_path)
    reference = run_ref(store)

    store.finish_run(reference, status="failed", error="The conversion stopped unexpectedly.")

    run = store.get_run(reference)
    assert run["status"] == "failed"
    assert run["error"] == "The conversion stopped unexpectedly."
    assert run["finished_at"] is not None


def test_a_second_conversion_of_the_same_upload_is_the_same_run(tmp_path) -> None:
    store = store_at(tmp_path)
    first = run_ref(store)
    store.finish_run(first, status="completed")

    again = store.begin_run(RunIdentity("job-1", "0", "Playlist", 1), [track()])

    assert again == first
    assert store.count_runs() == 1


def test_runs_are_listed_newest_first_by_default(tmp_path) -> None:
    store = store_at(tmp_path)
    for index in range(3):
        reference = store.begin_run(
            RunIdentity("job-1", str(index), f"Playlist {index}", 1), [track()]
        )
        store.finish_run(reference, status="completed")

    assert [run["playlist_name"] for run in store.list_runs()] == [
        "Playlist 2",
        "Playlist 1",
        "Playlist 0",
    ]
    assert [run["playlist_name"] for run in store.list_runs(order="oldest")] == [
        "Playlist 0",
        "Playlist 1",
        "Playlist 2",
    ]
    assert [run["playlist_name"] for run in store.list_runs(order="name")] == [
        "Playlist 0",
        "Playlist 1",
        "Playlist 2",
    ]


def test_runs_are_filtered_by_state_and_by_text(tmp_path) -> None:
    store = store_at(tmp_path)
    done = store.begin_run(RunIdentity("job-1", "0", "Commute", 1), [track("First")])
    store.finish_track(done, 0, status="completed", resolution="local")
    store.finish_run(done, status="completed")
    lost = store.begin_run(
        RunIdentity("job-1", "1", "Workout", 1), [track("Second", "Someone Else")]
    )
    store.finish_track(lost, 0, status="failed", resolution="missing")
    store.finish_run(lost, status="failed")

    assert [run["status"] for run in store.list_runs(status="failed")] == ["failed"]
    assert store.count_runs(status="completed") == 1
    # The text matches the playlist name, a track title, or a track artist.
    assert [run["playlist_name"] for run in store.list_runs(query="work")] == ["Workout"]
    assert [run["playlist_name"] for run in store.list_runs(query="first")] == ["Commute"]
    assert [run["playlist_name"] for run in store.list_runs(query="someone")] == ["Workout"]
    assert store.list_runs(query="nothing here") == []
    assert store.count_runs(query="second") == 1


def test_a_page_reports_what_the_filters_matched(tmp_path) -> None:
    store = store_at(tmp_path)
    for index in range(5):
        reference = store.begin_run(
            RunIdentity("job-1", str(index), f"Playlist {index}", 1), [track()]
        )
        store.finish_run(reference, status="completed")

    page = store.list_runs(limit=2, offset=2)

    assert [run["playlist_name"] for run in page] == ["Playlist 2", "Playlist 1"]
    assert store.count_runs() == 5
    assert store.list_runs(limit=0) != []  # a page always holds at least one row


def test_old_runs_are_pruned_and_a_running_one_is_kept(tmp_path) -> None:
    store = store_at(tmp_path, max_runs=2)
    for index in range(3):
        reference = store.begin_run(
            RunIdentity("job-1", str(index), f"Playlist {index}", 1), [track()]
        )
        store.finish_run(reference, status="completed")
    running = store.begin_run(RunIdentity("job-2", "0", "In flight", 1), [track()])
    for index in range(3, 6):
        reference = store.begin_run(
            RunIdentity("job-3", str(index), f"Later {index}", 1), [track()]
        )
        store.finish_run(reference, status="completed")

    names = [run["playlist_name"] for run in store.list_runs()]
    assert "In flight" in names
    assert "Playlist 0" not in names
    assert store.get_run(running)["status"] == "processing"


def test_the_default_limit_keeps_a_year_of_conversions(tmp_path) -> None:
    assert HistoryStore(tmp_path / "history.db").max_runs == DEFAULT_MAX_RUNS


def test_a_run_left_in_flight_by_a_closed_app_is_recorded_as_cancelled(tmp_path) -> None:
    path = tmp_path / "state" / "history.db"
    first = HistoryStore(path)
    reference = first.begin_run(
        RunIdentity("job-1", "0", "Interrupted", 2), [track("A"), track("B")]
    )
    first.set_track_stage(reference, 0, status="processing", stage="downloading")
    first.finish_track(reference, 1, status="completed", resolution="local", output_path="/a.mp3")
    first.close()  # the app exits here, mid-conversion

    # A new session over the same file: nothing is running, so the run the old
    # one left in flight is closed out instead of claiming to still be running.
    second = HistoryStore(path)
    run = second.get_run(reference)
    assert run["status"] == "cancelled"
    assert run["error"] == INTERRUPTED_ERROR
    assert run["finished_at"] is not None
    assert [entry["status"] for entry in run["tracks"]] == ["cancelled", "completed"]
    assert run["tracks"][0]["cancelled"] is True
    assert run["tracks"][0]["error"] == INTERRUPTED_ERROR
    # The track that had already finished keeps its outcome.
    assert run["tracks"][1]["output_path"] == "/a.mp3"
    second.close()


def test_a_disabled_store_records_nothing_and_reads_empty(tmp_path) -> None:
    store = HistoryStore(tmp_path / "state" / "history.db", enabled=False)

    assert not store.available
    assert not store.path.exists()
    assert store.begin_run(RunIdentity("job-1", "0", "Playlist", 1), [track()]) is None
    store.set_track_stage(1, 0, status="processing", stage="searching")
    store.finish_track(1, 0, status="completed")
    store.requeue_tracks(1, [0])
    store.finish_run(1, status="completed")
    assert store.list_runs() == []
    assert store.count_runs() == 0
    assert store.get_run(1) is None
    store.close()


def test_an_unusable_location_disables_the_store_instead_of_raising(tmp_path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")

    store = HistoryStore(blocker / "nested" / "history.db")

    assert not store.available
    assert store.failure
    assert store.list_runs() == []
    assert store.begin_run(RunIdentity("job-1", "0", "Playlist", 1), [track()]) is None
    store.close()


def test_concurrent_writers_from_worker_threads_all_land(tmp_path) -> None:
    store = store_at(tmp_path)
    tracks = [track(f"Song {index}") for index in range(24)]
    reference = store.begin_run(RunIdentity("job-1", "0", "Parallel", len(tracks)), tracks)

    def resolve(position: int) -> None:
        store.set_track_stage(reference, position, status="processing", stage="downloading")
        store.finish_track(
            reference,
            position,
            status="completed",
            resolution="downloaded",
            output_path=f"/{position}.mp3",
        )

    threads = [
        threading.Thread(target=resolve, args=(position,)) for position in range(len(tracks))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    store.finish_run(reference, status="completed")

    run = store.get_run(reference)
    assert run["counts"]["completed"] == len(tracks)
    assert len(run["tracks"]) == len(tracks)
    store.close()
