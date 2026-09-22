"""Tests for metadata enrichment module."""

from pathlib import Path

import pytest
import requests

from spotm3u.metadata import (
    MetadataResult,
    _artist_album_match,
    _cache_key,
    _embed_artwork,
    _find_album_artwork,
    _normalize_album_for_search,
    _write_all_metadata,
    artwork_artist,
    cached_artwork_path,
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


def _song_search_requests(monkeypatch, results):
    """Patch requests so iTunes song search returns ``results`` and nothing else."""

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        content = b""

        def raise_for_status(self):
            return None

        def json(self):
            return {"results": results}

    def fake_get(url, **kwargs):
        if "musicbrainz.org" in url:
            return FakeResponse()
        if "itunes.apple.com" in url:
            return FakeResponse()
        image = FakeResponse()
        image.headers = {"Content-Type": "image/jpeg"}
        image.content = b"song-image"
        return image

    monkeypatch.setattr("spotm3u.metadata.requests.get", fake_get)


def test_song_fallback_is_used_when_album_is_missing(tmp_path, monkeypatch):
    _song_search_requests(
        monkeypatch,
        [
            {
                "artistName": "Artist",
                "trackName": "Home",
                "collectionName": "An Album",
                "artworkUrl100": "https://images.example/100x100bb.jpg",
            }
        ],
    )

    data, source = _find_album_artwork(tmp_path, "Artist", "", "Home")

    assert data == b"song-image"
    assert source == "itunes-song"
    assert (tmp_path / "artwork_cache" / "artist_home.jpg").is_file()


def test_song_fallback_rejects_other_artists_common_titles(tmp_path, monkeypatch):
    _song_search_requests(
        monkeypatch,
        [
            {
                "artistName": "Another Singer",
                "trackName": "Home",
                "collectionName": "Other Album",
                "artworkUrl100": "https://images.example/100x100bb.jpg",
            }
        ],
    )

    data, source = _find_album_artwork(tmp_path, "Artist", "", "Home")

    assert data is None
    assert source == "not-found"


def test_album_lookup_failure_falls_back_to_song_artwork(tmp_path, monkeypatch):
    _song_search_requests(
        monkeypatch,
        [
            {
                "artistName": "Artist",
                "trackName": "Home",
                "collectionName": "Different Collection",
                "artworkUrl100": "https://images.example/100x100bb.jpg",
            }
        ],
    )

    data, source = _find_album_artwork(tmp_path, "Artist", "Not the actual album", "Home")

    assert data == b"song-image"
    assert source == "itunes-song"


def test_album_identity_is_primary_for_artwork(tmp_path, monkeypatch):
    _install_fake_requests(monkeypatch)

    data, source = _find_album_artwork(tmp_path, "Artist", "Album", "Different Song")

    assert data == b"fake-image-data"
    assert source == "coverartarchive"


def test_release_artwork_must_match_artist_and_album(tmp_path, monkeypatch):
    """A same-titled album by the wrong artist must not be used as artwork."""

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        content = b""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "release-groups": [
                    {
                        "id": "wrong-artist-rg",
                        "title": "Future Nostalgia",
                        "artist-credit-phrase": "U2",
                    }
                ]
            }

    def fake_get(url, **kwargs):
        if "musicbrainz.org" in url:
            return FakeResponse()
        if "coverartarchive.org" in url:
            return FakeResponse()
        return FakeResponse()

    monkeypatch.setattr("spotm3u.metadata.requests.get", fake_get)
    data, source = _find_album_artwork(tmp_path, "Dua Lipa", "Future Nostalgia", "Levitating")

    assert data is None
    assert source == "not-found"


def test_external_api_failure_is_graceful(tmp_path, monkeypatch):
    def unreachable(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("spotm3u.metadata.requests.get", unreachable)

    data, source = _find_album_artwork(tmp_path, "Artist", "", "Home")

    assert data is None
    assert source == "not-found"


def test_malformed_api_response_is_graceful(tmp_path, monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "image/jpeg"}
        content = b""

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("malformed body")

    def fake_get(url, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("spotm3u.metadata.requests.get", fake_get)

    data, source = _find_album_artwork(tmp_path, "Artist", "Album", "Home")

    assert data is None
    assert source == "not-found"


def test_multiple_artists_never_form_one_lookup_string():
    track = Track("Home", ["Jackson W.", "Jackson Z."], album="Album", album_artist="Jackson Z.")
    assert artwork_artist(track) == "Jackson Z."

    collab = Track("Home", ["Jackson W.", "Jackson Z."])
    assert artwork_artist(collab) == "Jackson W."

    key = _cache_key(artwork_artist(collab) or "", collab.album or "", collab.title)
    assert "jackson_w" in key
    assert "jacksonz" not in key


def test_duplicate_tracks_from_same_album_share_cached_artwork(tmp_path, monkeypatch):
    from spotm3u import metadata

    calls: list[str] = []
    _install_fake_requests(monkeypatch)
    base_get = metadata.requests.get

    def counting_get(url, **kwargs):
        calls.append(url)
        return base_get(url, **kwargs)

    monkeypatch.setattr(metadata.requests, "get", counting_get)

    data1, source1 = _find_album_artwork(
        tmp_path, "Oasis", "(What's the Story) Morning Glory?", "Song 1"
    )
    data2, source2 = _find_album_artwork(
        tmp_path, "Oasis", "(What's the Story) Morning Glory?", "Song 2"
    )

    assert data2 == data1
    assert source2 == "cache"
    assert sum("musicbrainz" in call for call in calls) == 1


def test_concurrent_lookups_share_one_fetch(tmp_path, monkeypatch):
    import threading

    gate = threading.Event()
    started = threading.Event()
    musicbrainz_calls: list[str] = []

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "image/jpeg"}
        content = b"concurrent-image"

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "release-groups": [
                    {
                        "id": "dedup-rg",
                        "title": "Dedup Album",
                        "artist-credit-phrase": "Dedup Artist",
                    }
                ]
            }

    def fake_get(url, **kwargs):
        if "musicbrainz.org" in url:
            musicbrainz_calls.append(url)
            started.set()
            gate.wait(5)
            return FakeResponse()
        if "coverartarchive.org" in url:
            image = FakeResponse()
            image.json = lambda: {
                "images": [
                    {
                        "front": True,
                        "image": "http://example.com/art.jpg",
                        "width": 500,
                        "height": 500,
                    }
                ]
            }
            return image
        image = FakeResponse()
        image.content = b"concurrent-image"
        return image

    monkeypatch.setattr("spotm3u.metadata.requests.get", fake_get)

    results: dict[int, tuple[bytes | None, str | None]] = {}

    def work(index: int) -> None:
        results[index] = _find_album_artwork(
            tmp_path, "Dedup Artist", "Dedup Album", f"Track {index}"
        )

    first = threading.Thread(target=work, args=(0,))
    first.start()
    assert started.wait(5)
    second = threading.Thread(target=work, args=(1,))
    second.start()
    gate.set()
    first.join(10)
    second.join(10)

    assert results[0][0] == results[1][0] == b"concurrent-image"
    assert len(musicbrainz_calls) == 1


def test_cached_artwork_path_returns_file_when_present(tmp_path):
    from spotm3u import metadata

    track = Track("Home", ["Artist"], album="Album")
    assert cached_artwork_path(tmp_path, track) is None

    cache_path = metadata._cached_artwork_path(
        metadata._cache_dir(tmp_path), metadata._cache_key("Artist", "Album", "Home")
    )
    cache_path.write_bytes(b"image-bytes")
    assert cached_artwork_path(tmp_path, track).read_bytes() == b"image-bytes"


def _write_artwork_entry(tmp_path, track, *, source="coverartarchive"):
    """Write a cached release image (and artist image) for one track."""
    from spotm3u import metadata

    artist = artwork_artist(track)
    key = _cache_key(artist or "", track.album or "", track.title)
    metadata._save_cached_artwork(tmp_path, key, b"image-bytes")
    metadata._write_artwork_source(tmp_path, key, source)
    artist_key = metadata._artist_cache_key(artist or "")
    metadata._write_cached_image(metadata._artist_cache_dir(tmp_path), artist_key, b"artist")
    metadata._write_cached_source(metadata._artist_cache_dir(tmp_path), artist_key, "deezer")
    return key, artist_key


def _entry_paths(tmp_path, key, artist_key):
    from spotm3u import metadata

    cache = metadata._cache_dir(tmp_path)
    return (
        metadata._cached_artwork_path(cache, key),
        metadata._artwork_source_path(cache, key),
        metadata._cached_artwork_path(metadata._artist_cache_dir(tmp_path), artist_key),
    )


def test_prune_missing_artwork_removes_the_release_entry(tmp_path):
    from spotm3u.metadata import prune_missing_artwork

    track = Track("Home", ["Artist"], album="Album")
    key, artist_key = _write_artwork_entry(tmp_path, track)
    image, source, artist_image = _entry_paths(tmp_path, key, artist_key)

    removed = prune_missing_artwork(tmp_path, [track])

    assert removed == 4  # the release image and artist image, each with its source marker
    assert not image.exists()
    assert not source.exists()
    assert not artist_image.exists()
    assert cached_artwork_path(tmp_path, track) is None


def test_prune_missing_artwork_keeps_artists_that_still_have_a_track(tmp_path):
    from spotm3u.metadata import prune_missing_artwork

    deleted = Track("Home", ["Artist"], album="Album")
    kept = Track("Other", ["Artist"], album="Another Album")
    deleted_key, deleted_artist_key = _write_artwork_entry(tmp_path, deleted)
    kept_key, kept_artist_key = _write_artwork_entry(tmp_path, kept)
    deleted_image, _deleted_source, artist_image = _entry_paths(
        tmp_path, deleted_key, deleted_artist_key
    )
    kept_image, _kept_source, _kept_artist_image = _entry_paths(tmp_path, kept_key, kept_artist_key)

    removed = prune_missing_artwork(tmp_path, [deleted], keep_artists=["Artist"])

    assert removed == 2  # the release image and its source marker only
    assert not deleted_image.exists()
    assert kept_image.exists()
    assert artist_image.exists()  # shared by the track that is still on disk


def test_prune_missing_artwork_leaves_other_identities_alone(tmp_path):
    from spotm3u.metadata import prune_missing_artwork

    deleted = Track("Home", ["Artist"], album="Album")
    untouched = Track("Song", ["Someone Else"], album="Their Album")
    key, artist_key = _write_artwork_entry(tmp_path, deleted)
    other_key, other_artist_key = _write_artwork_entry(tmp_path, untouched)
    other_image, other_source, other_artist_image = _entry_paths(
        tmp_path, other_key, other_artist_key
    )

    prune_missing_artwork(tmp_path, [deleted], keep_artists=["Someone Else"])

    assert other_image.exists()
    assert other_source.exists()
    assert other_artist_image.exists()


def test_prune_missing_artwork_without_a_cache_entry_removes_nothing(tmp_path):
    from spotm3u.metadata import prune_missing_artwork

    track = Track("Home", ["Artist"], album="Album")

    assert prune_missing_artwork(tmp_path, [track]) == 0
    assert prune_missing_artwork(tmp_path, [Track("No Artist", [])]) == 0


def test_prune_missing_artwork_forgets_the_in_process_memo(tmp_path):
    from spotm3u import metadata
    from spotm3u.metadata import prune_missing_artwork

    track = Track("Home", ["Artist"], album="Album")
    key, _artist_key = _write_artwork_entry(tmp_path, track)
    memory_key = metadata._artwork_memory_key(tmp_path, key)
    metadata._ARTWORK_MEMORY[memory_key] = (b"image-bytes", "cache")

    prune_missing_artwork(tmp_path, [track])

    assert memory_key not in metadata._ARTWORK_MEMORY


def test_enrich_metadata_uses_song_fallback_without_album(tmp_path, monkeypatch):
    _mock_id3_operations(monkeypatch)
    _song_search_requests(
        monkeypatch,
        [
            {
                "artistName": "Artist",
                "trackName": "Home",
                "collectionName": "An Album",
                "artworkUrl100": "https://images.example/100x100bb.jpg",
            }
        ],
    )

    track = Track("Home", ["Artist"], album=None)
    mp3_path = tmp_path / "test.mp3"
    mp3_path.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3_path, track, tmp_path)

    assert result.artwork_embedded is True
    assert result.artwork_source == "itunes-song"


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


def _write_embedded_cover(
    path: Path,
    data: bytes,
    mime: str = "image/jpeg",
    ptype=3,
    append: bool = False,
    desc: str = "Cover",
) -> None:
    """Write an APIC frame into a fresh media file using mutagen."""
    import mutagen.id3 as mutagen_id3

    if append and path.is_file():
        tags = mutagen_id3.ID3(str(path))
    else:
        tags = mutagen_id3.ID3()
    tags.add(mutagen_id3.APIC(encoding=3, mime=mime, type=ptype, desc=desc, data=data))
    tags.save(path, v2_version=3)


def test_verified_release_cover_overrides_local_embedded_art(tmp_path, monkeypatch):
    """A local file's embedded cover must not win over a verified release cover."""
    from spotm3u import metadata

    _install_fake_requests(monkeypatch)
    mp3 = tmp_path / "local.mp3"
    _write_embedded_cover(mp3, b"wrong-local-cover")

    data, source = _find_album_artwork(tmp_path, "Dua Lipa", "Future Nostalgia", "Levitating", mp3)

    assert data == b"fake-image-data"
    assert source == "coverartarchive"
    key = _cache_key("Dua Lipa", "Future Nostalgia", "Levitating")
    cache = tmp_path / "artwork_cache"
    assert (cache / f"{key}.jpg").read_bytes() == b"fake-image-data"
    assert metadata._read_artwork_source(tmp_path, key) == "coverartarchive"


def test_local_embedded_art_is_fallback_when_release_lookup_fails(tmp_path, monkeypatch):
    """Offline, the best local evidence is still embedded instead of leaving the track artless."""
    from spotm3u import metadata

    def unreachable(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("spotm3u.metadata.requests.get", unreachable)
    mp3 = tmp_path / "local.mp3"
    _write_embedded_cover(mp3, b"local-cover")

    data, source = _find_album_artwork(tmp_path, "Dua Lipa", "Future Nostalgia", "Levitating", mp3)

    assert data == b"local-cover"
    assert source == "embedded:image/jpeg"
    key = _cache_key("Dua Lipa", "Future Nostalgia", "Levitating")
    assert metadata._read_artwork_source(tmp_path, key) == "embedded:image/jpeg"


def test_legacy_unmarked_cache_entry_is_reverified(tmp_path, monkeypatch):
    """Old caches written before source tracking may hold a wrong cover; re-verify them."""
    from spotm3u import metadata

    cache = tmp_path / "artwork_cache"
    cache.mkdir()
    key = _cache_key("Dua Lipa", "Future Nostalgia", "Levitating")
    (cache / f"{key}.jpg").write_bytes(b"stale-wrong-cover")
    _install_fake_requests(monkeypatch)

    data, source = _find_album_artwork(tmp_path, "Dua Lipa", "Future Nostalgia", "Levitating")

    assert data == b"fake-image-data"
    assert source == "coverartarchive"
    assert (cache / f"{key}.jpg").read_bytes() == b"fake-image-data"
    assert metadata._read_artwork_source(tmp_path, key) == "coverartarchive"


def test_verified_cache_entry_is_not_refetched(tmp_path, monkeypatch):
    """A cache entry recorded from a verified lookup is authoritative and needs no network."""
    from spotm3u import metadata

    cache = tmp_path / "artwork_cache"
    cache.mkdir()
    key = _cache_key("Dua Lipa", "Future Nostalgia", "Levitating")
    (cache / f"{key}.jpg").write_bytes(b"verified-cover")
    (cache / f"{key}.src").write_text("itunes")
    _install_fake_requests(monkeypatch)
    calls: list[str] = []
    base_get = metadata.requests.get

    def counting_get(url, **kwargs):
        calls.append(url)
        return base_get(url, **kwargs)

    monkeypatch.setattr(metadata.requests, "get", counting_get)

    data, source = _find_album_artwork(tmp_path, "Dua Lipa", "Future Nostalgia", "Levitating")

    assert data == b"verified-cover"
    assert source == "cache"
    assert calls == []


def test_embedded_artwork_rejects_ambiguous_multiple_frames(tmp_path):
    """Multiple APIC frames with none marked as front cover must not be guessed."""
    from spotm3u import metadata

    mp3 = tmp_path / "multi.mp3"
    _write_embedded_cover(mp3, b"back-cover", ptype=4, desc="Back")
    _write_embedded_cover(mp3, b"artist-photo", ptype=8, append=True, desc="Artist")

    assert metadata._embedded_artwork(mp3) is None


def test_embedded_artwork_accepts_single_untyped_frame(tmp_path):
    """A sole APIC frame is treated as the cover even when its type is unset."""
    from spotm3u import metadata

    mp3 = tmp_path / "single.mp3"
    _write_embedded_cover(mp3, b"sole-cover", mime="image/png", ptype=0)

    assert metadata._embedded_artwork(mp3) == (b"sole-cover", "image/png")


def test_embedded_artwork_prefers_largest_front_cover(tmp_path):
    """When several frames are front covers, the highest-resolution one wins."""
    from spotm3u import metadata

    mp3 = tmp_path / "best.mp3"
    _write_embedded_cover(mp3, b"small", ptype=3, desc="Small")
    _write_embedded_cover(mp3, b"large-high-res-cover", ptype=3, append=True, desc="Large")

    assert metadata._embedded_artwork(mp3) == (b"large-high-res-cover", "image/jpeg")


@pytest.fixture
def verify_local_toggle():
    """Run a test with the artwork verification flag, restoring the default after."""
    from spotm3u import metadata

    yield
    metadata.set_artwork_verify_local(True)


def test_verify_local_disabled_uses_embedded_art_without_network(
    tmp_path, monkeypatch, verify_local_toggle
):
    """With verify_local off, a file that already has a cover does no network lookup."""
    from spotm3u import metadata

    metadata.set_artwork_verify_local(False)
    calls: list[str] = []

    def failing_get(url, **kwargs):
        calls.append(url)
        raise AssertionError(f"network must not be consulted: {url}")

    monkeypatch.setattr("spotm3u.metadata.requests.get", failing_get)
    mp3 = tmp_path / "local.mp3"
    _write_embedded_cover(mp3, b"local-cover")

    data, source = _find_album_artwork(tmp_path, "Dua Lipa", "Future Nostalgia", "Levitating", mp3)

    assert data == b"local-cover"
    assert source == "embedded:image/jpeg"
    assert calls == []


class _FakeJsonResponse:
    """Minimal requests-style response for a JSON metadata lookup."""

    status_code = 200
    content = b""

    def __init__(self, payload):
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeImageResponse:
    """Minimal requests-style response for an image download."""

    status_code = 200

    def __init__(self, data):
        self.content = data
        self.headers = {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        return None

    def json(self):
        return {}


def _install_artwork_requests(
    monkeypatch,
    *,
    artist_results=None,
    artist_image=b"artist-image",
    album=True,
    calls=None,
):
    """Mock the release and artist artwork requests used by enrichment."""
    if artist_results is None:
        artist_results = [{"name": "Artist", "picture_xl": "https://cdn.example/artist-1000.jpg"}]
    calls = calls if calls is not None else []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if "api.deezer.com" in url:
            return _FakeJsonResponse({"data": list(artist_results)})
        if "musicbrainz.org" in url:
            return _FakeJsonResponse({"release-groups": [{"id": "rg"}] if album else []})
        if "coverartarchive.org" in url:
            return _FakeJsonResponse(
                {
                    "images": [
                        {
                            "front": True,
                            "image": "http://example.com/art.jpg",
                            "width": 500,
                            "height": 500,
                        }
                    ]
                }
                if album
                else {"images": []}
            )
        if "itunes.apple.com" in url:
            return _FakeJsonResponse({"results": []})
        if url == "http://example.com/art.jpg":
            return _FakeImageResponse(b"album-image")
        return _FakeImageResponse(artist_image)

    monkeypatch.setattr("spotm3u.metadata.requests.get", fake_get)
    return calls


def _apic_frames(path: Path) -> dict[int, object]:
    """Return the file's APIC frames keyed by ID3 picture type."""
    import mutagen.id3 as mutagen_id3

    return {frame.type: frame for frame in mutagen_id3.ID3(str(path)).getall("APIC")}


def _write_existing_metadata(path: Path) -> None:
    """Write a front cover plus an unrelated frame that must survive enrichment."""
    import mutagen.id3 as mutagen_id3

    tags = mutagen_id3.ID3()
    tags.add(
        mutagen_id3.APIC(
            encoding=3, mime="image/jpeg", type=3, desc="Cover", data=b"existing-cover"
        )
    )
    tags.add(mutagen_id3.TXXX(encoding=3, desc="Testing", text=["kept"]))
    tags.save(path, v2_version=3)


@pytest.fixture
def artist_artwork_toggle():
    """Run a test with the artist-artwork flag, restoring the default after."""
    from spotm3u import metadata

    yield
    metadata.set_artist_artwork_enabled(True)


def test_artist_artwork_is_embedded_when_available(tmp_path, monkeypatch, artist_artwork_toggle):
    """The artist's profile image is embedded as an APIC artist picture (type 8)."""
    _install_artwork_requests(monkeypatch)
    track = Track("Song", ["Artist"], album="Album")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_embedded is True
    assert result.artist_artwork_source == "deezer-artist"
    assert result.artwork_embedded is True
    frames = _apic_frames(mp3)
    assert frames[8].data == b"artist-image"
    assert frames[3].data == b"album-image"


def test_album_and_artist_artwork_coexist(tmp_path, monkeypatch, artist_artwork_toggle):
    """Embedding artist artwork keeps the album cover and unrelated tags intact."""
    _install_artwork_requests(monkeypatch, album=False)
    mp3 = tmp_path / "local.mp3"
    _write_existing_metadata(mp3)
    track = Track("Song", ["Artist"], album="Album")

    result = enrich_metadata(mp3, track, tmp_path)

    frames = _apic_frames(mp3)
    assert set(frames) == {3, 8}
    assert frames[3].data == b"existing-cover"
    assert frames[8].data == b"artist-image"
    assert result.artist_artwork_embedded is True

    import mutagen.id3 as mutagen_id3

    assert str(mutagen_id3.ID3(str(mp3))["TXXX:Testing"].text[0]) == "kept"


def test_front_cover_stays_first_picture(tmp_path, monkeypatch, artist_artwork_toggle):
    """Rewriting artwork keeps the album cover as the file's first APIC frame."""
    import mutagen.id3 as mutagen_id3

    _install_artwork_requests(monkeypatch, album=False)
    mp3 = tmp_path / "local.mp3"
    _write_existing_metadata(mp3)
    track = Track("Song", ["Artist"], album="Album")

    enrich_metadata(mp3, track, tmp_path)
    enrich_metadata(mp3, track, tmp_path)

    frames = mutagen_id3.ID3(str(mp3)).getall("APIC")
    assert [frame.type for frame in frames] == [3, 8]
    assert frames[0].data == b"existing-cover"


def test_missing_artist_artwork_does_not_fail_generation(
    tmp_path, monkeypatch, artist_artwork_toggle
):
    """An artist with no matching profile image only reports a non-fatal error."""
    _install_artwork_requests(monkeypatch, artist_results=[])
    track = Track("Song", ["Artist"], album="Album")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_embedded is False
    assert result.artist_artwork_source is None
    assert "artist artwork not found" in result.errors
    assert result.artwork_embedded is True
    assert set(_apic_frames(mp3)) == {3}


def test_artist_artwork_network_failure_is_non_fatal(tmp_path, monkeypatch, artist_artwork_toggle):
    """A failed artist lookup never raises out of enrichment."""

    def unreachable(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("spotm3u.metadata.requests.get", unreachable)
    track = Track("Song", ["Artist"], album="Album")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_embedded is False
    assert "artist artwork not found" in result.errors


def test_artist_artwork_rejects_a_different_artist(tmp_path, monkeypatch, artist_artwork_toggle):
    """A result for another artist must never become the requested artist's image."""
    _install_artwork_requests(
        monkeypatch,
        artist_results=[
            {"name": "Someone Else Entirely", "picture_xl": "https://cdn.example/wrong.jpg"}
        ],
    )
    track = Track("Song", ["Artist"], album="Album")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_embedded is False
    assert "artist artwork not found" in result.errors


def test_same_artist_artwork_is_downloaded_once(tmp_path, monkeypatch, artist_artwork_toggle):
    """Tracks credited to the same artist reuse one cached profile image."""
    calls: list[str] = []
    _install_artwork_requests(monkeypatch, calls=calls)
    paths = []
    for index in range(3):
        mp3 = tmp_path / f"track{index}.mp3"
        mp3.write_bytes(b"fake-mp3-data")
        paths.append(mp3)

    results = enrich_metadata(
        [Track(f"Song {index}", ["Artist"], album=f"Album {index}") for index in range(3)],
        paths,
        tmp_path,
    )

    assert all(result.artist_artwork_embedded for result in results)
    assert calls.count("https://cdn.example/artist-1000.jpg") == 1
    assert sum(1 for url in calls if "api.deezer.com" in url) == 1
    assert (tmp_path / "artwork_cache" / "artists" / "artist_artist.jpg").is_file()


def test_artist_artwork_can_be_disabled(tmp_path, monkeypatch, artist_artwork_toggle):
    """With artist artwork off, no artist lookup happens at all."""
    from spotm3u import metadata

    metadata.set_artist_artwork_enabled(False)
    calls: list[str] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        assert "api.deezer.com" not in url, "artist lookup must not run when disabled"
        return _FakeJsonResponse({})

    monkeypatch.setattr("spotm3u.metadata.requests.get", fake_get)
    mp3 = tmp_path / "local.mp3"
    _write_existing_metadata(mp3)
    track = Track("Song", ["Artist"], album="Album")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_embedded is False
    assert "artist artwork" not in "; ".join(result.errors)
    assert calls


def test_verify_local_disabled_serves_unmarked_cache_without_network(
    tmp_path, monkeypatch, verify_local_toggle
):
    """With verify_local off, legacy unmarked cache entries are served as-is."""
    from spotm3u import metadata

    metadata.set_artwork_verify_local(False)
    cache = tmp_path / "artwork_cache"
    cache.mkdir()
    key = _cache_key("Dua Lipa", "Future Nostalgia", "Levitating")
    (cache / f"{key}.jpg").write_bytes(b"cached-cover")
    calls: list[str] = []

    def failing_get(url, **kwargs):
        calls.append(url)
        raise AssertionError(f"network must not be consulted: {url}")

    monkeypatch.setattr("spotm3u.metadata.requests.get", failing_get)

    data, source = _find_album_artwork(tmp_path, "Dua Lipa", "Future Nostalgia", "Levitating")

    assert data == b"cached-cover"
    assert source == "cache"
    assert calls == []


@pytest.fixture
def embedding_toggles():
    """Run a test with the embedding flags, restoring the defaults after."""
    from spotm3u import metadata

    yield
    metadata.set_metadata_enabled(True)
    metadata.set_id3_tags_enabled(True)
    metadata.set_album_artwork_enabled(True)


def test_album_artwork_can_be_disabled(tmp_path, monkeypatch, embedding_toggles):
    """With album artwork off, no cover is looked up, downloaded or embedded."""
    from spotm3u import metadata

    metadata.set_album_artwork_enabled(False)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("cover lookup must not run when album artwork is disabled")

    monkeypatch.setattr("spotm3u.metadata._find_album_artwork", unexpected)
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")
    track = Track("Song", ["Artist"], album="Album")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artwork_embedded is False
    assert result.artwork_source is None
    assert _apic_frames(mp3) == {}
    assert not any(error.startswith("artwork") for error in result.errors)
    assert "TIT2" in result.fields_written


def test_id3_tags_can_be_disabled(tmp_path, monkeypatch, embedding_toggles):
    """With tags off, the downloader's own frames are left exactly as they are."""
    import mutagen.id3 as mutagen_id3

    from spotm3u import metadata

    metadata.set_id3_tags_enabled(False)
    _install_artwork_requests(monkeypatch)
    mp3 = tmp_path / "local.mp3"
    _write_existing_metadata(mp3)
    track = Track("Song", ["Artist"], album="Album")

    result = enrich_metadata(mp3, track, tmp_path)

    tags = mutagen_id3.ID3(str(mp3))
    assert result.fields_written == ()
    assert "TIT2" not in tags
    assert str(tags["TXXX:Testing"].text[0]) == "kept"
    assert result.artwork_embedded is True  # artwork is a separate option


def test_metadata_master_switch_leaves_the_audio_untouched(
    tmp_path, monkeypatch, embedding_toggles
):
    """The master switch embeds nothing at all: no tags, pictures or lyrics."""
    from spotm3u import metadata

    metadata.set_metadata_enabled(False)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("no lookup may run when metadata embedding is disabled")

    monkeypatch.setattr("spotm3u.metadata._find_album_artwork", unexpected)
    monkeypatch.setattr("spotm3u.metadata._find_artist_artwork", unexpected)
    monkeypatch.setattr("spotm3u.metadata.fetch_lyrics", unexpected)
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")
    track = Track("Song", ["Artist"], album="Album")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.fields_written == ()
    assert result.errors == ()
    assert result.artwork_embedded is False
    assert result.artist_artwork_embedded is False
    assert mp3.read_bytes() == b"fake-mp3-data"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
