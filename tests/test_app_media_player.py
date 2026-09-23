"""Tests for the "Add to Media Player" actions on result and batch pages."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from conftest import (
    NoCandidates,
    _job_directory,
    _run_local_match_job,
    _upload_and_select,
    export_zip,
)

from spotm3u.app import create_app
from spotm3u.media_player import MediaPlayerError, MediaPlayerResult


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
