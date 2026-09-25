"""Tests for the Flask application scaffold: download defaults and API-driven behaviour."""

from io import BytesIO
from pathlib import Path

from conftest import NoCandidates, export_zip

from spotm3u import web_jobs
from spotm3u.app import create_app


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


def _start_playlist(client, job_id, playlist_id="1", **extra):
    client.put(f"/api/jobs/{job_id}/selection", json={"playlist_ids": [playlist_id]})
    started = client.post(
        f"/api/jobs/{job_id}/processing", json={"playlist_ids": [playlist_id], **extra}
    )
    assert started.status_code == 202
    return started.get_json()


def _await(client, job_id, playlist_id="1"):
    client.application.config["JOB_MANAGER"].get(job_id, playlist_id).wait(timeout=10)


def test_default_download_dir_renames_the_older_folder(tmp_path) -> None:
    """An upgrade finds its downloads under the new name, contents intact."""
    music = tmp_path / "music"
    legacy = music / "SpotM3U-downloads"
    legacy.mkdir(parents=True)
    (legacy / "Artist - Song.mp3").write_bytes(b"audio")
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})

    resolved = web_jobs._download_dir(app)

    assert resolved == music / "SpotM3U"
    assert (music / "SpotM3U" / "Artist - Song.mp3").read_bytes() == b"audio"
    assert not legacy.exists()


def test_default_download_dir_keeps_both_when_the_new_one_exists(tmp_path) -> None:
    """Two folders are never merged: the new one is used, the older is untouched."""
    music = tmp_path / "music"
    legacy = music / "SpotM3U-downloads"
    legacy.mkdir(parents=True)
    (legacy / "Artist - Old.mp3").write_bytes(b"old")
    (music / "SpotM3U").mkdir()
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})

    resolved = web_jobs._download_dir(app)

    assert resolved == music / "SpotM3U"
    assert (legacy / "Artist - Old.mp3").is_file()


def test_default_download_dir_keeps_using_a_folder_that_cannot_be_renamed(
    tmp_path, monkeypatch
) -> None:
    """A failed rename must not hide a user's existing downloads."""
    music = tmp_path / "music"
    legacy = music / "SpotM3U-downloads"
    legacy.mkdir(parents=True)

    def deny_rename(self, target):
        raise OSError("cross-device link")

    monkeypatch.setattr(Path, "rename", deny_rename)
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})

    assert web_jobs._download_dir(app) == legacy
    assert legacy.is_dir()


def test_configured_download_dir_is_never_migrated(tmp_path) -> None:
    """An explicit download_dir names the folder the user chose."""
    music = tmp_path / "music"
    legacy = music / "SpotM3U-downloads"
    legacy.mkdir(parents=True)
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music, "DOWNLOAD_DIR": str(legacy)})

    assert web_jobs._download_dir(app) == legacy
    assert legacy.is_dir()


def test_processing_job_resolves_local_matches(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = _client(tmp_path, music)
    job_id = _upload(tmp_path, client)

    _start_playlist(client, job_id)
    _await(client, job_id)
    final = client.get(f"/api/jobs/{job_id}/playlists/1/processing").get_json()

    assert final["status"] == "completed"
    assert final["successful"] == 1
    assert final["failed"] == 0
    m3u_path = music / "SpotM3U" / "playlist-1.m3u"
    assert str(music / "Artist - First.mp3") in m3u_path.read_text(encoding="utf-8")


def test_local_matching_scans_the_configured_library_extensions(tmp_path, monkeypatch) -> None:
    """``[library] extensions`` decides which files local matching scans."""
    music = tmp_path / "music"
    music.mkdir()
    # A format the built-in list does not cover, so only the configured
    # extension can make this track resolvable.
    (music / "Artist - First.ape").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = _client(tmp_path, music, LIBRARY_EXTENSIONS=frozenset({".ape"}))
    job_id = _upload(tmp_path, client)

    _start_playlist(client, job_id)
    _await(client, job_id)
    final = client.get(f"/api/jobs/{job_id}/playlists/1/processing").get_json()

    assert final["successful"] == 1
    m3u = (music / "SpotM3U" / "playlist-1.m3u").read_text(encoding="utf-8")
    assert str(music / "Artist - First.ape") in m3u


def test_download_dir_override_respected(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    download_dir = tmp_path / "custom-downloads"
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = _client(tmp_path, music, DOWNLOAD_DIR=str(download_dir))
    job_id = _upload(tmp_path, client)

    started = _start_playlist(client, job_id)
    _await(client, job_id)

    assert started["playlists"][0]["output_dir"] == str(download_dir)
    final = client.get(f"/api/jobs/{job_id}/playlists/1/processing").get_json()
    assert final["m3u_path"] == str(download_dir / "playlist-1.m3u")
    assert (download_dir / "playlist-1.m3u").is_file()


def test_start_processing_honours_fast_mode(tmp_path, monkeypatch) -> None:
    """Fast mode must not enrich metadata, and the fast resolver is the one that runs."""
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())

    def forbidden(*_args, **_kwargs):
        raise AssertionError("fast mode must not enrich metadata")

    monkeypatch.setattr("spotm3u.resolution.enrich_metadata", forbidden)
    built: list[bool] = []

    class SpyFastResolver(web_jobs.FastTrackResolver):
        def __init__(self, *args, **kwargs):
            built.append(True)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(web_jobs, "FastTrackResolver", SpyFastResolver)
    client = _client(tmp_path, music)
    job_id = _upload(tmp_path, client)

    started = _start_playlist(client, job_id, fast_mode=True)
    _await(client, job_id)
    final = client.get(f"/api/jobs/{job_id}/playlists/1/processing").get_json()

    assert started["playlists"][0]["fast_mode"] is True
    assert final["fast_mode"] is True
    assert final["successful"] == 1
    assert final["failed"] == 0
    assert built == [True], "the fast resolver must be the one that runs"


def test_status_reports_no_artwork_for_a_deleted_download(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork, artwork_cache

    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = _client(tmp_path, music)
    job_id = _upload(tmp_path, client)

    _start_playlist(client, job_id)
    _await(client, job_id)
    job = client.application.config["JOB_MANAGER"].get(job_id, "1")
    track = job.tracks[0]
    artwork_path = artwork_cache._cached_artwork_path(
        artwork_cache._cache_dir(job.output_dir),
        artwork_cache._cache_key(
            artwork.artwork_artist(track) or "", track.album or "", track.title
        ),
    )
    artwork_path.write_bytes(b"cached-artwork-bytes")
    progress_url = f"/api/jobs/{job_id}/playlists/1/processing"

    assert client.get(progress_url).get_json()["tracks"][0]["artwork"] is True

    Path(job.as_dict()["tracks"][0]["local_path"]).unlink()

    state = client.get(progress_url).get_json()

    assert state["tracks"][0]["file_missing"] is True
    assert state["tracks"][0]["artwork"] is False
