"""Shared test fixtures and helpers."""

from io import BytesIO
from zipfile import ZipFile

import pytest
import requests

from spotm3u import artwork
from spotm3u.app import create_app


@pytest.fixture(autouse=True)
def no_network_artwork(monkeypatch):
    """Keep artwork enrichment offline unless a test installs its own mock.

    Resolution jobs enrich resolved tracks with artwork; without this fixture
    those jobs would make real MusicBrainz/iTunes requests during unrelated
    tests. Tests that exercise artwork lookup override ``requests.get``
    themselves.
    """

    def unreachable(*_args, **_kwargs):
        raise requests.ConnectionError("network disabled in tests")

    monkeypatch.setattr("spotm3u.artwork_sources.requests.get", unreachable)


@pytest.fixture(autouse=True)
def no_network_lyrics(monkeypatch):
    """Keep lyrics enrichment offline unless a test installs its own stub.

    Metadata enrichment asks the ``syncedlyrics`` library for lyrics; without
    this fixture unrelated tests would query real lyrics providers. Tests that
    exercise lyrics override ``spotm3u.lyrics.syncedlyrics.search``.
    """

    def unreachable(*_args, **_kwargs):
        raise requests.ConnectionError("network disabled in tests")

    monkeypatch.setattr("spotm3u.lyrics.syncedlyrics.search", unreachable)


@pytest.fixture
def verify_local_toggle():
    """Run a test with the artwork verification flag, restoring the default after."""

    yield
    artwork.set_artwork_verify_local(True)


def export_zip() -> bytes:
    """A minimal Exportify archive with two playlists, "First" and "Second"."""
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
    return client, job, music / "SpotM3U"
