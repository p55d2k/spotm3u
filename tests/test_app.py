"""Tests for the Flask application scaffold."""

from io import BytesIO
from zipfile import ZipFile

from spotm3u.app import create_app


def export_zip() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "one.csv",
            "Track Name,Artist Name(s)\nFirst,Artist\n",
        )
        archive.writestr(
            "two.csv",
            "Track Name,Artist Name(s)\nSecond,Artist\n",
        )
    return output.getvalue()


def test_homepage_renders() -> None:
    client = create_app().test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"Spotify to M3U" in response.data
    assert b"Exportify" in response.data
    assert b"Export All" in response.data
    assert b"Download the playlist export ZIP" in response.data
    assert b'action="/upload"' in response.data
    assert b'accept=".zip,application/zip"' in response.data


def test_static_stylesheet_is_available() -> None:
    client = create_app().test_client()

    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert b"font-family" in response.data


def test_playlist_selection_persists_state_and_redirects(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()

    upload = client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201
    job_id = next(tmp_path.iterdir()).name.removeprefix("job-")
    selection = client.post(
        f"/playlists/{job_id}/select",
        data={"playlist_id": "1"},
    )

    assert selection.status_code == 302
    assert selection.headers["Location"] == f"/processing/{job_id}/1"
    assert (tmp_path / f"job-{job_id}" / "state.json").read_text() == (
        '{"selected_playlist_id": "1"}'
    )
    assert client.get(selection.headers["Location"]).status_code == 200


def test_playlist_selection_rejects_playlist_outside_job(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()
    upload = client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    job_id = next(tmp_path.iterdir()).name.removeprefix("job-")

    selection = client.post(
        f"/playlists/{job_id}/select",
        data={"playlist_id": "2"},
    )

    assert selection.status_code == 400
    assert b"not available for this upload" in selection.data


def test_job_id_is_stored_in_session_and_jobs_are_session_scoped(tmp_path) -> None:
    app = create_app({"UPLOAD_ROOT": tmp_path})
    client = app.test_client()
    other_client = app.test_client()

    upload = client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    job_id = next(tmp_path.iterdir()).name.removeprefix("job-")

    with client.session_transaction() as current_session:
        assert dict(current_session) == {"job_id": job_id}

    assert client.get(f"/playlists/{job_id}").status_code == 200
    assert other_client.get(f"/playlists/{job_id}").status_code == 404


class NoCandidates:
    def search(self, track):
        return ()


def _job_directory(tmp_path) -> str:
    return next(
        path for path in tmp_path.iterdir() if path.name.startswith("job-")
    ).name.removeprefix("job-")


def _upload_and_select(tmp_path, client, playlist_id: str = "1") -> tuple[str, object]:
    upload = client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201
    job_id = _job_directory(tmp_path)
    selection = client.post(
        f"/playlists/{job_id}/select",
        data={"playlist_id": playlist_id},
    )
    assert selection.status_code == 302
    return job_id, selection


def test_start_processing_runs_job_and_exposes_state(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.app.OnlineSourceSearcher", lambda: NoCandidates())
    client = create_app(
        {"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}
    ).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.post(f"/processing/{job_id}/1/start")

    assert response.status_code == 202
    state = response.get_json()
    assert state["job_id"] == job_id
    assert state["playlist"]["id"] == "1"
    assert state["playlist"]["total_tracks"] == 1
    assert state["status"] in {"running", "completed"}
    assert state["output_dir"] == str(tmp_path / f"job-{job_id}" / "output")

    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)
    status = client.get(f"/processing/{job_id}/1/status")
    assert status.status_code == 200
    final = status.get_json()
    assert final["status"] == "completed"
    assert final["completed"] == 1
    assert final["failed"] == 1
    assert final["tracks"][0]["status"] == "failed"
    assert final["m3u_path"] == str(tmp_path / f"job-{job_id}" / "output" / "playlist.m3u")


def test_processing_job_resolves_local_matches(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.app.OnlineSourceSearcher", lambda: NoCandidates())
    client = create_app(
        {"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}
    ).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)
    final = client.get(f"/processing/{job_id}/1/status").get_json()

    assert final["status"] == "completed"
    assert final["successful"] == 1
    assert final["failed"] == 0
    m3u_path = tmp_path / f"job-{job_id}" / "output" / "playlist.m3u"
    assert str(music / "Artist - First.mp3") in m3u_path.read_text(encoding="utf-8")


def test_start_processing_refuses_second_start(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.app.OnlineSourceSearcher", lambda: NoCandidates())
    client = create_app(
        {"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}
    ).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    first = client.post(f"/processing/{job_id}/1/start")
    job = client.application.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=5)
    second = client.post(f"/processing/{job_id}/1/start")

    assert first.status_code == 202
    assert second.status_code == 409


def test_status_endpoint_requires_matching_playlist(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.app.OnlineSourceSearcher", lambda: NoCandidates())
    client = create_app(
        {"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}
    ).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.get(f"/processing/{job_id}/0/status")

    assert response.status_code == 404


def test_processing_page_shows_playlist_details(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.get(f"/processing/{job_id}/1")

    assert response.status_code == 200
    assert b"two" in response.data
    assert b"Total tracks: 1" in response.data
    assert f"/processing/{job_id}/1/start".encode() in response.data
    assert b"Start processing" in response.data
    assert b'action="' + f"/processing/{job_id}/1/start".encode() + b'"' in response.data
