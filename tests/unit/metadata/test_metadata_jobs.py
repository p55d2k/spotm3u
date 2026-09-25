from pathlib import Path

import pytest

from spotm3u.metadata_jobs import MetadataJob
from spotm3u.models import Track


def test_metadata_job_can_wait_for_audio(tmp_path):
    track = Track(title="Song", artists=("Artist",), album="Album")
    job = MetadataJob(track, tmp_path)

    with pytest.raises(ValueError, match="audio path"):
        job.run()

    ready = job.with_audio_path(tmp_path / "song.mp3")

    assert ready.audio_path == tmp_path / "song.mp3"
    assert ready.track == track
    assert ready.download_dir == Path(tmp_path)


def test_metadata_job_delegates_to_existing_enrichment(monkeypatch, tmp_path):
    track = Track(title="Song", artists=("Artist",), album="Album")
    expected = object()
    calls = []

    def fake_enrich(path, received_track, download_dir):
        calls.append((path, received_track, download_dir))
        return expected

    monkeypatch.setattr("spotm3u.metadata.enrich_metadata", fake_enrich)
    job = MetadataJob(track, tmp_path, tmp_path / "song.mp3")

    assert job.run() is expected
    assert calls == [(tmp_path / "song.mp3", track, tmp_path)]
