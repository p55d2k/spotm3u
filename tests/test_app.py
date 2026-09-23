"""Tests for the Flask application scaffold."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from werkzeug.datastructures import MultiDict

from spotm3u import web_jobs
from spotm3u.app import create_app
from spotm3u.media_player import MediaPlayerError, MediaPlayerResult


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
    assert b"Spotify to M3U Converter" in response.data
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


def test_artwork_styles_support_light_and_dark_themes() -> None:
    import pathlib

    css_folder = create_app().static_folder
    css = (pathlib.Path(css_folder) / "style.css").read_text(encoding="utf-8")

    assert ".track-artwork" in css
    assert "--track" in css
    assert ':root[data-theme="dark"]' in css


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


def test_playlist_selection_shows_clickable_playlist_cards(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()

    client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    job_id = _job_directory(tmp_path)

    response = client.get(f"/playlists/{job_id}")

    assert response.status_code == 200
    assert b'class="playlist-card"' in response.data
    assert b"one" in response.data
    assert b"two" in response.data
    assert response.data.count(b'name="playlist_id"') == 2
    assert b"Download selected" in response.data
    assert b"Download all" in response.data
    assert b"Search by playlist name" in response.data


def test_playlist_selection_rejects_playlist_outside_job(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()
    client.post(
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


def test_batch_selection_processes_all_playlists(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    (music / "Artist - Second.mp3").write_bytes(b"audio")
    monkeypatch.setattr(
        "spotm3u.app.cached_artwork_path",
        lambda _download_dir, _track: music / "artwork.jpg",
    )
    monkeypatch.setattr(
        "spotm3u.web_jobs.cached_artwork_path",
        lambda _download_dir, _track: music / "artwork.jpg",
    )
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    upload = client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201
    job_id = _job_directory(tmp_path)

    selection = client.post(
        f"/playlists/{job_id}/batch-select",
        data={"playlist_id": ["0", "1"]},
    )
    assert selection.status_code == 302
    assert selection.headers["Location"] == f"/processing/{job_id}/batch"

    started = client.post(f"/processing/{job_id}/batch/start")
    assert started.status_code == 202
    manager = client.application.config["JOB_MANAGER"]
    manager.get(job_id, "0").wait(timeout=10)
    manager.get(job_id, "1").wait(timeout=10)

    status = client.get(f"/processing/{job_id}/batch/status").get_json()
    assert status["status"] == "completed"
    assert status["total"] == 2
    assert len(status["playlists"]) == 2
    assert all(track["artwork"] for playlist in status["playlists"] for track in playlist["tracks"])
    assert client.get(f"/processing/{job_id}/batch/result").status_code == 200
    assert (music / "SpotM3U-downloads" / "playlist-0.m3u").is_file()
    assert (music / "SpotM3U-downloads" / "playlist-1.m3u").is_file()
    result = client.get(f"/processing/{job_id}/0/result")
    assert result.status_code == 200
    assert b"Local matches" in result.data
    assert f"/processing/{job_id}/batch/result".encode() in result.data


def test_batch_processing_page_renders_full_start_state(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()
    client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    job_id = _job_directory(tmp_path)
    selection = client.post(
        f"/playlists/{job_id}/batch-select",
        data={"playlist_id": ["0", "1"]},
    )

    assert selection.status_code == 302
    response = client.get(f"/processing/{job_id}/batch")

    assert response.status_code == 200
    assert b"Start batch processing" in response.data
    assert b"undefined / undefined" not in response.data


def test_job_id_is_stored_in_session_and_jobs_are_session_scoped(tmp_path) -> None:
    app = create_app({"UPLOAD_ROOT": tmp_path})
    client = app.test_client()
    other_client = app.test_client()

    client.post(
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

    def search_query(self, track, query):
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
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.post(f"/processing/{job_id}/1/start")

    assert response.status_code == 202
    state = response.get_json()
    assert state["job_id"] == job_id
    assert state["playlist"]["id"] == "1"
    assert state["playlist"]["total_tracks"] == 1
    assert state["status"] in {"running", "completed"}
    assert state["output_dir"] == str(tmp_path / "music" / "SpotM3U-downloads")

    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)
    status = client.get(f"/processing/{job_id}/1/status")
    assert status.status_code == 200
    final = status.get_json()
    assert final["status"] == "completed"
    assert final["completed"] == 1
    assert final["failed"] == 1
    assert final["tracks"][0]["status"] == "failed"
    assert final["m3u_path"] == str(tmp_path / "music" / "SpotM3U-downloads" / "playlist.m3u")


def test_processing_job_resolves_local_matches(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)
    final = client.get(f"/processing/{job_id}/1/status").get_json()

    assert final["status"] == "completed"
    assert final["successful"] == 1
    assert final["failed"] == 0
    m3u_path = tmp_path / "music" / "SpotM3U-downloads" / "playlist.m3u"
    assert str(music / "Artist - First.mp3") in m3u_path.read_text(encoding="utf-8")


def test_processing_pages_offer_the_fast_mode_toggle(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    single = client.get(f"/processing/{job_id}/1")

    assert single.status_code == 200
    assert b"Fast mode" in single.data
    assert b'name="fast_mode"' in single.data
    assert b'value="1" checked' not in single.data, "fast mode is off by default"
    # The checkbox must be submitted before its hidden fallback: form fields
    # are read in document order, so a checked box has to come first.
    html = single.data.decode()
    assert html.index('id="fast-mode"') < html.index('name="fast_mode" value="0"')

    client.post(f"/playlists/{job_id}/batch-select", data={"playlist_id": ["0", "1"]})
    batch = client.get(f"/processing/{job_id}/batch")

    assert batch.status_code == 200
    assert b"Fast mode" in batch.data
    assert b'name="fast_mode"' in batch.data


def test_fast_mode_toggle_reflects_the_configuration_default(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path, "FAST_MODE": True}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.get(f"/processing/{job_id}/1")

    assert response.status_code == 200
    assert b'value="1" checked' in response.data


def test_checked_fast_mode_toggle_wins_over_the_hidden_fallback(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    # Exactly what the page submits with the box checked.
    response = client.post(
        f"/processing/{job_id}/1/start",
        data=MultiDict([("fast_mode", "1"), ("fast_mode", "0")]),
    )

    assert response.status_code == 202
    assert response.get_json()["fast_mode"] is True


def test_start_processing_honours_fast_mode(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())

    def forbidden(*_args, **_kwargs):
        raise AssertionError("fast mode must not enrich metadata")

    monkeypatch.setattr("spotm3u.resolution.enrich_metadata", forbidden)
    built: list[bool] = []
    resolver_class = web_jobs.FastTrackResolver

    class SpyFastResolver(resolver_class):
        def __init__(self, *args, **kwargs):
            built.append(True)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(web_jobs, "FastTrackResolver", SpyFastResolver)
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.post(f"/processing/{job_id}/1/start", data={"fast_mode": "1"})

    assert response.status_code == 202
    assert response.get_json()["fast_mode"] is True
    job = client.application.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=10)
    final = client.get(f"/processing/{job_id}/1/status").get_json()
    assert final["fast_mode"] is True
    assert final["successful"] == 1
    assert final["failed"] == 0
    assert built == [True], "the fast resolver must be the one that runs"


def test_start_processing_uses_the_configured_fast_mode_and_form_can_override(
    tmp_path, monkeypatch
) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app(
        {"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music, "FAST_MODE": True}
    ).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    configured = client.post(f"/processing/{job_id}/1/start")
    overridden = client.post(f"/processing/{job_id}/1/start", data={"fast_mode": "0"})

    assert configured.status_code == 202
    assert configured.get_json()["fast_mode"] is True
    assert overridden.status_code == 409, "an existing job keeps the mode it started with"


def test_result_page_offers_a_retry_after_the_download_folder_is_emptied(
    tmp_path, monkeypatch
) -> None:
    music = tmp_path / "music"
    music.mkdir()
    # One local file, as in ``test_processing_job_resolves_local_matches``: the
    # selected playlist resolves against it and the job finishes cleanly.
    local_file = music / "Artist - First.mp3"
    local_file.write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)
    client.post(f"/processing/{job_id}/1/start")
    job = client.application.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=10)

    fresh = client.get(f"/processing/{job_id}/1/result")
    assert b"no longer in the download folder" not in fresh.data
    assert b"unresolved track" not in fresh.data

    local_file.unlink()  # the user deleted everything from SpotM3U-downloads

    stale = client.get(f"/processing/{job_id}/1/result")

    assert stale.status_code == 200
    assert b"no longer in the download folder" in stale.data
    assert b"File missing from disk" in stale.data
    assert b"Retry 1 unresolved track" in stale.data

    retried = client.post(f"/processing/{job_id}/1/retry")

    assert retried.status_code == 302, "retry must actually re-resolve the deleted track"
    assert retried.headers["Location"].endswith(f"/processing/{job_id}/1")


def test_start_processing_refuses_second_start(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
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
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.get(f"/processing/{job_id}/0/status")

    assert response.status_code == 404


def test_processing_page_shows_playlist_details(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.get(f"/processing/{job_id}/1")

    assert response.status_code == 200
    assert b"two" in response.data
    assert b"1 tracks" in response.data
    assert f"/processing/{job_id}/1/start".encode() in response.data
    assert b"Start converting playlist" in response.data
    assert b'action="' + f"/processing/{job_id}/1/start".encode() + b'"' in response.data


def test_download_dir_override_respected(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    download_dir = tmp_path / "custom-downloads"
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app(
        {
            "UPLOAD_ROOT": tmp_path,
            "MUSIC_LIBRARY": music,
            "DOWNLOAD_DIR": str(download_dir),
        }
    ).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.post(f"/processing/{job_id}/1/start")

    assert response.status_code == 202
    assert response.get_json()["output_dir"] == str(download_dir)

    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)
    final = client.get(f"/processing/{job_id}/1/status").get_json()
    assert final["m3u_path"] == str(download_dir / "playlist.m3u")
    assert (download_dir / "playlist.m3u").is_file()


def test_download_m3u_route_returns_playlist(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/playlist.m3u")

    assert response.status_code == 200
    assert b"#EXTM3U" in response.data
    assert "attachment" in response.headers["Content-Disposition"]
    assert "two.m3u" in response.headers["Content-Disposition"]
    assert response.mimetype == "application/octet-stream"


def test_download_m3u_route_warns_when_referenced_files_are_gone(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    audio = music / "Artist - First.mp3"
    audio.write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)
    # The user deletes the downloaded audio by hand, so the written playlist now
    # points at files that are not on disk any more.
    audio.unlink()

    warning = client.get(f"/processing/{job_id}/1/playlist.m3u")

    assert warning.status_code == 409
    page = warning.get_data(as_text=True)
    assert "no longer in the download folder" in page
    assert "Artist - First.mp3" in page
    assert "/playlist.m3u?confirm=1" in page

    confirmed = client.get(f"/processing/{job_id}/1/playlist.m3u?confirm=1")

    assert confirmed.status_code == 200
    assert b"#EXTM3U" in confirmed.data


def test_download_m3u_route_serves_a_playlist_whose_files_are_all_present(
    tmp_path, monkeypatch
) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/playlist.m3u")

    assert response.status_code == 200
    assert b"#EXTM3U" in response.data


def test_download_m3u_route_requires_completed_job(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.get(f"/processing/{job_id}/1/playlist.m3u")

    assert response.status_code == 404


def test_result_page_shows_summary_and_reasons(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/result")

    assert response.status_code == 200
    assert b"Local matches" in response.data
    assert b"Successfully resolved" in response.data
    assert b"Total tracks: 1" in response.data
    assert f"/processing/{job_id}/1/playlist.m3u".encode() in response.data
    assert f"/playlists/{job_id}".encode() in response.data
    assert b"Convert another playlist" in response.data


def test_result_page_offers_retry_for_unresolved_tracks(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})
    client = app.test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    app.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    result = client.get(f"/processing/{job_id}/1/result")

    assert b"Retry 1 unresolved track" in result.data
    assert f"/processing/{job_id}/1/retry".encode() in result.data

    retry = client.post(f"/processing/{job_id}/1/retry")

    assert retry.status_code == 302
    assert retry.headers["Location"] == f"/processing/{job_id}/1"
    job = app.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=10)
    assert job.status == "completed"
    assert (music / "SpotM3U-downloads" / "playlist.m3u").is_file()


class AmbiguousResolver:
    """Resolver that reports every track as ambiguous, without any network work."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def resolve(self, track, *, stage_callback=None):
        from spotm3u.resolution import TrackResolution

        if stage_callback is not None:
            stage_callback("searching")
        return TrackResolution(track, "ambiguous", reasons=("multiple candidates",))


def test_result_page_filters_failed_and_ambiguous_tracks(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.TrackResolver", AmbiguousResolver)
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})
    client = app.test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    app.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/result")

    assert b'data-track-filter="all"' in response.data
    assert b'data-track-filter="failed"' in response.data
    assert b"Failed or ambiguous" in response.data
    assert b'id="track-list"' in response.data
    assert b'id="no-tracks-match"' in response.data
    # One ambiguous track is listed, and its count matches the summary stat.
    assert b'<span class="filter-count">1</span>' in response.data
    assert b'class="track-ambiguous"' in response.data
    assert b"<dt>Ambiguous</dt><dd>1</dd>" in response.data


def test_result_page_filter_ignores_tracks_that_only_failed_to_match(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})
    client = app.test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    app.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/result")

    # A missing track is retryable, but it is not a failed or ambiguous result.
    assert b"Retry 1 unresolved track" in response.data
    assert b'class="track-filters"' not in response.data
    assert b"<dt>Missing</dt><dd>1</dd>" in response.data


def test_result_page_hides_filter_and_retry_when_every_track_resolved(
    tmp_path, monkeypatch
) -> None:
    music = tmp_path / "music"
    music.mkdir()
    # Playlist 1 is "two", whose only track is "Second" by "Artist".
    (music / "Artist - Second.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})
    client = app.test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    job = app.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/result")

    assert job.status == "completed"
    assert b"Retry" not in response.data
    assert b'class="track-filters"' not in response.data
    assert b'class="track-complete"' in response.data


def test_retry_route_rejects_unknown_job(tmp_path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.post(f"/processing/{job_id}/1/retry")

    assert response.status_code == 404


def test_result_page_redirects_while_job_running(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})
    client = app.test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    class BlockingResolver:
        def resolve(self, track, *, stage_callback=None):
            import threading

            threading.Event().wait(timeout=30)
            raise RuntimeError("unreachable")

    from spotm3u.jobs import ProcessingJob
    from spotm3u.models import Track

    playlist = app.config["JOB_MANAGER"]
    job = ProcessingJob(
        job_id=job_id,
        playlist_id="1",
        playlist_name="two",
        tracks=[Track("First", ["Artist"], duration_ms=200_000)],
        output_dir=music / "SpotM3U-downloads",
        resolver_factory=lambda: BlockingResolver(),
    )
    playlist.submit(job)
    job.start()

    response = client.get(f"/processing/{job_id}/1/result")

    assert response.status_code == 302
    assert response.headers["Location"] == f"/processing/{job_id}/1"
    job.wait(timeout=1)


def test_result_page_exposes_rejected_reasons(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()

    class RejectingCandidates:
        def search(self, track):
            return ()

    monkeypatch.setattr(
        "spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: RejectingCandidates()
    )
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/result")

    assert response.status_code == 200
    assert b"no online source candidates" in response.data


def _run_local_match_job(tmp_path, monkeypatch):
    """Start playlist 1 (second.csv, track "Second") with a matching local file."""
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - Second.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    job = client.application.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=10)
    return client, job, music / "SpotM3U-downloads"


def test_artwork_route_returns_cached_image(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork, artwork_cache

    client, job, download_dir = _run_local_match_job(tmp_path, monkeypatch)
    track = job.tracks[0]
    artwork_path = artwork_cache._cached_artwork_path(
        artwork_cache._cache_dir(download_dir),
        artwork_cache._cache_key(
            artwork.artwork_artist(track) or "", track.album or "", track.title
        ),
    )
    artwork_path.write_bytes(b"cached-artwork-bytes")

    response = client.get(f"/processing/{job.job_id}/1/artwork/0")

    assert response.status_code == 200
    assert response.data == b"cached-artwork-bytes"
    assert response.headers["Content-Type"] == "image/jpeg"


def test_artwork_route_404_when_unavailable(tmp_path, monkeypatch) -> None:
    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)

    response = client.get(f"/processing/{job.job_id}/1/artwork/0")

    assert response.status_code == 404


def test_artwork_route_404_for_unknown_index(tmp_path, monkeypatch) -> None:
    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)

    response = client.get(f"/processing/{job.job_id}/1/artwork/5")

    assert response.status_code == 404


def test_result_page_shows_artwork_image_when_available(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork, artwork_cache

    client, job, download_dir = _run_local_match_job(tmp_path, monkeypatch)
    track = job.tracks[0]
    artwork_path = artwork_cache._cached_artwork_path(
        artwork_cache._cache_dir(download_dir),
        artwork_cache._cache_key(
            artwork.artwork_artist(track) or "", track.album or "", track.title
        ),
    )
    artwork_path.write_bytes(b"cached-artwork-bytes")

    response = client.get(f"/processing/{job.job_id}/1/result")

    assert response.status_code == 200
    assert b'class="track-art-img"' in response.data
    assert f"/processing/{job.job_id}/1/artwork/0".encode() in response.data


def test_artwork_is_hidden_for_a_download_that_was_deleted(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork, artwork_cache

    client, job, download_dir = _run_local_match_job(tmp_path, monkeypatch)
    track = job.tracks[0]
    artwork_path = artwork_cache._cached_artwork_path(
        artwork_cache._cache_dir(download_dir),
        artwork_cache._cache_key(
            artwork.artwork_artist(track) or "", track.album or "", track.title
        ),
    )
    artwork_path.write_bytes(b"cached-artwork-bytes")

    resolved_path = Path(job.as_dict()["tracks"][0]["local_path"])
    resolved_path.unlink()  # the user deleted the download by hand

    result = client.get(f"/processing/{job.job_id}/1/result")

    assert result.status_code == 200
    assert b'class="track-art-img"' not in result.data
    assert f"/processing/{job.job_id}/1/artwork/0".encode() not in result.data

    # The image itself is not served either, so no surface claims the download
    # is still there. Hiding is not deleting: the retry reclaims the cache.
    assert client.get(f"/processing/{job.job_id}/1/artwork/0").status_code == 404
    assert artwork_path.is_file()


def test_status_reports_no_artwork_for_a_deleted_download(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork, artwork_cache

    client, job, download_dir = _run_local_match_job(tmp_path, monkeypatch)
    track = job.tracks[0]
    artwork_cache._cached_artwork_path(
        artwork_cache._cache_dir(download_dir),
        artwork_cache._cache_key(
            artwork.artwork_artist(track) or "", track.album or "", track.title
        ),
    ).write_bytes(b"cached-artwork-bytes")

    assert (
        client.get(f"/processing/{job.job_id}/1/status").get_json()["tracks"][0]["artwork"] is True
    )

    Path(job.as_dict()["tracks"][0]["local_path"]).unlink()

    state = client.get(f"/processing/{job.job_id}/1/status").get_json()

    assert state["tracks"][0]["file_missing"] is True
    assert state["tracks"][0]["artwork"] is False


def test_result_page_shows_placeholder_without_artwork(tmp_path, monkeypatch) -> None:
    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)

    response = client.get(f"/processing/{job.job_id}/1/result")

    assert response.status_code == 200
    assert b'class="track-art-img"' not in response.data
    assert b'class="track-artwork"' in response.data


def test_result_page_offers_add_to_media_player(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.app.media_player_available", lambda: True)
    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)

    response = client.get(f"/processing/{job.job_id}/1/result")

    assert response.status_code == 200
    assert b"Add to Media Player" in response.data
    assert b"Add to Apple Music" not in response.data
    assert f"/processing/{job.job_id}/1/media-player".encode() in response.data


def test_result_page_hides_add_to_media_player_without_platform_support(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("spotm3u.app.media_player_available", lambda: False)
    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)

    response = client.get(f"/processing/{job.job_id}/1/result")

    assert b"Add to Media Player" not in response.data
    # The manual playlist export is never gated on media-player integration.
    assert f"/processing/{job.job_id}/1/playlist.m3u".encode() in response.data


def test_result_page_hides_add_to_media_player_without_resolved_tracks(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("spotm3u.app.media_player_available", lambda: True)
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)
    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/result")

    assert response.status_code == 200
    assert b"Add to Media Player" not in response.data


def test_add_to_media_player_reuses_the_generated_playlist(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.app.media_player_available", lambda: True)
    client, job, download_dir = _run_local_match_job(tmp_path, monkeypatch)
    captured = {}

    def fake_add(playlist_name, playlist_path, paths):
        captured["playlist_name"] = playlist_name
        captured["playlist_path"] = playlist_path
        captured["paths"] = list(paths)
        return MediaPlayerResult(
            action="created",
            imported=1,
            message="Added to Media Player. 1 track(s) were imported into Apple Music.",
        )

    monkeypatch.setattr("spotm3u.app.add_to_media_player", fake_add)
    monkeypatch.setattr("spotm3u.web_jobs.add_to_media_player", fake_add)

    response = client.post(f"/processing/{job.job_id}/1/media-player")

    assert response.status_code == 200
    assert b"Added to Media Player. 1 track(s) were imported into Apple Music." in response.data
    assert captured["playlist_name"] == job.playlist_name
    assert Path(captured["playlist_path"]) == download_dir / "playlist.m3u"
    assert Path(captured["playlist_path"]).is_file()
    assert [Path(path).name for path in captured["paths"]] == ["Artist - Second.mp3"]
    # The imported playlist stays available for the normal M3U download.
    assert client.get(f"/processing/{job.job_id}/1/playlist.m3u").status_code == 200


def test_add_to_media_player_keeps_artwork_on_result_page(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.app.media_player_available", lambda: True)
    from spotm3u import artwork, artwork_cache

    client, job, download_dir = _run_local_match_job(tmp_path, monkeypatch)
    track = job.tracks[0]
    artwork_path = artwork_cache._cached_artwork_path(
        artwork_cache._cache_dir(download_dir),
        artwork_cache._cache_key(
            artwork.artwork_artist(track) or "", track.album or "", track.title
        ),
    )
    artwork_path.write_bytes(b"cached-artwork-bytes")
    captured = {}

    def fake_add(playlist_name, playlist_path, paths):
        captured["paths"] = list(paths)
        return MediaPlayerResult(
            action="created",
            imported=1,
            message="Added to Media Player. 1 track(s) were imported into Apple Music.",
        )

    monkeypatch.setattr("spotm3u.app.add_to_media_player", fake_add)
    monkeypatch.setattr("spotm3u.web_jobs.add_to_media_player", fake_add)

    response = client.post(f"/processing/{job.job_id}/1/media-player")

    assert response.status_code == 200
    assert b'class="track-art-img"' in response.data
    assert f"/processing/{job.job_id}/1/artwork/0".encode() in response.data
    assert artwork_path.is_file()


def test_add_to_media_player_reports_failures(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.app.media_player_available", lambda: True)
    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)

    def failing_add(*_args, **_kwargs):
        raise MediaPlayerError("Apple Music import failed: boom")

    monkeypatch.setattr("spotm3u.app.add_to_media_player", failing_add)
    monkeypatch.setattr("spotm3u.web_jobs.add_to_media_player", failing_add)

    response = client.post(f"/processing/{job.job_id}/1/media-player")

    assert response.status_code == 502
    assert b"Add to Media Player failed: Apple Music import failed: boom" in response.data


def test_add_to_media_player_is_unavailable_without_platform_support(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.app.media_player_available", lambda: False)
    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)

    response = client.post(f"/processing/{job.job_id}/1/media-player")

    assert response.status_code == 404
    assert "only available on macOS and Windows" in response.get_json()["error"]


def _two_playlist_zip() -> bytes:
    """A ZIP whose two playlists have clearly distinct tracks.

    ``export_zip``'s synthetic titles fuzzy-match each other's library files,
    which makes a two-playlist batch ambiguous; these names resolve cleanly so
    the batch tests can assert on real successful playlists.
    """
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("alpha.csv", "Track Name,Artist Name(s)\nAlpha Rhapsody,Artist A\n")
        archive.writestr("zulu.csv", "Track Name,Artist Name(s)\nZulu Ballad,Artist B\n")
    return output.getvalue()


def _run_batch_job(tmp_path, monkeypatch, *, local: tuple[str, ...] = ()):
    """Process playlists 0 and 1 as a batch against the given library files."""
    music = tmp_path / "music"
    music.mkdir()
    for name in local or ("Artist A - Alpha Rhapsody.mp3", "Artist B - Zulu Ballad.mp3"):
        (music / name).write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    upload = client.post(
        "/upload",
        data={"file": (BytesIO(_two_playlist_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201
    job_id = _job_directory(tmp_path)
    client.post(f"/playlists/{job_id}/batch-select", data={"playlist_id": ["0", "1"]})
    client.post(f"/processing/{job_id}/batch/start")
    manager = client.application.config["JOB_MANAGER"]
    manager.get(job_id, "0").wait(timeout=10)
    manager.get(job_id, "1").wait(timeout=10)
    return client, job_id, manager, music


def test_batch_result_offers_add_all_to_apple_music(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.app.media_player_available", lambda: True)
    monkeypatch.setattr("spotm3u.app.library_import_available", lambda: True)
    client, job_id, _manager, _music = _run_batch_job(tmp_path, monkeypatch)

    response = client.get(f"/processing/{job_id}/batch/result")

    assert response.status_code == 200
    assert b"Add all 2 playlists to Apple Music" in response.data
    assert f"/processing/{job_id}/batch/media-player".encode() in response.data
    # The per-playlist action stays available next to the batch one.
    assert b"Add to Media Player" in response.data


def test_batch_result_hides_add_all_without_a_media_library(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.app.media_player_available", lambda: True)
    monkeypatch.setattr("spotm3u.app.library_import_available", lambda: False)
    client, job_id, _manager, _music = _run_batch_job(tmp_path, monkeypatch)

    response = client.get(f"/processing/{job_id}/batch/result")

    assert response.status_code == 200
    assert b"Add all" not in response.data
    assert b"Add to Media Player" in response.data


def test_add_all_imports_every_playlist_as_its_own(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.app.media_player_available", lambda: True)
    monkeypatch.setattr("spotm3u.app.library_import_available", lambda: True)
    client, job_id, _manager, music = _run_batch_job(tmp_path, monkeypatch)
    calls = []

    def fake_add(playlist_name, playlist_path, paths):
        calls.append((playlist_name, Path(playlist_path), [Path(path) for path in paths]))
        return MediaPlayerResult(
            action="created",
            imported=1,
            message=f'Added "{playlist_name}" to Apple Music.',
        )

    monkeypatch.setattr("spotm3u.app.add_to_media_player", fake_add)
    monkeypatch.setattr("spotm3u.web_jobs.add_to_media_player", fake_add)

    response = client.post(f"/processing/{job_id}/batch/media-player")

    assert response.status_code == 200
    assert [name for name, _path, _paths in calls] == ["alpha", "zulu"]
    assert [path.name for _name, path, _paths in calls] == ["playlist-0.m3u", "playlist-1.m3u"]
    assert all(path.is_file() for _name, path, _paths in calls)
    assert [paths[0].name for _name, _path, paths in calls] == [
        "Artist A - Alpha Rhapsody.mp3",
        "Artist B - Zulu Ballad.mp3",
    ]
    assert b"Added 2 of 2 playlist(s) to Apple Music." in response.data
    assert b"2 track(s) were imported." in response.data


def test_add_all_reports_a_failed_playlist_without_stopping(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.app.media_player_available", lambda: True)
    monkeypatch.setattr("spotm3u.app.library_import_available", lambda: True)
    client, job_id, _manager, _music = _run_batch_job(tmp_path, monkeypatch)
    imported = []

    def fake_add(playlist_name, _playlist_path, _paths):
        if playlist_name == "alpha":
            raise MediaPlayerError("Apple Music import failed: boom")
        imported.append(playlist_name)
        return MediaPlayerResult(action="created", imported=1, message="ok")

    monkeypatch.setattr("spotm3u.app.add_to_media_player", fake_add)
    monkeypatch.setattr("spotm3u.web_jobs.add_to_media_player", fake_add)

    response = client.post(f"/processing/{job_id}/batch/media-player")

    assert response.status_code == 200
    assert imported == ["zulu"], "a failing playlist must not stop the others"
    assert b"Apple Music import failed: boom" in response.data
    assert b"Added 1 of 2 playlist(s) to Apple Music." in response.data
    assert b"1 playlist(s) could not be added at all." in response.data


def test_add_all_skips_playlists_without_resolved_tracks(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.app.media_player_available", lambda: True)
    monkeypatch.setattr("spotm3u.app.library_import_available", lambda: True)
    # Only the second playlist has a matching file, so the first has nothing to import.
    client, job_id, _manager, _music = _run_batch_job(
        tmp_path, monkeypatch, local=("Artist B - Zulu Ballad.mp3",)
    )

    def fake_add(playlist_name, _playlist_path, _paths):
        return MediaPlayerResult(action="created", imported=1, message=f"ok {playlist_name}")

    monkeypatch.setattr("spotm3u.app.add_to_media_player", fake_add)
    monkeypatch.setattr("spotm3u.web_jobs.add_to_media_player", fake_add)

    response = client.post(f"/processing/{job_id}/batch/media-player")

    assert response.status_code == 200
    assert b"There were no resolved tracks to add." in response.data
    assert b"Added 1 of 2 playlist(s) to Apple Music." in response.data


def test_add_all_is_unavailable_without_a_media_library(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.app.library_import_available", lambda: False)
    client, job_id, _manager, _music = _run_batch_job(tmp_path, monkeypatch)

    response = client.post(f"/processing/{job_id}/batch/media-player")

    assert response.status_code == 404
    assert "Apple Music" in response.get_json()["error"]


def test_add_all_requires_finished_playlists(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.app.library_import_available", lambda: True)
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()
    client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    job_id = _job_directory(tmp_path)
    client.post(f"/playlists/{job_id}/batch-select", data={"playlist_id": ["0", "1"]})

    response = client.post(f"/processing/{job_id}/batch/media-player")

    assert response.status_code == 409
    assert "finish processing" in response.get_json()["error"]
