"""Tests for the experimental Apple Music catalog matching (task 93).

The resolver is opt-in and default-off, and its matching is deliberately
conservative: a local track is associated with a catalog id only on strong,
multi-field evidence. These tests pin the accepted and rejected cases with
mocked iTunes responses, so they need no network access.
"""

from __future__ import annotations

import types

import pytest
import requests

from spotm3u import apple_music
from spotm3u.apple_music import CatalogTrack, find_catalog_track
from spotm3u.metadata import enrich_metadata
from spotm3u.models import Track


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _reset_apple_music():
    """Run each test with a clean rng/cache and the feature restored to off."""
    apple_music._CATALOG_CACHE = apple_music.MetadataCache()
    apple_music.set_apple_catalog_id_enabled(False)
    yield
    apple_music.set_apple_catalog_id_enabled(False)


@pytest.fixture
def catalog_enabled():
    apple_music.set_apple_catalog_id_enabled(True)
    yield
    apple_music.set_apple_catalog_id_enabled(False)


def _install(monkeypatch, results, *, calls=None):
    """Stub the Apple Music module's HTTP client, recording its lookups.

    ``apple_music`` shares the ``requests`` module with artwork/MusicBrainz
    enrichment (and the artwork providers query the very same iTunes endpoint),
    so the stub replaces the module's own ``requests`` reference. ``calls`` then
    answers "did the Apple Music catalog lookup run?" and nothing else.
    """
    calls = calls if calls is not None else []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params or {})
        return _FakeResponse({"resultCount": len(results), "results": list(results)})

    monkeypatch.setattr(
        "spotm3u.apple_music.requests",
        types.SimpleNamespace(get=fake_get, RequestException=requests.RequestException),
    )
    return calls


def _song(
    *,
    track_id=123456,
    title="Song",
    artist="Artist",
    album="Album",
    duration_ms=210_000,
):
    return {
        "trackId": track_id,
        "trackName": title,
        "artistName": artist,
        "collectionName": album,
        "trackTimeMillis": duration_ms,
    }


def test_correct_track_resolves_to_its_catalog_id(monkeypatch, catalog_enabled) -> None:
    _install(monkeypatch, [_song()])
    track = Track("Song", ["Artist"], album="Album", duration_ms=210_000)

    match = find_catalog_track(track)

    assert match == CatalogTrack(
        catalog_id=123456, title="Song", artist="Artist", album="Album", duration_ms=210_000
    )


def test_metadata_noise_and_case_do_not_prevent_a_match(monkeypatch, catalog_enabled) -> None:
    _install(monkeypatch, [_song(title="SONG (Remastered 2011)")])
    track = Track("song", ["ARTIST"], album="album", duration_ms=210_000)

    assert find_catalog_track(track) is not None


def test_same_title_by_a_different_artist_is_rejected(monkeypatch, catalog_enabled) -> None:
    _install(monkeypatch, [_song(artist="Some Cover Band")])
    track = Track("Song", ["Artist"], album="Album", duration_ms=210_000)

    assert find_catalog_track(track) is None


def test_wrong_version_is_rejected(monkeypatch, catalog_enabled) -> None:
    """A plain request must not be matched to a live/remix catalog track."""
    _install(monkeypatch, [_song(title="Song (Live)", track_id=1)])
    track = Track("Song", ["Artist"], album="Album", duration_ms=210_000)

    assert find_catalog_track(track) is None


def test_a_requested_version_matches_its_catalog_counterpart(monkeypatch, catalog_enabled) -> None:
    _install(monkeypatch, [_song(title="Song (Live at Wembley)", track_id=99)])
    track = Track("Song (Live)", ["Artist"], album="Album", duration_ms=210_000)

    match = find_catalog_track(track)

    assert match is not None and match.catalog_id == 99


def test_version_word_opening_the_title_is_not_matched_loosely(
    monkeypatch, catalog_enabled
) -> None:
    """A title that *starts* with a version word has no base left to compare."""
    _install(monkeypatch, [_song(title="Live and Let Die")])
    track = Track("Live to Tell", ["Artist"], album="Album", duration_ms=210_000)

    assert find_catalog_track(track) is None


def test_duration_mismatch_is_rejected(monkeypatch, catalog_enabled) -> None:
    _install(monkeypatch, [_song(duration_ms=420_000)])
    track = Track("Song", ["Artist"], album="Album", duration_ms=210_000)

    assert find_catalog_track(track) is None


def test_ambiguous_candidates_are_not_guessed_between(monkeypatch, catalog_enabled) -> None:
    _install(monkeypatch, [_song(track_id=1), _song(track_id=2)])
    track = Track("Song", ["Artist"], duration_ms=210_000)

    assert find_catalog_track(track) is None


def test_album_selects_between_two_catalog_tracks(monkeypatch, catalog_enabled) -> None:
    _install(
        monkeypatch,
        [
            _song(track_id=1, album="Other Album"),
            _song(track_id=2, album="Album"),
        ],
    )
    track = Track("Song", ["Artist"], album="Album", duration_ms=210_000)

    match = find_catalog_track(track)

    assert match is not None and match.catalog_id == 2


def test_lookup_is_off_by_default(monkeypatch) -> None:
    calls = _install(monkeypatch, [_song()])
    track = Track("Song", ["Artist"], album="Album", duration_ms=210_000)

    assert find_catalog_track(track) is None
    assert calls == []


def test_network_failure_is_non_fatal(monkeypatch, catalog_enabled) -> None:
    def unreachable(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr("spotm3u.apple_music.requests.get", unreachable)
    track = Track("Song", ["Artist"], album="Album", duration_ms=210_000)

    assert find_catalog_track(track) is None


def _write_mp3(path) -> None:
    path.write_bytes(b"fake-mp3-data")


def test_enrich_metadata_writes_catalog_id_when_enabled(
    tmp_path, monkeypatch, catalog_enabled
) -> None:
    import mutagen.id3 as mutagen_id3

    _install(monkeypatch, [_song()])
    mp3 = tmp_path / "track.mp3"
    _write_mp3(mp3)
    track = Track("Song", ["Artist"], album="Album", duration_ms=210_000)

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.catalog_id == 123456
    assert "TXXX:ITUNESCATALOGID" in result.fields_written
    tags = mutagen_id3.ID3(str(mp3))
    assert str(tags["TXXX:ITUNESCATALOGID"].text[0]) == "123456"


def test_enrich_metadata_writes_no_catalog_id_when_disabled(tmp_path, monkeypatch) -> None:
    calls = _install(monkeypatch, [_song()])
    mp3 = tmp_path / "track.mp3"
    _write_mp3(mp3)
    track = Track("Song", ["Artist"], album="Album", duration_ms=210_000)

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.catalog_id is None
    assert "TXXX:ITUNESCATALOGID" not in result.fields_written
    assert calls == []


def test_config_defaults_off_and_parses(tmp_path) -> None:
    from spotm3u.config import load_config

    assert load_config(tmp_path / "missing.toml").apple_catalog_id is False

    path = tmp_path / "config.toml"
    path.write_text("[apple_music]\ncatalog_id = true\n")
    assert load_config(path).apple_catalog_id is True
