"""End-to-end web flow test (task 30).

Exercises the complete user journey—upload → select → start → poll → result
→ M3U download—with both local audio matching and online source resolution
working together. Network and audio inspection boundaries are mocked.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from spotm3u.app import create_app
from spotm3u.online.search import SourceCandidate

ZIP_HEADER = "Track URI,Track Name,Album Name,Artist Name(s),Duration (ms)\n"


def _job_id(tmp_path) -> str:
    return next(
        path for path in tmp_path.iterdir() if path.name.startswith("job-")
    ).name.removeprefix("job-")


def _export_zip(*files: tuple[str, str]) -> bytes:
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        for name, content in files:
            zf.writestr(name, content)
    return buf.getvalue()


class _FakeSearcher:
    """Return a plausible online candidate for any track without a local match."""

    def __init__(self, no_results_titles: frozenset[str] = frozenset()):
        self._no_results = no_results_titles

    def search(self, track):
        if track.title in self._no_results:
            return ()
        return (
            SourceCandidate(
                url=f"https://example.com/{track.title.lower().replace(' ', '-')}",
                title=track.title,
                uploader=f"{track.artists[0]} - Topic" if track.artists else "Unknown",
                artist=track.artists[0] if track.artists else None,
                duration_s=(track.duration_ms / 1000) if track.duration_ms else None,
            ),
        )


def _mock_download(track, source_url, output_dir, **_kwargs):
    """Write a fake MP3 so the pipeline treats the download as real."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"{track.title} - {', '.join(track.artists)}.mp3"
    path = output_dir / name
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 1024)
    return path


def _mock_audio_valid(track, path):
    from spotm3u.online.audio_validation import AudioValidation

    return AudioValidation(path=Path(path), status="valid")


ZIP_CONTENT = _export_zip(
    (
        "Mixed.csv",
        ZIP_HEADER
        + "spotify:track:local,First Song,,Local Artist,200000\n"
        "spotify:track:online,Second Song,,Online Artist,250000\n"
        "spotify:track:miss,Missing Song,,Nobody,180000\n",
    ),
    (
        "Short.csv",
        ZIP_HEADER + "spotify:track:solo,Alone,,Solo Artist,300000\n",
    ),
)


def test_full_e2e_flow_local_download_and_missing(tmp_path, monkeypatch):
    music = tmp_path / "music"
    music.mkdir()
    (music / "Local Artist - First Song.mp3").write_bytes(b"audio")

    monkeypatch.setattr(
        "spotm3u.app.OnlineSourceSearcher",
        lambda **kw: _FakeSearcher(no_results_titles=frozenset({"Missing Song"})),
    )
    monkeypatch.setattr("spotm3u.app.download_track", _mock_download)
    monkeypatch.setattr(
        "spotm3u.resolution.validate_downloaded_audio", _mock_audio_valid
    )

    download_dir = tmp_path / "downloads"
    client = create_app(
        {"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music, "DOWNLOAD_DIR": str(download_dir)}
    ).test_client()

    # 1. Upload
    resp = client.post(
        "/upload",
        data={"file": (BytesIO(ZIP_CONTENT), "export.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    assert b"Mixed" in resp.data
    assert b"Short" in resp.data
    job_id = _job_id(tmp_path)

    # 2. Select playlist
    resp = client.post(f"/playlists/{job_id}/select", data={"playlist_id": "0"})
    assert resp.status_code == 302
    assert resp.headers["Location"] == f"/processing/{job_id}/0"

    # 3. Processing page
    resp = client.get(f"/processing/{job_id}/0")
    assert resp.status_code == 200
    assert b"Mixed" in resp.data
    assert b"Total tracks: 3" in resp.data

    # 4. Start processing
    resp = client.post(f"/processing/{job_id}/0/start")
    assert resp.status_code == 202
    state = resp.get_json()
    assert state["playlist"]["total_tracks"] == 3

    # 5. Poll until complete
    job = client.application.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=30)
    resp = client.get(f"/processing/{job_id}/0/status")
    assert resp.status_code == 200
    final = resp.get_json()
    assert final["status"] == "completed"
    assert final["successful"] == 2
    assert final["failed"] == 1
    assert final["ambiguous"] == 0

    tracks = {t["title"]: t for t in final["tracks"]}
    assert tracks["First Song"]["resolution"] == "local"
    assert tracks["Second Song"]["resolution"] == "downloaded"
    assert tracks["Missing Song"]["resolution"] == "missing"

    # 6. Result page
    resp = client.get(f"/processing/{job_id}/0/result")
    assert resp.status_code == 200
    assert b"Successfully resolved" in resp.data
    assert b"Local matches" in resp.data
    assert b"Downloaded" in resp.data
    assert b"Missing" in resp.data
    assert b"Total tracks: 3" in resp.data

    # 7. Download M3U
    resp = client.get(f"/processing/{job_id}/0/playlist.m3u")
    assert resp.status_code == 200
    assert b"#EXTM3U" in resp.data
    assert "attachment" in resp.headers["Content-Disposition"]
    body = resp.data.decode()
    assert "First Song" in body
    assert "Second Song" in body
    # Missing track must NOT appear in the M3U
    assert "Missing Song" not in body

    # 8. Verify the downloaded file exists on disk
    assert (download_dir / "Second Song - Online Artist.mp3").is_file()
    assert (music / "Local Artist - First Song.mp3").is_file()


def test_e2e_second_playlist_independent(tmp_path, monkeypatch):
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.app.OnlineSourceSearcher", lambda **kw: _FakeSearcher())
    monkeypatch.setattr("spotm3u.app.download_track", _mock_download)
    monkeypatch.setattr(
        "spotm3u.resolution.validate_downloaded_audio", _mock_audio_valid
    )

    download_dir = tmp_path / "downloads"
    client = create_app(
        {"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music, "DOWNLOAD_DIR": str(download_dir)}
    ).test_client()

    resp = client.post(
        "/upload",
        data={"file": (BytesIO(ZIP_CONTENT), "export.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    job_id = _job_id(tmp_path)

    resp = client.post(f"/playlists/{job_id}/select", data={"playlist_id": "1"})
    assert resp.status_code == 302

    resp = client.post(f"/processing/{job_id}/1/start")
    assert resp.status_code == 202

    job = client.application.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=30)

    final = client.get(f"/processing/{job_id}/1/status").get_json()
    assert final["status"] == "completed"
    assert final["successful"] == 1
    assert final["failed"] == 0
    assert final["tracks"][0]["resolution"] == "downloaded"

    resp = client.get(f"/processing/{job_id}/1/playlist.m3u")
    assert resp.status_code == 200
    assert b"#EXTM3U" in resp.data
    assert "Alone" in resp.data.decode()


def test_e2e_all_tracks_local_no_online_search(tmp_path, monkeypatch):
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist A - Song One.mp3").write_bytes(b"audio")
    (music / "Artist B - Song Two.mp3").write_bytes(b"audio")

    search_called = False

    class _NoSearch:
        def search(self, track):
            nonlocal search_called
            search_called = True
            return ()

    monkeypatch.setattr("spotm3u.app.OnlineSourceSearcher", lambda **kw: _NoSearch())

    client = create_app(
        {"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}
    ).test_client()

    zip_data = _export_zip(
        (
            "AllLocal.csv",
            ZIP_HEADER
            + "spotify:track:a,Song One,,Artist A,200000\n"
            "spotify:track:b,Song Two,,Artist B,250000\n",
        ),
    )

    resp = client.post(
        "/upload",
        data={"file": (BytesIO(zip_data), "export.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    job_id = _job_id(tmp_path)

    client.post(f"/playlists/{job_id}/select", data={"playlist_id": "0"})
    client.post(f"/processing/{job_id}/0/start")

    job = client.application.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=10)

    final = client.get(f"/processing/{job_id}/0/status").get_json()
    assert final["status"] == "completed"
    assert final["successful"] == 2
    assert final["failed"] == 0
    assert all(t["resolution"] == "local" for t in final["tracks"])

    resp = client.get(f"/processing/{job_id}/0/result")
    assert resp.status_code == 200
    assert b"Local matches" in resp.data
    assert b"<dd>2</dd>" in resp.data

    assert not search_called
