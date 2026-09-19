"""Tests for metadata enrichment module."""

from pathlib import Path

import pytest

from spotm3u.metadata import (
    MetadataResult,
    _artist_album_match,
    _cache_key,
    _embed_artwork,
    _find_album_artwork,
    _normalize_album_for_search,
    _write_all_metadata,
    enrich_metadata,
)
from spotm3u.models import Track


def _install_fake_requests(monkeypatch, response_data=None, status_code=200):
    """Mock requests.get for artwork lookup."""
    if response_data is None:
        response_data = {"release-groups": [{"id": "test-rg-id"}]}

    class FakeResponse:
        def __init__(self, data, code=200):
            self._data = data
            self.status_code = code
            self.headers = {"Content-Type": "image/jpeg"}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

        def json(self):
            return self._data

        @property
        def content(self):
            return b"fake-image-data"

    def fake_get(url, params=None, headers=None, timeout=None):
        if "musicbrainz.org" in url:
            return FakeResponse(response_data)
        if "coverartarchive.org" in url:
            return FakeResponse(
                {
                    "images": [
                        {
                            "front": True,
                            "image": "http://example.com/art.jpg",
                            "types": ["image/jpeg"],
                            "width": 500,
                            "height": 500,
                        }
                    ]
                }
            )
        return FakeResponse({"content": b"fake-image-data"}, 200)

    monkeypatch.setattr("spotm3u.metadata.requests.get", fake_get)


def _mock_id3_operations(monkeypatch):
    """Mock mutagen ID3 operations for testing."""
    saved_tags = {}

    class MockFrame:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class MockTags:
        def __init__(self, *args, **kwargs):
            self._frames = {}
            self._filepath = args[0] if args else None

        def __setitem__(self, key, value):
            self._frames[key] = value

        def __getitem__(self, key):
            return self._frames[key]

        def getall(self, key):
            return [v for k, v in self._frames.items() if k == key]

        def save(self, path, v2_version=3):
            saved_tags[path] = self._frames.copy()

    def mock_id3_init(path):
        if path in saved_tags:
            tags = MockTags(path)
            tags._frames = saved_tags[path].copy()
            return tags
        return MockTags(path)

    def mock_id3_no_header_error(*args, **kwargs):
        raise Exception("ID3NoHeaderError")

    # Patch at mutagen.id3 level since imports happen inside functions at runtime
    import mutagen.id3 as mutagen_id3

    monkeypatch.setattr(mutagen_id3, "ID3", mock_id3_init)
    monkeypatch.setattr(mutagen_id3, "ID3NoHeaderError", mock_id3_no_header_error)
    monkeypatch.setattr(mutagen_id3, "TIT2", lambda **kw: MockFrame(**kw))
    monkeypatch.setattr(mutagen_id3, "TPE1", lambda **kw: MockFrame(**kw))
    monkeypatch.setattr(mutagen_id3, "TALB", lambda **kw: MockFrame(**kw))
    monkeypatch.setattr(mutagen_id3, "TPE2", lambda **kw: MockFrame(**kw))
    monkeypatch.setattr(mutagen_id3, "TRCK", lambda **kw: MockFrame(**kw))
    monkeypatch.setattr(mutagen_id3, "TPOS", lambda **kw: MockFrame(**kw))
    monkeypatch.setattr(mutagen_id3, "APIC", lambda **kw: MockFrame(**kw))

    return saved_tags


def test_cache_key_generation():
    """Test that cache keys are stable and normalized."""
    key1 = _cache_key("The Beatles", "Abbey Road")
    key2 = _cache_key("the beatles", "abbey road")
    key3 = _cache_key("The  Beatles", "Abbey  Road")

    assert key1 == key2 == key3
    assert "beatles" in key1
    assert "abbey" in key1
    assert "road" in key1


def test_normalize_album_for_search():
    """Test album title normalization for search."""
    assert _normalize_album_for_search("Abbey Road") == "Abbey Road"
    assert _normalize_album_for_search("Abbey Road (Remastered)") == "Abbey Road"
    assert _normalize_album_for_search("Abbey Road [Deluxe Edition]") == "Abbey Road"
    assert _normalize_album_for_search("  Abbey Road  ") == "Abbey Road"


def test_artist_album_matching_ignores_unicode_punctuation_and_versions():
    assert _artist_album_match(
        "Beyoncé",
        "Renaissance (Deluxe Edition)",
        "Beyonce",
        "Renaissance",
    )
    assert _artist_album_match(
        "Dua Lipa",
        "Future Nostalgia",
        "Dua Lipa feat. Madonna",
        "Future Nostalgia",
    )


def test_itunes_fallback_is_used_when_musicbrainz_has_no_cover(tmp_path, monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        content = b"itunes-image"

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "artistName": "Dua Lipa",
                        "collectionName": "Future Nostalgia",
                        "trackName": "Levitating",
                        "artworkUrl100": "https://images.example/100x100bb.jpg",
                    }
                ]
            }

    def fake_get(url, **kwargs):
        if "musicbrainz.org" in url:
            return FakeResponse()
        if "itunes.apple.com" in url:
            return FakeResponse()
        image = FakeResponse()
        image.headers = {"Content-Type": "image/jpeg"}
        image.content = b"itunes-image"
        return image

    monkeypatch.setattr("spotm3u.metadata.requests.get", fake_get)
    data, source = _find_album_artwork(tmp_path, "Dua Lipa", "Future Nostalgia", "Levitating")

    assert data == b"itunes-image"
    assert source == "itunes"
    assert not list((tmp_path / "artwork_cache").glob("*.failed"))


def test_stale_negative_cache_does_not_block_retry(tmp_path, monkeypatch):
    cache = tmp_path / "artwork_cache"
    cache.mkdir()
    marker = cache / "dua_lipa_future_nostalgia.failed"
    marker.write_text("")
    _install_fake_requests(monkeypatch)

    data, source = _find_album_artwork(tmp_path, "Dua Lipa", "Future Nostalgia", "Levitating")

    assert data == b"fake-image-data"
    assert source == "coverartarchive"
    assert not marker.exists()


def test_write_all_metadata_basic(monkeypatch):
    """Test basic ID3 metadata writing."""
    _mock_id3_operations(monkeypatch)

    track = Track(
        title="Wonderwall",
        artists=["Oasis"],
        album="(What's the Story) Morning Glory?",
    )

    written = _write_all_metadata(Path("/fake/path.mp3"), track)

    assert "TIT2" in written
    assert "TPE1" in written
    assert "TALB" in written
    assert "TPE2" in written
    assert "TRCK" in written
    assert "TPOS" in written


def test_embed_artwork(monkeypatch):
    """Test artwork embedding."""
    _mock_id3_operations(monkeypatch)

    result = _embed_artwork(Path("/fake/path.mp3"), b"fake-image", "image/jpeg")

    assert result is True


def test_find_album_artwork_cache_miss_then_hit(tmp_path, monkeypatch):
    """Test artwork lookup with cache miss then hit."""
    _install_fake_requests(monkeypatch)

    # First call - cache miss, should download
    data1, source1 = _find_album_artwork(tmp_path, "Oasis", "(What's the Story) Morning Glory?")
    assert data1 == b"fake-image-data"
    assert source1 == "coverartarchive"

    # Second call - should use cache
    _install_fake_requests(monkeypatch, response_data={"release-groups": []})
    data2, source2 = _find_album_artwork(tmp_path, "Oasis", "(What's the Story) Morning Glory?")
    assert data2 == b"fake-image-data"
    assert source2 == "cache"


def test_find_album_artwork_not_found(tmp_path, monkeypatch):
    """Test artwork lookup when not found."""
    _install_fake_requests(monkeypatch, response_data={"release-groups": []})

    data, source = _find_album_artwork(tmp_path, "Unknown", "Unknown Album")

    assert data is None
    assert source == "not-found"


def test_enrich_metadata_basic_id3(tmp_path, monkeypatch):
    """Test basic ID3 metadata writing via enrich_metadata."""
    _mock_id3_operations(monkeypatch)
    _install_fake_requests(monkeypatch)

    track = Track(
        title="Wonderwall",
        artists=["Oasis"],
        album="(What's the Story) Morning Glory?",
        spotify_id="track-1",
    )

    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3_path, track, tmp_path)

    assert isinstance(result, MetadataResult)
    assert "TIT2" in result.fields_written
    assert "TPE1" in result.fields_written
    assert "TALB" in result.fields_written
    assert "TPE2" in result.fields_written
    assert result.artwork_embedded is True


def test_enrich_metadata_preserves_featured_artists(tmp_path, monkeypatch):
    """Test that featured artists are preserved in track artist."""
    _mock_id3_operations(monkeypatch)
    _install_fake_requests(monkeypatch)

    track = Track(
        title="Song",
        artists=["Main Artist", "Featured Artist"],
        album="Album",
    )

    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3_path, track, tmp_path)

    assert "TPE1" in result.fields_written


def test_metadata_writes_collaborating_artists_as_multiple_id3_values(monkeypatch):
    saved_tags = _mock_id3_operations(monkeypatch)
    track = Track("Song", ["Jay Chou", "Gary Yang"], album="Album")

    _write_all_metadata(Path("/fake/path.mp3"), track)

    assert saved_tags["/fake/path.mp3"]["TPE1"].text == ["Jay Chou", "Gary Yang"]


def test_enrich_metadata_missing_album_artist(tmp_path, monkeypatch):
    """Test handling of missing album/artist for artwork."""
    _mock_id3_operations(monkeypatch)

    track = Track(title="Track", artists=[], album=None)

    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3_path, track, tmp_path)

    assert result.artwork_embedded is False
    assert "missing album/artist for artwork lookup" in result.errors


def test_enrich_metadata_artwork_not_found_handled_gracefully(tmp_path, monkeypatch):
    """Test that missing artwork doesn't fail the track."""
    _mock_id3_operations(monkeypatch)
    _install_fake_requests(monkeypatch, response_data={"release-groups": []})

    track = Track(
        title="Track",
        artists=["Unknown Artist"],
        album="Unknown Album",
    )

    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3_path, track, tmp_path)

    assert result.artwork_embedded is False
    assert "artwork not found" in result.errors[0]
    assert "TIT2" in result.fields_written
    assert "TPE1" in result.fields_written


def test_enrich_metadata_batch(tmp_path, monkeypatch):
    """Test batch enrichment reuses artwork cache."""
    _mock_id3_operations(monkeypatch)
    _install_fake_requests(monkeypatch)

    tracks = [Track(title=f"Track {i}", artists=["Artist"], album="Album") for i in range(3)]
    paths = [tmp_path / f"track{i}.mp3" for i in range(3)]

    for p in paths:
        p.write_bytes(b"fake-mp3-data")

    results = enrich_metadata(tracks, paths, tmp_path)

    for r in results:
        assert "TIT2" in r.fields_written


def test_enrich_metadata_failure_returns_result_not_raise(tmp_path):
    """Test that failures return MetadataResult with errors, don't raise."""
    track = Track(title="Track", artists=["Artist"], album="Album")
    nonexistent = tmp_path / "nonexistent.mp3"

    result = enrich_metadata(nonexistent, track, tmp_path)

    assert isinstance(result, MetadataResult)
    assert not result.artwork_embedded
    assert "audio file not found" in result.errors[0]


def test_enrich_metadata_error_handling(tmp_path, monkeypatch):
    """Test that exceptions during metadata write are caught and returned as errors."""

    def failing_id3(*args, **kwargs):
        raise RuntimeError("Simulated failure")

    import spotm3u.metadata as metadata_module

    monkeypatch.setattr(metadata_module, "ID3", failing_id3)
    # Keep the artwork lookup offline; otherwise this test makes real
    # MusicBrainz/Cover Art Archive requests and blocks on the socket timeout.
    _install_fake_requests(monkeypatch)

    track = Track(title="Track", artists=["Artist"], album="Album")
    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3_path, track, tmp_path)

    assert isinstance(result, MetadataResult)
    assert "metadata write failed" in result.errors[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
