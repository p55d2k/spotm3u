"""Tests for the "Add to Media Player" JSON actions."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from conftest import NoCandidates, export_zip

from spotm3u.app import create_app
from spotm3u.media_player import MediaPlayerError, MediaPlayerResult


def _client(tmp_path, music: Path | None = None, **config):
    music = music or tmp_path / "music"
    music.mkdir(exist_ok=True)
    return create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music, **config}).test_client()


def _upload(tmp_path, client, source=None) -> str:
    archive = source if source is not None else export_zip()
    response = client.post(
        "/api/upload",
        data={"file": (BytesIO(archive), "export.zip")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    return response.get_json()["job_id"]


def _run_local_match_job(tmp_path, monkeypatch):
    """Convert playlist 1 (track "Second") with a matching local file."""
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - Second.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = _client(tmp_path, music)
    job_id = _upload(tmp_path, client)
    client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": ["1"]})
    client.post(f"/api/jobs/{job_id}/processing", json={"playlist_ids": ["1"]})
    job = client.application.config["JOB_MANAGER"].get(job_id, "1")
    job.wait(timeout=10)
    return client, job, music


def test_media_player_import_reuses_the_generated_playlist(tmp_path, monkeypatch) -> None:
    client, job, music = _run_local_match_job(tmp_path, monkeypatch)
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

    monkeypatch.setattr("spotm3u.api.add_to_media_player", fake_add)
    monkeypatch.setattr("spotm3u.web_jobs.add_to_media_player", fake_add)

    response = client.post(f"/api/jobs/{job.job_id}/playlists/1/media-player")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["media_player"]["action"] == "created"
    assert payload["media_player"]["message"].startswith("Added to Media Player.")
    assert captured["playlist_name"] == job.playlist_name
    assert Path(captured["playlist_path"]) == music / "SpotM3U" / "playlist-1.m3u"
    assert Path(captured["playlist_path"]).is_file()
    assert [Path(path).name for path in captured["paths"]] == ["Artist - Second.mp3"]
    # The imported playlist stays available for the normal M3U download.
    assert client.get(f"/api/jobs/{job.job_id}/playlists/1/m3u").status_code == 200


def test_media_player_import_keeps_the_artwork_flags(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork, artwork_cache

    client, job, music = _run_local_match_job(tmp_path, monkeypatch)
    track = job.tracks[0]
    artwork_path = artwork_cache._cached_artwork_path(
        artwork_cache._cache_dir(music / "SpotM3U"),
        artwork_cache._cache_key(
            artwork.artwork_artist(track) or "", track.album or "", track.title
        ),
    )
    artwork_path.write_bytes(b"cached-artwork-bytes")

    def fake_add(playlist_name, playlist_path, paths):
        return MediaPlayerResult(action="created", imported=1, message="imported")

    monkeypatch.setattr("spotm3u.api.add_to_media_player", fake_add)

    response = client.post(f"/api/jobs/{job.job_id}/playlists/1/media-player")

    assert response.status_code == 200
    assert response.get_json()["tracks"][0]["artwork"] is True
    assert artwork_path.is_file()


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
    client = _client(tmp_path, music)
    job_id = _upload(tmp_path, client, source=_two_playlist_zip())
    client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": ["0", "1"]})
    client.post(f"/api/jobs/{job_id}/processing", json={"playlist_ids": ["0", "1"]})
    manager = client.application.config["JOB_MANAGER"]
    manager.get(job_id, "0").wait(timeout=10)
    manager.get(job_id, "1").wait(timeout=10)
    return client, job_id, manager


def test_add_all_imports_every_playlist_as_its_own(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.api.media_player_available", lambda: True)
    monkeypatch.setattr("spotm3u.api.library_import_available", lambda: True)
    client, job_id, _manager = _run_batch_job(tmp_path, monkeypatch)
    calls = []

    def fake_add(playlist_name, playlist_path, paths):
        calls.append((playlist_name, Path(playlist_path), [Path(path) for path in paths]))
        return MediaPlayerResult(
            action="created",
            imported=1,
            message=f'Added "{playlist_name}" to Apple Music.',
        )

    monkeypatch.setattr("spotm3u.web_jobs.add_to_media_player", fake_add)

    response = client.post(f"/api/jobs/{job_id}/media-player")

    assert response.status_code == 200
    payload = response.get_json()
    assert [name for name, _path, _paths in calls] == ["alpha", "zulu"]
    assert [path.name for _name, path, _paths in calls] == ["playlist-0.m3u", "playlist-1.m3u"]
    assert all(path.is_file() for _name, path, _paths in calls)
    assert [paths[0].name for _name, _path, paths in calls] == [
        "Artist A - Alpha Rhapsody.mp3",
        "Artist B - Zulu Ballad.mp3",
    ]
    assert "Added 2 of 2 playlist(s) to Apple Music." in payload["message"]
    assert "2 track(s) were imported." in payload["message"]


def test_add_all_reports_a_failed_playlist_without_stopping(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.api.media_player_available", lambda: True)
    monkeypatch.setattr("spotm3u.api.library_import_available", lambda: True)
    client, job_id, _manager = _run_batch_job(tmp_path, monkeypatch)
    imported = []

    def fake_add(playlist_name, _playlist_path, _paths):
        if playlist_name == "alpha":
            raise MediaPlayerError("Apple Music import failed: boom")
        imported.append(playlist_name)
        return MediaPlayerResult(action="created", imported=1, message="ok")

    monkeypatch.setattr("spotm3u.web_jobs.add_to_media_player", fake_add)

    response = client.post(f"/api/jobs/{job_id}/media-player")

    assert response.status_code == 200
    payload = response.get_json()
    assert imported == ["zulu"], "a failing playlist must not stop the others"
    assert payload["playlists"][0]["error"] == "Apple Music import failed: boom"
    assert "Added 1 of 2 playlist(s) to Apple Music." in payload["message"]
    assert "1 playlist(s) could not be added at all." in payload["message"]


def test_add_all_skips_playlists_without_resolved_tracks(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.api.media_player_available", lambda: True)
    monkeypatch.setattr("spotm3u.api.library_import_available", lambda: True)
    # Only the second playlist has a matching file, so the first has nothing to import.
    client, job_id, _manager = _run_batch_job(
        tmp_path, monkeypatch, local=("Artist B - Zulu Ballad.mp3",)
    )

    def fake_add(playlist_name, _playlist_path, _paths):
        return MediaPlayerResult(action="created", imported=1, message=f"ok {playlist_name}")

    monkeypatch.setattr("spotm3u.web_jobs.add_to_media_player", fake_add)

    response = client.post(f"/api/jobs/{job_id}/media-player")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["playlists"][0]["error"] == "There were no resolved tracks to add."
    assert "Added 1 of 2 playlist(s) to Apple Music." in payload["message"]


def test_add_all_requires_finished_playlists(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.api.library_import_available", lambda: True)
    client = _client(tmp_path)
    job_id = _upload(tmp_path, client, source=_two_playlist_zip())
    client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": ["0", "1"]})

    response = client.post(f"/api/jobs/{job_id}/media-player")

    assert response.status_code == 409
    assert response.get_json()["code"] == "job_running"
