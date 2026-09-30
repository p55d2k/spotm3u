"""Tests for the queue as the API exposes it.

The queue is what makes several playlists representable at once, so these drive
it through the routes a real batch uses: what a batch reports about waiting
versus running work, that the bound is configurable, that a waiting playlist can
be dropped and is recorded as cancelled rather than failed, and that queued work
is written down before it starts, so it survives the window being closed.
"""

import threading
import time
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from spotm3u.app import create_app

ZIP_HEADER = "Track URI,Track Name,Album Name,Artist Name(s),Duration (ms)\n"


def _export_zip(playlist_count: int, tracks_per_playlist: int = 1) -> bytes:
    """An Exportify archive with ``playlist_count`` single-file playlists.

    The CSVs are named by index rather than by a caller-supplied label, because
    an archive cannot hold two files of the same name and a batch of playlists
    has to be told apart by position.
    """
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for index in range(playlist_count):
            rows = "".join(
                f"spotify:track:p{index}t{track},Song {index}-{track},,Artist,200000\n"
                for track in range(tracks_per_playlist)
            )
            archive.writestr(f"playlist-{index}.csv", ZIP_HEADER + rows)
    return buffer.getvalue()


def _app(tmp_path: Path, **config):
    music = tmp_path / "music"
    music.mkdir(exist_ok=True)
    return create_app(
        {
            "UPLOAD_ROOT": tmp_path / "uploads",
            "MUSIC_LIBRARY": music,
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "HISTORY_DB": str(tmp_path / "state" / "history.db"),
            **config,
        }
    )


def _upload(client, playlist_count: int = 1) -> str:
    """Upload an archive with ``playlist_count`` playlists and return its job id."""
    response = client.post(
        "/api/upload",
        data={"file": (BytesIO(_export_zip(playlist_count)), "export.zip")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    return response.get_json()["job_id"]


def _select(client, job_id: str, count: int) -> None:
    """Choose the first ``count`` playlists, as the selection screen does."""
    ids = [str(index) for index in range(count)]
    assert (
        client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": ids}).status_code == 200
    )


def _start(client, job_id: str, count: int) -> dict:
    """Queue the first ``count`` playlists and answer with the batch summary."""
    _select(client, job_id, count)
    response = client.post(f"/api/jobs/{job_id}/processing")
    assert response.status_code == 202
    return response.get_json()


class Gate:
    """A gate the tests hold source searches at.

    Held conversions are what let a test observe the difference between work that
    has been given a queue slot and work that is only waiting, without depending
    on how long a real download happens to take. The search returns nothing once
    released, so a conversion always finishes rather than hanging on the network.
    """

    def __init__(self) -> None:
        self.released = threading.Event()

    def hold(self, monkeypatch) -> "Gate":
        monkeypatch.setattr(
            "spotm3u.web_jobs.OnlineSourceSearcher",
            lambda **_kw: type("HeldSearcher", (), self._searchers())(),
        )
        return self

    def _searchers(self) -> dict:
        wait = self.released.wait
        return {
            "search": lambda _self, _track: (wait(timeout=60), ())[1],
            "search_query": lambda _self, _track, _query: (wait(timeout=60), ())[1],
        }

    def release(self) -> None:
        self.released.set()

    def close(self) -> None:
        """Let anything still waiting through, so a failing test cannot hang."""
        self.released.set()


def _wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _queue(client) -> dict:
    queue = client.application.config["CONVERSION_QUEUE"]
    return queue.snapshot()


def test_a_batch_reports_what_is_running_and_what_is_waiting(tmp_path, monkeypatch) -> None:
    """With one slot, three playlists are one running and two waiting."""
    release = Gate().hold(monkeypatch)
    client = _app(tmp_path, QUEUE_MAX_ACTIVE=1).test_client()
    job_id = _upload(client, 3)
    body = _start(client, job_id, 3)
    assert body["queue"]["max_active"] == 1
    assert body["queue"]["total"] == 3
    assert body["queue"]["active"] == 1
    assert body["queue"]["waiting"] == 2
    assert body["status"] in {"queued", "running"}

    # Every playlist carries its own queue state, so the interface can tell a
    # waiting playlist from one that is stalled mid-conversion.
    live = client.get(f"/api/jobs/{job_id}/processing").get_json()
    states = {entry["playlist_id"]: entry["state"] for entry in live["queue"]["entries"]}
    assert sorted(states.values()) == ["active", "queued", "queued"]
    positions = {
        entry["playlist_id"]: entry["position"]
        for entry in live["queue"]["entries"]
        if entry["state"] == "queued"
    }
    assert sorted(positions.values()) == [1, 2]

    release.release()
    queue = client.application.config["CONVERSION_QUEUE"]
    assert queue.wait_for_idle(timeout=60)


def test_the_concurrency_limit_is_configurable(tmp_path, monkeypatch) -> None:
    """A configured limit is the number of playlists allowed to convert at once."""
    release = Gate().hold(monkeypatch)
    client = _app(tmp_path, QUEUE_MAX_ACTIVE=3).test_client()
    job_id = _upload(client, 4)
    body = _start(client, job_id, 4)

    assert body["queue"]["max_active"] == 3
    assert body["queue"]["active"] == 3
    assert body["queue"]["waiting"] == 1

    release.release()
    assert client.application.config["CONVERSION_QUEUE"].wait_for_idle(timeout=60)


def test_a_waiting_playlist_can_be_dropped_and_is_cancelled_not_failed(
    tmp_path, monkeypatch
) -> None:
    """Cancelling before a slot is free stops work that never started."""
    release = Gate().hold(monkeypatch)
    client = _app(tmp_path, QUEUE_MAX_ACTIVE=1).test_client()
    job_id = _upload(client, 2)
    _start(client, job_id, 2)
    assert _wait_for(lambda: _queue(client)["active"] == 1)

    cancelled = client.post(f"/api/jobs/{job_id}/playlists/1/queue/cancel")

    assert cancelled.status_code == 200
    assert cancelled.get_json()["status"] == "cancelled"
    assert cancelled.get_json()["queue_state"] == "cancelled"
    assert _queue(client)["cancelled"] == 1
    assert _queue(client)["failed"] == 0, "stopping work is not failing it"

    release.release()
    queue = client.application.config["CONVERSION_QUEUE"]
    assert queue.wait_for_idle(timeout=60)
    runs = client.get("/api/history").get_json()["runs"]
    assert {run["status"] for run in runs} == {"completed", "cancelled"}


def test_a_running_playlist_cannot_be_dropped_from_the_queue(tmp_path, monkeypatch) -> None:
    """Stopping work in progress is refused, not pretended to have happened."""
    release = Gate().hold(monkeypatch)
    client = _app(tmp_path, QUEUE_MAX_ACTIVE=1).test_client()
    job_id = _upload(client)
    _start(client, job_id, 1)
    assert _wait_for(lambda: _queue(client)["active"] == 1)

    refused = client.post(f"/api/jobs/{job_id}/playlists/0/queue/cancel")

    assert refused.status_code == 409
    assert refused.get_json()["code"] == "job_running"

    release.release()
    assert client.application.config["CONVERSION_QUEUE"].wait_for_idle(timeout=60)


def test_a_playlist_the_queue_never_took_cannot_be_cancelled(tmp_path) -> None:
    client = _app(tmp_path).test_client()
    job_id = _upload(client)
    _select(client, job_id, 1)

    refused = client.post(f"/api/jobs/{job_id}/playlists/0/queue/cancel")

    assert refused.status_code == 404
    assert refused.get_json()["code"] == "job_not_found"


def test_queued_work_is_written_down_before_it_starts(tmp_path, monkeypatch) -> None:
    """Waiting work is already recorded, so closing the window cannot lose it."""
    release = Gate().hold(monkeypatch)
    client = _app(tmp_path, QUEUE_MAX_ACTIVE=1).test_client()
    job_id = _upload(client, 2)
    _start(client, job_id, 2)
    assert _wait_for(lambda: _queue(client)["waiting"] == 1)

    runs = client.get("/api/history").get_json()["runs"]

    assert len(runs) == 2, "both playlists should be recorded while one still waits"
    assert sorted(run["status"] for run in runs) == ["processing", "queued"]

    release.release()
    assert client.application.config["CONVERSION_QUEUE"].wait_for_idle(timeout=60)
    finished = client.get("/api/history").get_json()["runs"]
    assert {run["status"] for run in finished} == {"completed"}


def test_a_retry_joins_the_queue_instead_of_running_around_it(tmp_path, monkeypatch) -> None:
    """A retry is queued work, so it cannot slip past the limit."""
    release = Gate().hold(monkeypatch)
    client = _app(tmp_path, QUEUE_MAX_ACTIVE=1).test_client()
    job_id = _upload(client)
    _start(client, job_id, 1)
    queue = client.application.config["CONVERSION_QUEUE"]
    release.release()
    assert queue.wait_for_idle(timeout=60)

    # Hold the retry this time, so it can be seen waiting for the slot rather
    # than finishing before anything is asserted about it.
    retry_gate = Gate().hold(monkeypatch)
    retried = client.post(f"/api/jobs/{job_id}/playlists/0/processing/retry")

    assert retried.status_code == 200
    assert retried.get_json()["retried"] == 1
    assert retried.get_json()["queue_state"] == "active"
    assert _wait_for(lambda: _queue(client)["active"] == 1)

    retry_gate.release()
    assert queue.wait_for_idle(timeout=60)
    assert queue.count_in("completed") == 1, "the retry is the same conversion, not a second run"


def test_the_config_setting_reaches_the_queue(tmp_path, monkeypatch) -> None:
    """The documented ``[queue] max_active_playlists`` setting is the limit."""
    config = tmp_path / "config.toml"
    config.write_text(
        "[queue]\nmax_active_playlists = 3\n"
        f'[web]\nupload_root = "{tmp_path / "uploads"}"\n'
        f'music_library = "{tmp_path / "music"}"\n'
        f'download_dir = "{tmp_path / "downloads"}"\n'
    )
    monkeypatch.setenv("SPOTM3U_CONFIG", str(config))
    (tmp_path / "music").mkdir(exist_ok=True)

    app = create_app()

    assert app.config["QUEUE_MAX_ACTIVE"] == 3
    assert app.config["CONVERSION_QUEUE"].max_active == 3


def test_the_single_playlist_status_reports_its_queue_state(tmp_path, monkeypatch) -> None:
    """One playlist's own route says whether it is working or waiting."""
    release = Gate().hold(monkeypatch)
    client = _app(tmp_path, QUEUE_MAX_ACTIVE=1).test_client()
    job_id = _upload(client, 2)
    _start(client, job_id, 2)
    assert _wait_for(lambda: _queue(client)["waiting"] == 1)

    running = client.get(f"/api/jobs/{job_id}/playlists/0/processing").get_json()
    waiting = client.get(f"/api/jobs/{job_id}/playlists/1/processing").get_json()

    assert running["queue_state"] == "active"
    assert running["queue_position"] is None
    assert waiting["queue_state"] == "queued"
    assert waiting["queue_position"] == 1
    # Waiting work has made no progress, and says so rather than reporting zero
    # of zero as though it were under way.
    assert waiting["status"] == "queued"
    assert waiting["resolved"] == 0

    release.release()
    assert client.application.config["CONVERSION_QUEUE"].wait_for_idle(timeout=60)
