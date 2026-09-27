"""Tests for the download history routes: what a conversion leaves behind."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from spotm3u.app import create_app
from spotm3u.history import HistoryStore, RunIdentity, persisted_track_status
from spotm3u.models import Track
from spotm3u.online.search import SourceCandidate

ZIP_HEADER = "Track URI,Track Name,Album Name,Artist Name(s),Duration (ms)\n"


def _export_zip(name: str, *rows: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(name, ZIP_HEADER + "".join(rows))
    return buffer.getvalue()


def _client(tmp_path: Path, **config):
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
    ).test_client()


class _FakeSearcher:
    """A plausible online candidate for every track it is asked about."""

    def __init__(self, no_results_titles: frozenset[str] = frozenset()) -> None:
        self._no_results = no_results_titles

    def search(self, track):
        if track.title in self._no_results:
            return ()
        return (
            SourceCandidate(
                url=f"https://example.com/{track.title}",
                title=track.title,
                uploader=f"{track.artists[0]} - Topic",
                artist=track.artists[0] if track.artists else None,
                duration_s=(track.duration_ms / 1000) if track.duration_ms else None,
            ),
        )


def _mock_download(track, source_url, output_dir, **_kwargs) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{track.title}.mp3"
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 512)
    return path


def _mock_audio_valid(track, path):
    from spotm3u.online.audio_validation import AudioValidation

    return AudioValidation(path=Path(path), status="valid")


def _convert(
    client,
    tmp_path: Path,
    monkeypatch,
    name: str,
    *rows: str,
    searcher: _FakeSearcher | None = None,
) -> str:
    """Run one conversion to completion and return its job id."""
    monkeypatch.setattr(
        "spotm3u.web_jobs.OnlineSourceSearcher", lambda **_kw: searcher or _FakeSearcher()
    )
    monkeypatch.setattr("spotm3u.web_jobs.download_track", _mock_download)
    monkeypatch.setattr("spotm3u.resolution.validate_downloaded_audio", _mock_audio_valid)
    uploaded = client.post(
        "/api/upload",
        data={"file": (BytesIO(_export_zip(f"{name}.csv", *rows)), "export.zip")},
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 201
    job_id = uploaded.get_json()["job_id"]
    assert (
        client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": ["0"]}).status_code == 200
    )
    assert client.post(f"/api/jobs/{job_id}/processing").status_code == 202
    job = client.application.config["JOB_MANAGER"].get(job_id, "0")
    assert job is not None
    job.wait(timeout=30)
    return job_id


def _recorded_run(client, **config) -> dict:
    """Put one finished run with a completed and a failed track in the history."""
    downloads = Path(client.application.config["DOWNLOAD_DIR"])
    store = HistoryStore(client.application.config["HISTORY_DB"], **config)
    reference = store.begin_run(
        RunIdentity("job-1", "0", "Road trip", 2, str(downloads)),
        [Track("First", ["A"], album="First album", spotify_id="id-1"), Track("Second", ["B"])],
    )
    store.set_track_stage(reference, 0, status="processing", stage="downloading")
    store.finish_track(
        reference,
        0,
        status="completed",
        stage="downloaded",
        resolution="downloaded",
        source_url="https://example.com/first",
        output_path=str(downloads / "First.mp3"),
    )
    store.finish_track(
        reference,
        1,
        status="failed",
        stage="missing",
        resolution="missing",
        reason="no source matched this track",
    )
    store.finish_run(reference, status="completed", m3u_path=str(downloads / "playlist-0.m3u"))
    store.close()
    return client.application.config["HISTORY"].get_run(reference)


def test_an_untouched_history_is_an_empty_page_not_an_error(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/history")

    assert response.status_code == 200
    body = response.get_json()
    assert body["runs"] == []
    assert body["total"] == 0
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["order"] == "recent"
    # The states come from the backend so the filter cannot drift from the model.
    assert body["statuses"] == [
        "queued",
        "processing",
        "completed",
        "failed",
        "cancelled",
        "skipped",
    ]


def test_a_conversion_appears_in_the_history_when_it_finishes(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)
    _convert(
        client,
        tmp_path,
        monkeypatch,
        "Mixed",
        "spotify:track:a,First Song,,First Artist,200000\n",
        "spotify:track:b,Second Song,,Second Artist,250000\n",
    )

    body = client.get("/api/history").get_json()

    assert body["total"] == 1
    run = body["runs"][0]
    assert run["playlist_name"] == "Mixed"
    assert run["status"] == "completed"
    assert run["total_tracks"] == 2
    assert run["counts"]["completed"] == 2
    assert run["m3u_path"].endswith("playlist-0.m3u")
    assert run["started_at"] and run["finished_at"]

    detail = client.get(f"/api/history/{run['id']}").get_json()
    assert [track["title"] for track in detail["tracks"]] == ["First Song", "Second Song"]
    assert all(track["status"] == "completed" for track in detail["tracks"])
    assert all(track["file_missing"] is False for track in detail["tracks"])
    assert detail["tracks"][0]["output_path"].endswith("First Song.mp3")


def test_a_failed_track_is_recorded_with_its_reason(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)

    _convert(
        client,
        tmp_path,
        monkeypatch,
        "Mixed",
        "spotify:track:a,First Song,,First Artist,200000\n",
        "spotify:track:b,Missing Song,,Nobody,180000\n",
        searcher=_FakeSearcher(no_results_titles=frozenset({"Missing Song"})),
    )

    run = client.get("/api/history").get_json()["runs"][0]
    detail = client.get(f"/api/history/{run['id']}").get_json()
    failed = next(track for track in detail["tracks"] if track["title"] == "Missing Song")

    assert run["counts"]["completed"] == 1
    assert run["counts"]["failed"] == 1
    assert failed["status"] == "failed"
    assert failed["resolution"] == "missing"
    assert failed["reason"]
    assert failed["output_path"] is None
    assert failed["file_missing"] is False


def test_a_deleted_output_is_reported_as_missing_from_disk(tmp_path) -> None:
    client = _client(tmp_path)
    _recorded_run(client)
    output = Path(client.application.config["DOWNLOAD_DIR"]) / "First.mp3"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"audio")

    listed = client.get("/api/history/1").get_json()
    assert listed["tracks"][0]["file_missing"] is False

    output.unlink()

    after = client.get("/api/history/1").get_json()
    assert after["tracks"][0]["file_missing"] is True
    # The stored path is still reported: that is where the file was written.
    assert after["tracks"][0]["output_path"] == str(output)


def test_a_run_reports_its_tracks_in_playlist_order(tmp_path) -> None:
    client = _client(tmp_path)
    run = _recorded_run(client)

    detail = client.get(f"/api/history/{run['id']}").get_json()

    assert [track["title"] for track in detail["tracks"]] == ["First", "Second"]
    assert [track["position"] for track in detail["tracks"]] == [0, 1]
    assert detail["tracks"][0]["spotify_id"] == "id-1"
    assert detail["tracks"][0]["album"] == "First album"
    assert detail["tracks"][0]["source_url"] == "https://example.com/first"
    assert detail["tracks"][1]["reason"] == "no source matched this track"
    assert detail["error"] == ""
    assert [track["retry_count"] for track in detail["tracks"]] == [0, 0]


def test_the_list_can_be_filtered_and_paged(tmp_path) -> None:
    client = _client(tmp_path)
    store = client.application.config["HISTORY"]
    for index in range(3):
        reference = store.begin_run(
            RunIdentity("job-1", str(index), f"Playlist {index}", 1),
            [Track(f"Song {index}", [f"Artist {index}"])],
        )
        store.finish_track(reference, 0, status="completed", resolution="local")
        store.finish_run(reference, status="completed")
    lost = store.begin_run(RunIdentity("job-2", "0", "Broken", 1), [Track("Nope", ["Nobody"])])
    store.finish_track(lost, 0, status="failed", resolution="missing", reason="no source")
    store.finish_run(lost, status="failed")
    unsure = store.begin_run(
        RunIdentity("job-3", "0", "Undecided", 1), [Track("Maybe", ["Someone"])]
    )
    store.finish_track(
        unsure, 0, status=persisted_track_status("ambiguous"), resolution="ambiguous"
    )
    store.finish_run(unsure, status="completed")

    assert client.get("/api/history").get_json()["total"] == 5
    newest_first = [run["playlist_name"] for run in client.get("/api/history").get_json()["runs"]]
    assert newest_first == [
        "Undecided",
        "Broken",
        "Playlist 2",
        "Playlist 1",
        "Playlist 0",
    ]
    assert [
        run["playlist_name"] for run in client.get("/api/history?order=oldest").get_json()["runs"]
    ] == ["Playlist 0", "Playlist 1", "Playlist 2", "Broken", "Undecided"]
    assert [
        run["playlist_name"] for run in client.get("/api/history?order=name").get_json()["runs"]
    ] == ["Broken", "Playlist 0", "Playlist 1", "Playlist 2", "Undecided"]
    assert [
        run["playlist_name"] for run in client.get("/api/history?status=failed").get_json()["runs"]
    ] == ["Broken"]
    # A finished run is also found through a track that did not make it, so the
    # state filter answers "which conversions have something in this state".
    assert [
        run["playlist_name"]
        for run in client.get("/api/history?status=completed").get_json()["runs"]
    ] == ["Undecided", "Playlist 2", "Playlist 1", "Playlist 0"]
    assert [
        run["playlist_name"] for run in client.get("/api/history?status=skipped").get_json()["runs"]
    ] == ["Undecided"]
    assert [
        run["playlist_name"] for run in client.get("/api/history?q=song 1").get_json()["runs"]
    ] == ["Playlist 1"]
    assert [
        run["playlist_name"] for run in client.get("/api/history?q=nobody").get_json()["runs"]
    ] == ["Broken"]

    page = client.get("/api/history?limit=2&offset=1").get_json()
    assert [run["playlist_name"] for run in page["runs"]] == ["Broken", "Playlist 2"]
    assert page["total"] == 5
    assert (page["limit"], page["offset"]) == (2, 1)


def test_an_unknown_filter_is_refused_with_a_readable_message(tmp_path) -> None:
    client = _client(tmp_path)

    for query in ("status=bogus", "order=sideways", "limit=lots", "offset=soon"):
        response = client.get(f"/api/history?{query}")
        assert response.status_code == 400
        assert response.get_json()["code"] == "history_invalid"
        assert response.get_json()["error"]


def test_paging_arguments_are_clamped_rather_than_refused(tmp_path) -> None:
    client = _client(tmp_path)

    body = client.get("/api/history?limit=100000&offset=-3").get_json()

    assert body["limit"] == 500
    assert body["offset"] == 0


def test_an_unknown_run_is_a_not_found(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/history/999")

    assert response.status_code == 404
    assert response.get_json()["code"] == "history_not_found"


def test_the_history_survives_a_restart(tmp_path) -> None:
    first = _client(tmp_path)
    _recorded_run(first)
    # The application is closed here; the download folder is emptied with it.
    for path in Path(first.application.config["DOWNLOAD_DIR"]).glob("*"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    second = _client(tmp_path)

    body = second.get("/api/history").get_json()
    assert body["total"] == 1
    run = body["runs"][0]
    assert run["playlist_name"] == "Road trip"
    detail = second.get(f"/api/history/{run['id']}").get_json()
    assert [track["title"] for track in detail["tracks"]] == ["First", "Second"]
    assert detail["tracks"][0]["file_missing"] is True


def test_a_conversion_interrupted_by_a_close_is_recorded_as_cancelled(tmp_path) -> None:
    client = _client(tmp_path)
    store = client.application.config["HISTORY"]
    reference = store.begin_run(
        RunIdentity("job-1", "0", "Interrupted", 2), [Track("A", ["X"]), Track("B", ["Y"])]
    )
    store.set_track_stage(reference, 0, status="processing", stage="downloading")

    # The app is closed mid-conversion and started again over the same file.
    store.close()
    restarted = _client(tmp_path)
    run = restarted.get(f"/api/history/{reference}").get_json()

    assert run["status"] == "cancelled"
    assert run["error"]
    # Every track the interrupted run had not finished with is recorded as
    # cancelled: nothing is going to come back for it.
    assert [track["status"] for track in run["tracks"]] == ["cancelled", "cancelled"]


def test_a_disabled_history_says_so_instead_of_pretending_it_is_empty(tmp_path) -> None:
    client = _client(tmp_path, HISTORY_ENABLED=False)

    for response in (client.get("/api/history"), client.get("/api/history/1")):
        assert response.status_code == 503
        assert response.get_json()["code"] == "history_unavailable"
        assert response.get_json()["error"]


def test_a_conversion_still_works_when_the_history_is_off(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, HISTORY_ENABLED=False)

    job_id = _convert(
        client,
        tmp_path,
        monkeypatch,
        "Mixed",
        "spotify:track:a,First Song,,First Artist,200000\n",
    )

    job = client.application.config["JOB_MANAGER"].get(job_id, "0")
    assert job.status == "completed"
    assert job.successful == 1
    assert not client.application.config["HISTORY"].available
