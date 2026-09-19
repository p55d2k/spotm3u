"""Tests for diagnostic logging: context helpers and pipeline traceability."""

import logging

from spotm3u import log as log_module
from spotm3u.audio.resolver import LocalAudioResolver
from spotm3u.jobs import ProcessingJob
from spotm3u.log import TrackLogger, configure_logging, track_identifier
from spotm3u.models import Track
from spotm3u.online.search import SourceCandidate
from spotm3u.resolution import TrackResolver


def test_track_identifier_prefers_spotify_id() -> None:
    track = Track("Song", ["Artist"], spotify_id="abc123")
    assert track_identifier(track) == "abc123"


def test_track_identifier_falls_back_to_title_and_artists() -> None:
    track = Track("演员", ["薛之谦"])
    assert track_identifier(track) == "演员 - 薛之谦"


def test_track_logger_prefixes_job_and_track(caplog) -> None:
    logger = logging.getLogger("spotm3u.logtest")

    with caplog.at_level(logging.INFO, logger="spotm3u.logtest"):
        TrackLogger(logger, job_id="job-1", track=Track("Song", ["Artist"])).info(
            "stage=searching status=ok"
        )

    assert caplog.records
    record = caplog.records[-1]
    assert "job=job-1" in record.getMessage()
    assert "track=Song - Artist" in record.getMessage()
    assert "stage=searching" in record.getMessage()


def test_track_logger_with_track_binds_a_new_track() -> None:
    logger = logging.getLogger("spotm3u.logtest")
    base = TrackLogger(logger, job_id="job-1", track=Track("A", ["X"]))
    swapped = base.with_track(Track("B", ["Y"]))

    assert swapped._track.title == "B"
    assert swapped._job_id == "job-1"


def test_configure_logging_is_idempotent(monkeypatch) -> None:
    logger = logging.getLogger(log_module.PACKAGE_LOGGER)

    configure_logging("DEBUG")
    handler_count_after_first = len(logger.handlers)

    configure_logging("WARNING")

    assert len(logger.handlers) == handler_count_after_first
    assert logger.level == logging.WARNING


def test_configure_logging_uses_env_level(monkeypatch) -> None:
    monkeypatch.setenv(log_module.LOG_LEVEL_ENV, "DEBUG")
    configure_logging()
    assert logging.getLogger(log_module.PACKAGE_LOGGER).level == logging.DEBUG


def test_attach_job_logging_sets_resolver_job_context(tmp_path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    resolver = TrackResolver(LocalAudioResolver(music), tmp_path / "output")

    log_module.attach_job_logging(resolver, job_id="abc")

    assert isinstance(resolver._log, TrackLogger)
    assert resolver._log._job_id == "abc"


def test_failed_track_is_traceable_through_job_logs(tmp_path, caplog) -> None:
    music = tmp_path / "music"
    music.mkdir()
    tracks = [Track("Song", ["Artist"], duration_ms=200_000)]

    class EmptySearcher:
        def search(self, track):
            return ()

    resolver = TrackResolver(
        LocalAudioResolver(music),
        tmp_path / "output",
        searcher=EmptySearcher(),
    )
    job = ProcessingJob(
        job_id="trace-me",
        playlist_id="0",
        playlist_name="Playlist",
        tracks=tracks,
        output_dir=tmp_path / "output",
        resolver_factory=lambda: resolver,
    )

    with caplog.at_level(logging.DEBUG, logger="spotm3u"):
        job.start()
        job.wait(timeout=5)

    assert job.as_dict()["status"] == "completed"
    assert [state["resolution"] for state in job.as_dict()["tracks"]] == ["missing"]

    text = caplog.text
    assert "job=trace-me" in text
    assert "track=Song - Artist" in text
    assert "job started" in text
    assert "job completed" in text
    for stage in (
        "stage=resolving-local",
        "stage=searching",
        "searched index=",
        "finalized index=0",
    ):
        assert stage in text
    assert "no online source candidates" in text
    assert "no local match" in text


def test_resolution_failure_logs_download_and_validation_status(
    tmp_path, caplog, monkeypatch
) -> None:
    music = tmp_path / "music"
    music.mkdir()
    downloaded = tmp_path / "output" / "song.mp3"
    downloaded.parent.mkdir()
    downloaded.write_bytes(b"audio")
    track = Track("Song", ["Artist"], duration_ms=200_000)

    class Searcher:
        def search(self, track):
            return (
                SourceCandidate(
                    url="https://example.com/song",
                    title="Song - Official Audio",
                    artist="Artist",
                    uploader="Artist",
                    duration_s=200,
                    source_type="youtube",
                    source_query="Artist Song",
                ),
            )

    monkeypatch.setattr(
        "spotm3u.resolution.validate_downloaded_audio",
        lambda track, path: type(
            "Validation", (), {"status": "invalid", "reasons": ("speech detected",)}
        )(),
    )
    resolver = TrackResolver(
        LocalAudioResolver(music),
        tmp_path / "output",
        searcher=Searcher(),
        downloader=lambda track, url, output: downloaded,
    )

    with caplog.at_level(logging.DEBUG, logger="spotm3u"):
        result = resolver.resolve(track)

    assert result.status == "failed"
    text = caplog.text
    assert "job=" not in text or True
    assert "track=Song - Artist" in text
    assert "source_status=accepted" in text
    assert "stage=downloading" in text
    assert "download completed" in text
    assert "audio_status=invalid" in text
    assert "downloaded audio invalid" in text
    assert "all downloaded candidates failed audio validation" in text
