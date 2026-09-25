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
    Lyrics,
    fetch_lyrics,
    is_synced_lyrics,
    lyrics_search_term,
    normalize_lyrics,
    parse_lyrics,
    set_lyrics_enabled,
)
from spotm3u.metadata import MetadataResult, embedded_lyrics_form, enrich_metadata
from spotm3u.models import Track
from spotm3u.online import SourceCandidate
from spotm3u.resolution import TrackResolver

LYRICS = "Today is gonna be the day\nThat they're gonna throw it back to you"
SYNCED_LYRICS = (
    "[00:06.21] Today is gonna be the day\n[00:11.00] That they're gonna throw it back to you"
)
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


def _sylt_pairs(path: Path) -> list[list[tuple[str, int]]]:
    return [frame.text for frame in mutagen_id3.ID3(str(path)).getall("SYLT")]


def test_lyrics_are_written_to_the_standard_lyrics_field(tmp_path, monkeypatch, lyrics_on):
    """Plain lyrics go into the field and get no sidecar, which would be untimed."""
    calls = _stub_search(monkeypatch, LYRICS)
    path = _write_mp3(tmp_path)

    result = enrich_metadata(path, TRACK, tmp_path)

    assert "USLT" in result.fields_written
    assert _uslt_texts(path) == [LYRICS]
    assert not (tmp_path / "track.lrc").exists()
    # One lookup for "[title] [artist]", asking the library for its default
    # (prefer synced, fall back to plain) rather than plain-only.
    assert calls == [("Wonderwall Oasis", {})]


@pytest.mark.parametrize(
    ("audio_name", "sidecar_name"),
    [("track.mp3", "track.lrc"), ("song.flac", "song.lrc"), ("a.b.m4a", "a.b.lrc")],
)
def test_synced_lyrics_are_written_to_both_frame_types_and_a_sidecar(
    tmp_path, monkeypatch, lyrics_on, audio_name, sidecar_name
):
    """Timed lyrics produce a clean USLT, a timed SYLT and an .lrc sidecar."""
    _stub_search(monkeypatch, SYNCED_LYRICS)
    path = _write_mp3(tmp_path, audio_name)

    result = enrich_metadata(path, TRACK, tmp_path)

    assert "USLT" in result.fields_written
    assert "SYLT" in result.fields_written
    assert _uslt_texts(path) == [LYRICS]
    assert _sylt_pairs(path) == [
        [("Today is gonna be the day", 6210), ("That they're gonna throw it back to you", 11000)]
    ]
    sidecar = tmp_path / sidecar_name
    assert sidecar.read_text(encoding="utf-8") == SYNCED_LYRICS


def test_uslt_never_contains_timestamps(tmp_path, monkeypatch, lyrics_on):
    """The plain lyrics field is clean text, even when the source was LRC."""
    _stub_search(monkeypatch, "[00:12.00][01:20.00]Repeated chorus\n[00:24.22] More lyrics")
    path = _write_mp3(tmp_path)

    enrich_metadata(path, TRACK, tmp_path)

    assert _uslt_texts(path) == ["Repeated chorus\nMore lyrics"]
    assert "[00:" not in _uslt_texts(path)[0]


def test_sylt_keeps_the_timestamps_in_milliseconds(tmp_path, monkeypatch, lyrics_on):
    """SYLT carries absolute timestamps, so timed players can use them."""
    _stub_search(monkeypatch, "[01:20.00] Hello\n[02:30.25] World")
    path = _write_mp3(tmp_path)

    enrich_metadata(path, TRACK, tmp_path)

    assert _sylt_pairs(path) == [[("Hello", 80000), ("World", 150250)]]


def test_multiple_timestamps_on_one_line_give_one_pair_each(tmp_path, monkeypatch, lyrics_on):
    """``[00:12.00][01:20.00]Repeated chorus`` yields both times, one USLT line."""
    _stub_search(monkeypatch, "[00:12.00][01:20.00]Repeated chorus")
    path = _write_mp3(tmp_path)

    enrich_metadata(path, TRACK, tmp_path)

    assert _uslt_texts(path) == ["Repeated chorus"]
    assert _sylt_pairs(path) == [[("Repeated chorus", 12000), ("Repeated chorus", 80000)]]


def test_missing_lyrics_leaves_the_lyrics_field_empty(tmp_path, monkeypatch, lyrics_on):
    _stub_search(monkeypatch, None)
    path = _write_mp3(tmp_path)

    result = enrich_metadata(path, TRACK, tmp_path)

    assert "USLT" not in result.fields_written
    assert _uslt_texts(path) == []
    assert not (tmp_path / "track.lrc").exists()
    assert "TIT2" in result.fields_written


@pytest.mark.parametrize("unusable", ["", "   ", 42, {"lyrics": LYRICS}, b"bytes"])
def test_unusable_lyrics_result_is_ignored(tmp_path, monkeypatch, lyrics_on, unusable):
    _stub_search(monkeypatch, unusable)
    path = _write_mp3(tmp_path)

    result = enrich_metadata(path, TRACK, tmp_path)

    assert isinstance(result, MetadataResult)
    assert "USLT" not in result.fields_written
    assert _uslt_texts(path) == []
    assert not (tmp_path / "track.lrc").exists()


def test_unwritable_sidecar_still_keeps_the_embedded_lyrics(tmp_path, monkeypatch, lyrics_on):
    """A blocked sidecar path is contained: the lyrics frames are already written."""
    _stub_search(monkeypatch, SYNCED_LYRICS)
    path = _write_mp3(tmp_path)
    # A directory where the .lrc file would go makes the write fail.
    (tmp_path / "track.lrc").mkdir()

    result = enrich_metadata(path, TRACK, tmp_path)

    assert "USLT" in result.fields_written
    assert "SYLT" in result.fields_written
    assert _uslt_texts(path) == [LYRICS]
    assert _sylt_pairs(path)
    assert "TIT2" in result.fields_written


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


def test_embedded_lyrics_form_reports_what_the_file_holds(tmp_path, monkeypatch, lyrics_on):
    """The reader answers from the file, not from what a run meant to write."""
    _stub_search(monkeypatch, SYNCED_LYRICS)
    synced_path = _write_mp3(tmp_path, "synced.mp3")
    enrich_metadata(synced_path, TRACK, tmp_path)

    _stub_search(monkeypatch, LYRICS)
    plain_path = _write_mp3(tmp_path, "plain.mp3")
    enrich_metadata(plain_path, TRACK, tmp_path)

    untagged = _write_mp3(tmp_path, "untagged.mp3")

    assert embedded_lyrics_form(synced_path) == "synced"
    assert embedded_lyrics_form(plain_path) == "plain"
    assert embedded_lyrics_form(untagged) is None
    assert embedded_lyrics_form(tmp_path / "missing.mp3") is None


def test_embedded_lyrics_form_ignores_an_empty_frame(tmp_path):
    """An empty lyrics frame is no lyrics, not plain lyrics."""
    path = _write_mp3(tmp_path)
    tags = mutagen_id3.ID3()
    tags["USLT"] = mutagen_id3.USLT(encoding=3, lang="eng", desc="", text="   \n")
    tags.save(str(path))

    assert embedded_lyrics_form(path) is None


def test_embedded_lyrics_form_looks_at_sylt_not_uslt_timestamps(tmp_path, monkeypatch, lyrics_on):
    """A clean USLT with an SYLT frame reads as synced; USLT alone reads as plain."""
    synced = tmp_path / "synced.mp3"
    _stub_search(monkeypatch, SYNCED_LYRICS)
    _write_mp3(tmp_path, "synced.mp3")
    enrich_metadata(synced, TRACK, tmp_path)
    assert embedded_lyrics_form(synced) == "synced"

    plain = tmp_path / "plain.mp3"
    _stub_search(monkeypatch, LYRICS)
    _write_mp3(tmp_path, "plain.mp3")
    enrich_metadata(plain, TRACK, tmp_path)
    assert embedded_lyrics_form(plain) == "plain"

    legacy = tmp_path / "legacy.mp3"
    _write_mp3(tmp_path, "legacy.mp3")
    tags = mutagen_id3.ID3()
    tags["USLT"] = mutagen_id3.USLT(encoding=3, lang="eng", desc="", text=SYNCED_LYRICS)
    tags.save(str(legacy))
    assert embedded_lyrics_form(legacy) == "synced"


def test_lyrics_disabled_skips_retrieval_entirely(tmp_path, monkeypatch, lyrics_off):
    """Fast mode disables lyrics, so no lyrics request is made at all."""
    calls = _stub_search(monkeypatch, LYRICS)
    path = _write_mp3(tmp_path)

    result = enrich_metadata(path, TRACK, tmp_path)

    assert calls == []
    assert "USLT" not in result.fields_written
    assert _uslt_texts(path) == []
    assert not (tmp_path / "track.lrc").exists()


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


@pytest.mark.parametrize(
    ("lyrics", "expected"),
    [
        (SYNCED_LYRICS, True),
        ("[00:06.21]Hello", True),
        ("[01:20]Hello", True),
        ("[00:12.00][01:20.00]Repeated chorus", True),
        (LYRICS, False),
        ("[ar:Oasis]\n[ti:Wonderwall]", False),
        ("[Verse 1]\nToday is gonna be the day", False),
        ("", False),
    ],
)
def test_is_synced_lyrics(lyrics, expected):
    assert is_synced_lyrics(lyrics) is expected


def test_parse_lyrics_keeps_plain_text_plain():
    parsed = parse_lyrics(LYRICS)

    assert isinstance(parsed, Lyrics)
    assert parsed.text == LYRICS
    assert parsed.lines == ()
    assert parsed.synced is False


def test_parse_lyrics_splits_lrc_into_structured_pairs():
    parsed = parse_lyrics(SYNCED_LYRICS)

    assert parsed is not None
    assert parsed.text == LYRICS
    assert parsed.lines == (
        (6.21, "Today is gonna be the day"),
        (11.0, "That they're gonna throw it back to you"),
    )
    assert parsed.synced is True


def test_parse_lyrics_drops_lrc_metadata_tags():
    parsed = parse_lyrics(
        "[ar:Oasis]\n[ti:Wonderwall]\n[00:06.21]Today is gonna be the day\n[00:11.00] That "
        "they're gonna throw it back to you"
    )

    assert parsed is not None
    assert parsed.text == LYRICS
    assert parsed.lines == (
        (6.21, "Today is gonna be the day"),
        (11.0, "That they're gonna throw it back to you"),
    )


def test_parse_lyrics_handles_multiple_timestamps_on_one_line():
    parsed = parse_lyrics("[00:12.00][01:20.00]Repeated chorus")

    assert parsed is not None
    assert parsed.text == "Repeated chorus"
    assert parsed.lines == (
        (12.0, "Repeated chorus"),
        (80.0, "Repeated chorus"),
    )


@pytest.mark.parametrize("unusable", ["", "   ", 42, {"lyrics": LYRICS}, b"bytes", None])
def test_parse_lyrics_rejects_unusable_input(unusable):
    assert parse_lyrics(unusable) is None


def test_parse_lyrics_rejects_lrc_without_any_lyric_text():
    """Timestamps timing nothing are not lyrics."""
    assert parse_lyrics("[00:06.21]\n[00:11.00]") is None
