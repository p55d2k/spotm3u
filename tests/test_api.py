"""Tests for the JSON API the React frontend uses (``/api/...``)."""

import threading
from io import BytesIO
from pathlib import Path

from conftest import NoCandidates, export_zip

from spotm3u.app import create_app
from spotm3u.media_player import MediaPlayerError, MediaPlayerResult
from spotm3u.resolution import TrackResolution


class _StubWindowControls:
    """Stands in for the desktop bridge's picked-file handoff."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def take_pending_import(self) -> Path | None:
        path, self.path = self.path, None
        return path


def _client(tmp_path, music: Path | None = None, **config):
    """An API client whose uploads and downloads stay inside ``tmp_path``."""
    music = music or tmp_path / "music"
    music.mkdir(exist_ok=True)
    return create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music, **config}).test_client()


def _upload(tmp_path, client) -> str:
    response = client.post(
        "/api/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    return response.get_json()["job_id"]


def _finished(tmp_path, monkeypatch, playlist_ids=("1",), *, local_match=True):
    """Convert playlists through the API and wait for the jobs to settle."""
    music = tmp_path / "music"
    music.mkdir(exist_ok=True)
    if local_match:
        (music / "Artist - Second.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = _client(tmp_path, music)
    job_id = _upload(tmp_path, client)
    client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": list(playlist_ids)})
    started = client.post(f"/api/jobs/{job_id}/processing")
    assert started.status_code == 202
    manager = client.application.config["JOB_MANAGER"]
    for playlist_id in playlist_ids:
        manager.get(job_id, playlist_id).wait(timeout=10)
    return client, job_id, music


def test_meta_reports_capabilities_defaults_and_limits(tmp_path) -> None:
    client = _client(tmp_path, MAX_CONTENT_LENGTH=1234)

    payload = client.get("/api/meta").get_json()

    assert payload["version"]
    assert isinstance(payload["media_player_available"], bool)
    assert payload["defaults"]["fast_mode"] is False
    assert payload["limits"]["max_upload_size"] == 1234
    # The UI says "up to your configured size limit", so the limit has to be
    # readable without uploading anything.
    assert payload["limits"]["max_decompressed_size"] > 0


def test_meta_reports_the_configured_fast_mode_default(tmp_path) -> None:
    client = _client(tmp_path, FAST_MODE=True)

    assert client.get("/api/meta").get_json()["defaults"]["fast_mode"] is True


def test_upload_imports_the_archive_and_lists_its_playlists(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["job_id"]
    assert [playlist["id"] for playlist in payload["playlists"]] == ["0", "1"]
    assert payload["playlists"][1]["track_count"] == 1
    assert payload["selected_playlist_ids"] == []
    assert payload["download_dir"] == str(tmp_path / "music" / "SpotM3U")
    # The upload opened the session its job routes are scoped to.
    assert client.get(f"/api/jobs/{payload['job_id']}").status_code == 200


def test_upload_requires_a_file(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.post("/api/upload", data={}, content_type="multipart/form-data")

    assert response.status_code == 400
    assert response.get_json()["code"] == "upload_missing_file"


def test_upload_rejects_an_archive_it_cannot_read(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/upload",
        data={"file": (BytesIO(b"not a zip"), "export.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "upload_invalid"


def test_upload_answers_json_when_the_file_is_too_large(tmp_path) -> None:
    client = _client(tmp_path, MAX_CONTENT_LENGTH=16)

    response = client.post(
        "/api/upload",
        data={"file": (BytesIO(b"x" * 1024), "export.zip")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert response.get_json()["code"] == "upload_too_large"


def test_upload_picked_imports_the_archive_chosen_in_the_native_dialog(tmp_path) -> None:
    archive = tmp_path / "export.zip"
    archive.write_bytes(export_zip())
    music = tmp_path / "music"
    music.mkdir()
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})
    app.config["WINDOW_CONTROLS"] = _StubWindowControls(archive)
    client = app.test_client()

    response = client.post("/api/upload/picked")

    assert response.status_code == 201
    payload = response.get_json()
    assert len(payload["playlists"]) == 2
    # The handoff is single-use: the path cannot be imported twice.
    assert client.post("/api/upload/picked").status_code == 400


def test_upload_picked_needs_a_file_from_the_native_dialog(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.post("/api/upload/picked")

    assert response.status_code == 400
    assert response.get_json()["code"] == "upload_missing_file"


def test_job_detail_lists_the_export_and_its_selection(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)
    client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": ["1"]})

    payload = client.get(f"/api/jobs/{job_id}").get_json()

    assert payload["job_id"] == job_id
    assert [playlist["name"] for playlist in payload["playlists"]] == ["one", "two"]
    assert payload["selected_playlist_ids"] == ["1"]


def test_job_detail_is_refused_for_an_upload_that_is_not_in_the_session(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    # A second client has no session for that upload, so the job stays private
    # to the session that imported it.
    other = _client(tmp_path)

    response = other.get(f"/api/jobs/{job_id}")

    assert response.status_code == 404
    assert response.get_json()["code"] == "job_expired"


def test_job_detail_reports_an_upload_whose_directory_is_gone(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)
    (tmp_path / f"job-{job_id}").rename(tmp_path / "moved")

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 404
    assert response.get_json()["code"] == "job_expired"


def test_playlist_detail_returns_the_tracks_in_order(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    payload = client.get(f"/api/jobs/{job_id}/playlists/1").get_json()

    assert payload["id"] == "1"
    assert payload["name"] == "two"
    assert payload["track_count"] == 1
    assert payload["tracks"] == [
        {
            "index": 0,
            "title": "Second",
            "artists": ["Artist"],
            "album": None,
            "duration_ms": None,
            "album_artist": None,
            "track_number": None,
            "disc_number": None,
            "release_year": None,
            "genre": None,
            "comments": None,
            "spotify_id": None,
            "spotify_url": None,
        }
    ]


def test_playlist_detail_rejects_a_playlist_the_export_does_not_have(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    response = client.get(f"/api/jobs/{job_id}/playlists/9")

    assert response.status_code == 404
    assert response.get_json()["code"] == "playlist_unavailable"


def test_selection_is_stored_and_read_back(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    saved = client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": ["0", "1"]})

    assert saved.status_code == 200
    assert saved.get_json()["playlist_ids"] == ["0", "1"]
    assert client.get(f"/api/jobs/{job_id}").get_json()["selected_playlist_ids"] == ["0", "1"]


def test_selection_keeps_a_single_playlist_readable_for_the_jinja_pages(tmp_path) -> None:
    """Both frontends share one job state, so a selection made here is usable there."""
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": ["1"]})

    assert client.get(f"/processing/{job_id}/1").status_code == 200


def test_selection_rejects_an_unknown_playlist(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    response = client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": ["1", "9"]})

    assert response.status_code == 400
    assert response.get_json()["code"] == "playlist_unavailable"


def test_selection_requires_at_least_one_playlist(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    response = client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": []})

    assert response.status_code == 400
    assert response.get_json()["code"] == "selection_required"


def test_starting_processing_requires_a_selection(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    response = client.post(f"/api/jobs/{job_id}/processing", json={})

    assert response.status_code == 400
    assert response.get_json()["code"] == "selection_required"


def test_starting_processing_rejects_an_unknown_playlist(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    response = client.post(f"/api/jobs/{job_id}/processing", json={"playlist_ids": ["7"]})

    assert response.status_code == 400
    assert response.get_json()["code"] == "playlist_unavailable"


def test_starting_processing_converts_the_stored_selection(tmp_path, monkeypatch) -> None:
    client, job_id, music = _finished(tmp_path, monkeypatch)

    progress = client.get(f"/api/jobs/{job_id}/playlists/1/processing").get_json()

    assert progress["status"] == "completed"
    assert progress["successful"] == 1
    # One playlist per file, so a batch can never overwrite another playlist.
    assert progress["m3u_path"] == str(music / "SpotM3U" / "playlist-1.m3u")
    assert Path(progress["m3u_path"]).is_file()


def test_starting_processing_accepts_playlists_named_in_the_body(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    response = client.post(f"/api/jobs/{job_id}/processing", json={"playlist_ids": ["0"]})

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["job_id"] == job_id
    assert [state["playlist"]["id"] for state in payload["playlists"]] == ["0"]
    # Starting also stores the selection, so the status routes find it.
    assert client.get(f"/api/jobs/{job_id}").get_json()["selected_playlist_ids"] == ["0"]
    client.application.config["JOB_MANAGER"].get(job_id, "0").wait(timeout=10)


def test_starting_processing_honours_the_fast_mode_choice(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    chosen = client.post(
        f"/api/jobs/{job_id}/processing", json={"playlist_ids": ["1"], "fast_mode": True}
    )
    configured = _client(tmp_path, FAST_MODE=True)
    configured_job = _upload(tmp_path, configured)
    default = configured.post(
        f"/api/jobs/{configured_job}/processing", json={"playlist_ids": ["1"]}
    )

    assert chosen.get_json()["playlists"][0]["fast_mode"] is True
    assert default.get_json()["playlists"][0]["fast_mode"] is True
    client.application.config["JOB_MANAGER"].get(job_id, "1").wait(timeout=10)
    configured.application.config["JOB_MANAGER"].get(configured_job, "1").wait(timeout=10)


def test_batch_start_converts_every_selected_playlist(tmp_path, monkeypatch) -> None:
    client, job_id, _music = _finished(tmp_path, monkeypatch, playlist_ids=("0", "1"))

    payload = client.get(f"/api/jobs/{job_id}/result").get_json()

    assert payload["total"] == 2
    assert [state["playlist"]["id"] for state in payload["playlists"]] == ["0", "1"]
    assert payload["status"] == "completed"


def test_processing_status_reports_the_selected_playlists(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)
    client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": ["1"]})
    client.post(f"/api/jobs/{job_id}/processing")

    payload = client.get(f"/api/jobs/{job_id}/processing").get_json()
    scoped = client.get(f"/api/jobs/{job_id}/processing?playlist_ids=1").get_json()
    other = client.get(f"/api/jobs/{job_id}/processing?playlist_ids=0")

    assert payload["job_id"] == job_id
    assert [state["playlist"]["id"] for state in payload["playlists"]] == ["1"]
    assert scoped["playlists"][0]["playlist"]["id"] == "1"
    # Nothing was started for playlist 0, which the frontend reads as "not yet".
    assert other.status_code == 404
    assert other.get_json()["code"] == "job_not_found"
    client.application.config["JOB_MANAGER"].get(job_id, "1").wait(timeout=10)


def test_processing_status_without_a_selection_is_reported(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    response = client.get(f"/api/jobs/{job_id}/processing")

    assert response.status_code == 404
    assert response.get_json()["code"] == "selection_expired"


def test_playlist_progress_needs_the_playlist_to_be_selected(tmp_path, monkeypatch) -> None:
    client, job_id, _music = _finished(tmp_path, monkeypatch)

    response = client.get(f"/api/jobs/{job_id}/playlists/0/processing")

    assert response.status_code == 404
    assert response.get_json()["code"] == "selection_expired"


def test_playlist_progress_without_a_job_is_reported(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)
    client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": ["1"]})

    response = client.get(f"/api/jobs/{job_id}/playlists/1/processing")

    assert response.status_code == 404
    assert response.get_json()["code"] == "job_not_found"


def test_result_reports_the_outcome_and_where_to_download_it(tmp_path, monkeypatch) -> None:
    client, job_id, _music = _finished(tmp_path, monkeypatch)

    payload = client.get(f"/api/jobs/{job_id}/playlists/1/result").get_json()

    assert payload["status"] == "completed"
    assert payload["counts"]["successful"] == 1
    assert payload["counts"]["total"] == 1
    assert payload["tracks"][0]["status"] == "complete"
    # Lyrics are read from the file, which a local match never wrote.
    assert payload["tracks"][0]["lyrics"] is None
    assert payload["m3u_url"] == f"/api/jobs/{job_id}/playlists/1/m3u"
    assert isinstance(payload["media_player_available"], bool)
    assert isinstance(payload["library_import_available"], bool)


def test_result_waits_until_every_playlist_finished(tmp_path, monkeypatch) -> None:
    release = threading.Event()
    started = threading.Event()

    class BlockingResolver:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def resolve(self, track, stage_callback=None):
            started.set()
            release.wait(timeout=10)
            return TrackResolution(track=track, status="failed", reason="blocked")

    monkeypatch.setattr("spotm3u.web_jobs.TrackResolver", BlockingResolver)
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = _client(tmp_path, RESOLVE_WORKERS=1)
    job_id = _upload(tmp_path, client)
    client.post(f"/api/jobs/{job_id}/processing", json={"playlist_ids": ["1"]})
    assert started.wait(timeout=10)
    try:
        response = client.get(f"/api/jobs/{job_id}/result")
        single = client.get(f"/api/jobs/{job_id}/playlists/1/result")
    finally:
        release.set()
    client.application.config["JOB_MANAGER"].get(job_id, "1").wait(timeout=10)

    assert response.status_code == 409
    assert response.get_json()["code"] == "job_running"
    # The single-playlist view stays available while the job runs; it is the
    # progress view that the frontend polls.
    assert single.status_code == 200
    assert single.get_json()["status"] in {"running", "completed"}


def test_retry_reports_how_many_tracks_it_picked_up(tmp_path, monkeypatch) -> None:
    client, job_id, music = _finished(tmp_path, monkeypatch)
    # The user deletes the download by hand, so the track has to be resolved again.
    (music / "Artist - Second.mp3").unlink()

    response = client.post(f"/api/jobs/{job_id}/playlists/1/processing/retry")

    assert response.status_code == 200
    assert response.get_json()["retried"] == 1
    client.application.config["JOB_MANAGER"].get(job_id, "1").wait(timeout=10)


def test_retry_refuses_a_playlist_that_is_still_running(tmp_path, monkeypatch) -> None:
    release = threading.Event()
    started = threading.Event()

    class BlockingResolver:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def resolve(self, track, stage_callback=None):
            started.set()
            release.wait(timeout=10)
            return TrackResolution(track=track, status="failed", reason="blocked")

    monkeypatch.setattr("spotm3u.web_jobs.TrackResolver", BlockingResolver)
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = _client(tmp_path, RESOLVE_WORKERS=1)
    job_id = _upload(tmp_path, client)
    client.post(f"/api/jobs/{job_id}/processing", json={"playlist_ids": ["1"]})
    assert started.wait(timeout=10)
    try:
        response = client.post(f"/api/jobs/{job_id}/playlists/1/processing/retry")
    finally:
        release.set()
    client.application.config["JOB_MANAGER"].get(job_id, "1").wait(timeout=10)

    assert response.status_code == 409
    assert response.get_json()["code"] == "job_running"


def test_m3u_download_returns_the_generated_playlist(tmp_path, monkeypatch) -> None:
    client, job_id, _music = _finished(tmp_path, monkeypatch)

    response = client.get(f"/api/jobs/{job_id}/playlists/1/m3u")

    assert response.status_code == 200
    assert b"#EXTM3U" in response.data
    assert response.mimetype == "application/octet-stream"
    assert "attachment" in response.headers["Content-Disposition"]
    assert "two.m3u" in response.headers["Content-Disposition"]


def test_m3u_download_warns_when_the_referenced_files_are_gone(tmp_path, monkeypatch) -> None:
    client, job_id, music = _finished(tmp_path, monkeypatch)
    (music / "Artist - Second.mp3").unlink()

    response = client.get(f"/api/jobs/{job_id}/playlists/1/m3u")

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["code"] == "playlist_incomplete"
    assert payload["missing"] == ["Artist - Second.mp3"]
    assert payload["confirm_url"].endswith("/m3u?confirm=1")
    # The playlist is still downloadable once the user confirms.
    assert client.get(payload["confirm_url"]).status_code == 200


def test_m3u_download_needs_a_finished_conversion(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)
    client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": ["1"]})

    response = client.get(f"/api/jobs/{job_id}/playlists/1/m3u")

    assert response.status_code == 404
    assert response.get_json()["code"] == "job_not_ready"


def test_artwork_endpoint_serves_the_cached_image(tmp_path, monkeypatch) -> None:
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"jpeg")
    client, job_id, _music = _finished(tmp_path, monkeypatch)
    monkeypatch.setattr("spotm3u.api.cached_artwork_path", lambda *_args: image)

    response = client.get(f"/api/jobs/{job_id}/playlists/1/artwork/0")

    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    assert response.data == b"jpeg"


def test_artwork_endpoint_404s_without_a_cached_image(tmp_path, monkeypatch) -> None:
    client, job_id, _music = _finished(tmp_path, monkeypatch)
    monkeypatch.setattr("spotm3u.api.cached_artwork_path", lambda *_args: None)

    response = client.get(f"/api/jobs/{job_id}/playlists/1/artwork/0")

    assert response.status_code == 404
    assert response.get_json()["code"] == "not_found"


def test_artwork_endpoint_404s_for_a_track_the_playlist_does_not_have(
    tmp_path, monkeypatch
) -> None:
    client, job_id, _music = _finished(tmp_path, monkeypatch)

    assert client.get(f"/api/jobs/{job_id}/playlists/1/artwork/9").status_code == 404


def test_artwork_endpoint_404s_for_a_download_that_was_deleted(tmp_path, monkeypatch) -> None:
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"jpeg")
    client, job_id, music = _finished(tmp_path, monkeypatch)
    monkeypatch.setattr("spotm3u.api.cached_artwork_path", lambda *_args: image)
    (music / "Artist - Second.mp3").unlink()

    assert client.get(f"/api/jobs/{job_id}/playlists/1/artwork/0").status_code == 404


def test_media_player_import_is_refused_without_platform_support(tmp_path, monkeypatch) -> None:
    client, job_id, _music = _finished(tmp_path, monkeypatch)
    monkeypatch.setattr("spotm3u.api.media_player_available", lambda: False)

    response = client.post(f"/api/jobs/{job_id}/playlists/1/media-player")

    assert response.status_code == 404
    assert response.get_json()["code"] == "media_player_unavailable"


def test_media_player_import_reports_the_outcome(tmp_path, monkeypatch) -> None:
    client, job_id, _music = _finished(tmp_path, monkeypatch)
    monkeypatch.setattr("spotm3u.api.media_player_available", lambda: True)

    def fake_add(name, m3u_path, paths):
        return MediaPlayerResult(action="imported", imported=len(paths), message=f"Added {name}.")

    monkeypatch.setattr("spotm3u.api.add_to_media_player", fake_add)

    payload = client.post(f"/api/jobs/{job_id}/playlists/1/media-player").get_json()

    assert payload["media_player"]["action"] == "imported"
    assert payload["media_player"]["imported"] == 1
    assert payload["media_player"]["message"] == "Added two."
    assert payload["media_player"]["unresolved"] == 0
    assert payload["media_player"]["partial"] is False
    # The whole state comes back, so the result view needs no second request.
    assert payload["counts"]["successful"] == 1


def test_media_player_import_reports_a_failure(tmp_path, monkeypatch) -> None:
    client, job_id, _music = _finished(tmp_path, monkeypatch)
    monkeypatch.setattr("spotm3u.api.media_player_available", lambda: True)

    def failing_add(*_args, **_kwargs):
        raise MediaPlayerError("Apple Music could not be opened.")

    monkeypatch.setattr("spotm3u.api.add_to_media_player", failing_add)

    response = client.post(f"/api/jobs/{job_id}/playlists/1/media-player")

    assert response.status_code == 502
    payload = response.get_json()
    assert payload["code"] == "media_player_failed"
    assert payload["error"] == "Apple Music could not be opened."


def test_media_player_import_needs_a_finished_playlist(tmp_path) -> None:
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client)

    response = client.post(f"/api/jobs/{job_id}/playlists/1/media-player")

    assert response.status_code == 404
    assert response.get_json()["code"] == "job_not_ready"


def test_batch_media_player_import_is_refused_without_a_media_library(
    tmp_path, monkeypatch
) -> None:
    client, job_id, _music = _finished(tmp_path, monkeypatch)
    monkeypatch.setattr("spotm3u.api.library_import_available", lambda: False)

    response = client.post(f"/api/jobs/{job_id}/media-player")

    assert response.status_code == 404
    assert response.get_json()["code"] == "media_player_unavailable"


def test_batch_media_player_import_reports_every_playlist(tmp_path, monkeypatch) -> None:
    client, job_id, _music = _finished(tmp_path, monkeypatch, playlist_ids=("1",))
    monkeypatch.setattr("spotm3u.api.library_import_available", lambda: True)

    def fake_add(name, m3u_path, paths):
        return MediaPlayerResult(action="imported", imported=len(paths), message=f"Added {name}.")

    monkeypatch.setattr("spotm3u.web_jobs.add_to_media_player", fake_add)

    payload = client.post(f"/api/jobs/{job_id}/media-player").get_json()

    assert payload["imported"] == 1
    assert [entry["playlist_id"] for entry in payload["playlists"]] == ["1"]
    assert payload["partial"] is False


def test_icon_endpoint_serves_the_application_artwork(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/icon.png")

    assert response.status_code == 200
    assert response.mimetype == "image/png"


def test_update_endpoint_reports_a_disabled_check(tmp_path) -> None:
    client = _client(tmp_path, UPDATE_CHECK=False)

    payload = client.get("/api/update").get_json()

    assert payload["update_available"] is False
    assert payload["error"] == "Update checks are disabled."


def test_unknown_api_paths_answer_json(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.get_json()["code"] == "not_found"


def test_a_wrong_method_on_an_api_route_answers_json(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.delete("/api/meta")

    assert response.status_code == 405
    assert response.get_json()["code"] == "method_not_allowed"


def test_the_jinja_pages_keep_their_own_404_page(tmp_path) -> None:
    """The API's JSON fallbacks must not leak into the pages still in use."""
    client = _client(tmp_path)

    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.mimetype == "text/html"
