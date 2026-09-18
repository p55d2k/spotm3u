from pathlib import Path

from spotm3u.audio import LocalAudioResolver
from spotm3u.models import Track
from spotm3u.online import SourceCandidate
from spotm3u.resolution import TrackResolver


TRACK = Track("Song", ["Artist"], duration_ms=200_000)


class Searcher:
    def __init__(self, candidates):
        self.candidates = candidates

    def search(self, track):
        return self.candidates


def candidate() -> SourceCandidate:
    return SourceCandidate(
        url="https://example.com/song",
        title="Song - Official Audio",
        artist="Artist",
        uploader="Artist",
        duration_s=200,
        source_type="youtube",
    )


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


def test_uncertain_sources_never_succeed(tmp_path: Path) -> None:
    incomplete = candidate()
    incomplete = SourceCandidate(
        url=incomplete.url,
        title=incomplete.title,
        artist=incomplete.artist,
        uploader=None,
        duration_s=None,
        source_type=incomplete.source_type,
    )

    result = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=Searcher((incomplete,)),
        downloader=lambda *args: (_ for _ in ()).throw(AssertionError("must not download")),
    ).resolve(TRACK)

    assert result.status == "uncertain"
    assert not result.successful
