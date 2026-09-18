from pathlib import Path

from spotm3u.audio import LocalAudioResolver
from spotm3u.models import Track
from spotm3u.online import SourceCandidate
from spotm3u.resolution import ResolutionReport, TrackResolver


TRACK = Track("Song", ["Artist"], duration_ms=200_000)


class Searcher:
    def __init__(self, candidates):
        self.candidates = candidates

    def search(self, track):
        return self.candidates


def candidate(**overrides) -> SourceCandidate:
    values = {
        "url": "https://example.com/song",
        "title": "Song - Official Audio",
        "artist": "Artist",
        "uploader": "Artist",
        "duration_s": 200,
        "source_type": "youtube",
    }
    values.update(overrides)
    return SourceCandidate(**values)


def test_local_match_is_final_and_does_not_search(tmp_path: Path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    local_file = music / "Artist - Song.mp3"
    local_file.write_bytes(b"audio")

    class NoSearch:
        def search(self, track):
            raise AssertionError("online search should not run")

    result = TrackResolver(LocalAudioResolver(music), tmp_path / "output", searcher=NoSearch()).resolve(TRACK)

    assert result.status == "local"
    assert result.successful
    assert result.local_path == local_file


def test_online_resolution_requires_accepted_and_valid_audio(
    tmp_path: Path, monkeypatch
) -> None:
    downloaded = tmp_path / "output" / "song.mp3"
    downloaded.parent.mkdir()
    downloaded.write_bytes(b"audio")

    monkeypatch.setattr(
        "spotm3u.resolution.validate_downloaded_audio",
        lambda track, path: type("Validation", (), {"status": "valid", "reasons": ()})(),
    )
    result = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=Searcher((candidate(),)),
        downloader=lambda track, url, output: downloaded,
    ).resolve(TRACK)

    assert result.status == "downloaded"
    assert result.successful
    assert result.resolved is not None
    assert result.resolved.source_url == candidate().url


def test_incomplete_metadata_candidate_is_downloaded(tmp_path: Path, monkeypatch) -> None:
    incomplete = SourceCandidate(
        url="https://example.com/song",
        title="Song - Official Audio",
        artist="Artist",
        uploader=None,
        duration_s=None,
        source_type="youtube",
    )
    downloaded = tmp_path / "output" / "song.mp3"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"audio")
    monkeypatch.setattr(
        "spotm3u.resolution.validate_downloaded_audio",
        lambda track, path: type("Validation", (), {"status": "valid", "reasons": ()})(),
    )

    result = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=Searcher((incomplete,)),
        downloader=lambda track, url, output: downloaded,
    ).resolve(TRACK)

    assert result.status == "downloaded"
    assert result.successful


def test_invalid_download_file_is_discarded(tmp_path: Path, monkeypatch) -> None:
    downloaded = tmp_path / "output" / "song.mp3"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"audio")
    monkeypatch.setattr(
        "spotm3u.resolution.validate_downloaded_audio",
        lambda track, path: type("Validation", (), {
            "status": "invalid",
            "reasons": ("speech detected",),
        })(),
    )

    result = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=Searcher((candidate(),)),
        downloader=lambda track, url, output: downloaded,
    ).resolve(TRACK)

    assert result.status == "failed"
    assert not downloaded.exists()


def test_tries_next_candidate_when_first_download_fails_audio_validation(
    tmp_path: Path, monkeypatch
) -> None:
    first_url = "https://example.com/first"
    second_url = "https://example.com/second"
    first_download = tmp_path / "output" / "first.mp3"
    second_download = tmp_path / "output" / "second.mp3"
    first_download.parent.mkdir(parents=True)
    first_download.write_bytes(b"audio")
    second_download.write_bytes(b"audio")

    def fake_validation(track, path):
        return type(
            "Validation",
            (),
            {
                "status": "valid" if path == second_download else "invalid",
                "reasons": () if path == second_download else ("speech detected",),
            },
        )()

    monkeypatch.setattr("spotm3u.resolution.validate_downloaded_audio", fake_validation)

    candidates = (
        candidate(url=first_url),
        candidate(url=second_url),
    )
    downloaded: list[str] = []

    def downloader(track, url, output):
        downloaded.append(url)
        return first_download if url == first_url else second_download

    result = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=Searcher(candidates),
        downloader=downloader,
    ).resolve(TRACK)

    assert downloaded == [first_url, second_url]
    assert result.status == "downloaded"
    assert result.resolved.source_url == second_url


def test_retries_when_first_download_fails(tmp_path: Path, monkeypatch) -> None:
    first_url = "https://example.com/first"
    second_url = "https://example.com/second"
    second_download = tmp_path / "output" / "second.mp3"
    second_download.parent.mkdir(parents=True)
    second_download.write_bytes(b"audio")
    monkeypatch.setattr(
        "spotm3u.resolution.validate_downloaded_audio",
        lambda track, path: type("Validation", (), {"status": "valid", "reasons": ()})(),
    )

    def downloader(track, url, output):
        if url == first_url:
            raise OSError("network down")
        return second_download

    result = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=Searcher((candidate(url=first_url), candidate(url=second_url))),
        downloader=downloader,
    ).resolve(TRACK)

    assert result.status == "downloaded"
    assert result.resolved.source_url == second_url


def test_all_downloads_failing_reports_failed(tmp_path: Path) -> None:
    def downloader(track, url, output):
        raise OSError("network down")

    result = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=Searcher((candidate(),)),
        downloader=downloader,
    ).resolve(TRACK)

    assert result.status == "failed"
    assert any("download" in reason for reason in result.reasons)


def test_all_rejected_candidates_report_rejected(tmp_path: Path) -> None:
    wrong = SourceCandidate(
        url="https://example.com/live",
        title="Song (Live)",
        artist="Artist",
        uploader="Artist",
        duration_s=200,
        source_type="youtube",
    )
    result = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=Searcher((wrong,)),
        downloader=lambda track, url, output: (_ for _ in ()).throw(AssertionError("must not download")),
    ).resolve(TRACK)

    assert result.status == "rejected"
    assert result.reason == "no candidate passed source validation"


def test_downloaded_audio_uncertainty_is_not_success(tmp_path: Path, monkeypatch) -> None:
    downloaded = tmp_path / "output" / "song.mp3"
    downloaded.parent.mkdir()
    downloaded.write_bytes(b"audio")
    monkeypatch.setattr(
        "spotm3u.resolution.validate_downloaded_audio",
        lambda track, path: type("Validation", (), {
            "status": "uncertain",
            "reasons": ("speech detected",),
        })(),
    )

    result = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=Searcher((candidate(),)),
        downloader=lambda track, url, output: downloaded,
    ).resolve(TRACK)

    assert result.status == "uncertain"
    assert result.reason == "speech detected"
    assert result.as_dict()["local_path"] == str(downloaded)
    assert not result.successful


def test_resolution_report_exposes_all_track_outcomes(tmp_path: Path) -> None:
    report = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=Searcher(()),
    ).resolve_report([TRACK, TRACK])

    assert isinstance(report, ResolutionReport)
    assert report.counts["missing"] == 2
    assert report.as_dict()["total"] == 2
    assert report.as_dict()["tracks"][0]["track"]["title"] == "Song"
    assert report.as_dict()["tracks"][0]["status"] == "missing"
    assert report.as_dict()["tracks"][0]["reason"]
