"""Tests for lyrics enrichment.

The lyrics library is always stubbed here (and suite-wide through the
``no_network_lyrics`` fixture), so no test depends on live lyrics providers.
"""

from pathlib import Path

import mutagen.id3 as mutagen_id3
import pytest
import requests

from spotm3u.audio import LocalAudioResolver
from spotm3u.lyrics import (
    fetch_lyrics,
    lyrics_search_term,
    normalize_lyrics,
    set_lyrics_enabled,
)
from spotm3u.metadata import MetadataResult, enrich_metadata
from spotm3u.models import Track
from spotm3u.online import SourceCandidate
from spotm3u.resolution import TrackResolver

LYRICS = "Today is gonna be the day\nThat they're gonna throw it back to you"
TRACK = Track(
    title="Wonderwall",
    artists=["Oasis"],
    album="(What's the Story) Morning Glory?",
    spotify_id="track-1",
)


@pytest.fixture
def lyrics_on():
    """Run one test with lyrics enabled, restoring the default afterwards."""
    set_lyrics_enabled(True)
    yield
    set_lyrics_enabled(True)


@pytest.fixture
def lyrics_off():
    """Run one test with lyrics disabled (what fast mode does)."""
    set_lyrics_enabled(False)
    yield
    set_lyrics_enabled(True)


def _stub_search(monkeypatch, result):
    """Replace the lyrics library search; returns the recorded calls."""
    calls: list[tuple[str, dict]] = []

    def fake_search(term, **kwargs):
        calls.append((term, kwargs))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("spotm3u.lyrics.syncedlyrics.search", fake_search)
    return calls


def _write_mp3(tmp_path: Path, name: str = "track.mp3") -> Path:
    path = tmp_path / name
    path.write_bytes(b"fake-mp3-data")
    return path


def _uslt_texts(path: Path) -> list[str]:
    return [frame.text for frame in mutagen_id3.ID3(str(path)).getall("USLT")]


def test_lyrics_are_written_to_the_standard_lyrics_field(tmp_path, monkeypatch, lyrics_on):
    calls = _stub_search(monkeypatch, LYRICS)
    path = _write_mp3(tmp_path)

    result = enrich_metadata(path, TRACK, tmp_path)

    assert "USLT" in result.fields_written
    assert _uslt_texts(path) == [LYRICS]
    # One plain-lyrics lookup for "[title] [artist]".
    assert [term for term, _kwargs in calls] == ["Wonderwall Oasis"]
    assert calls[0][1] == {"plain_only": True}


def test_missing_lyrics_leaves_the_lyrics_field_empty(tmp_path, monkeypatch, lyrics_on):
    _stub_search(monkeypatch, None)
    path = _write_mp3(tmp_path)

    result = enrich_metadata(path, TRACK, tmp_path)

    assert "USLT" not in result.fields_written
    assert _uslt_texts(path) == []
    assert "TIT2" in result.fields_written


@pytest.mark.parametrize("unusable", ["", "   ", 42, {"lyrics": LYRICS}, b"bytes"])
def test_unusable_lyrics_result_is_ignored(tmp_path, monkeypatch, lyrics_on, unusable):
    _stub_search(monkeypatch, unusable)
    path = _write_mp3(tmp_path)

    result = enrich_metadata(path, TRACK, tmp_path)

    assert isinstance(result, MetadataResult)
    assert "USLT" not in result.fields_written
    assert _uslt_texts(path) == []


def test_lyrics_library_failure_does_not_fail_enrichment(tmp_path, monkeypatch, lyrics_on):
    _stub_search(monkeypatch, requests.ConnectionError("lyrics provider unreachable"))
    path = _write_mp3(tmp_path)

    result = enrich_metadata(path, TRACK, tmp_path)

    assert isinstance(result, MetadataResult)
    assert "USLT" not in result.fields_written
    assert "TIT2" in result.fields_written
    assert result.path == path


def test_unsupported_audio_format_does_not_fail_enrichment(tmp_path, monkeypatch, lyrics_on):
    """A metadata writer that cannot hold lyrics is contained, not fatal."""

    def unsupported_uslt(**_kwargs):
        raise ValueError("lyrics frames are not supported for this audio format")

    monkeypatch.setattr(mutagen_id3, "USLT", unsupported_uslt)
    _stub_search(monkeypatch, LYRICS)
    path = _write_mp3(tmp_path, "track.flac")

    result = enrich_metadata(path, TRACK, tmp_path)

    assert "USLT" not in result.fields_written
    assert "TIT2" in result.fields_written


def test_existing_metadata_and_artwork_are_preserved(tmp_path, monkeypatch, lyrics_on):
    _stub_search(monkeypatch, LYRICS)
    path = _write_mp3(tmp_path)
    cover = b"\xff\xd8\xffcover-bytes"
    tags = mutagen_id3.ID3()
    tags["TCOM"] = mutagen_id3.TCOM(encoding=3, text=["Noel Gallagher"])
    tags["APIC"] = mutagen_id3.APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover)
    tags.save(str(path))

    result = enrich_metadata(path, TRACK, tmp_path)

    assert "USLT" in result.fields_written
    reread = mutagen_id3.ID3(str(path))
    assert reread["TCOM"].text == ["Noel Gallagher"]
    assert reread["TIT2"].text == ["Wonderwall"]
    assert [frame.data for frame in reread.getall("APIC")] == [cover]


def test_lyrics_disabled_skips_retrieval_entirely(tmp_path, monkeypatch, lyrics_off):
    """Fast mode disables lyrics, so no lyrics request is made at all."""
    calls = _stub_search(monkeypatch, LYRICS)
    path = _write_mp3(tmp_path)

    result = enrich_metadata(path, TRACK, tmp_path)

    assert calls == []
    assert "USLT" not in result.fields_written
    assert _uslt_texts(path) == []


def test_lyrics_failure_does_not_fail_the_download(tmp_path, monkeypatch, lyrics_on):
    _stub_search(monkeypatch, requests.ConnectionError("lyrics provider unreachable"))
    downloaded = tmp_path / "output" / "song.mp3"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"fake-mp3-data")
    monkeypatch.setattr(
        "spotm3u.resolution.validate_downloaded_audio",
        lambda track, path: type("Validation", (), {"status": "valid", "reasons": ()})(),
    )

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
                ),
            )

    result = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=Searcher(),
        downloader=lambda track, url, output: downloaded,
    ).resolve(Track("Song", ["Artist"], duration_ms=200_000))

    assert result.status == "downloaded"
    assert result.successful
    assert _uslt_texts(downloaded) == []


def test_fetch_lyrics_returns_none_when_disabled(monkeypatch, lyrics_off):
    calls = _stub_search(monkeypatch, LYRICS)

    assert fetch_lyrics(TRACK) is None
    assert calls == []


def test_lyrics_search_term_prefers_artist_and_falls_back_to_album():
    assert lyrics_search_term(TRACK) == "Wonderwall Oasis"
    assert lyrics_search_term(Track("Song", ["A", "B"], album_artist="Album Artist")) == (
        "Song Album Artist"
    )
    assert lyrics_search_term(Track("Song", [], album="Album")) == "Song Album"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"  {LYRICS}  ", LYRICS),
        ("line\x00one", "lineone"),
        ("", None),
        ("\n \t", None),
        (None, None),
        (["not", "text"], None),
    ],
)
def test_normalize_lyrics(raw, expected):
    assert normalize_lyrics(raw) == expected
