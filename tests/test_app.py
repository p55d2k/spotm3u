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
