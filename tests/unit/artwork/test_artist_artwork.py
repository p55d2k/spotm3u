"""Tests for artist artwork resolution and embedding."""

import logging
import threading
import time
from pathlib import Path

import pytest
import requests

from spotm3u import artwork, artwork_cache, metadata
from spotm3u.artwork import _find_album_artwork
from spotm3u.artwork_cache import _cache_key
from spotm3u.metadata import enrich_metadata
from spotm3u.models import Track


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
    artist_images=None,
    artist_albums=None,
    mb_artists=None,
    mb_artist=None,
    mb_release_groups=None,
    album=True,
    calls=None,
):
    """Mock the release and artist artwork requests used by enrichment.

    MusicBrainz is stubbed to know exactly the artists the Deezer stub returns,
    so an exact-name artist that Deezer lists once is confirmed by the second
    authority. Tests that want *no* MusicBrainz agreement pass ``mb_artists=[]``,
    and tests that want a MusicBrainz identity pass ``mb_release_groups`` with an
    ``artist-credit`` plus the artist's ``mb_artist`` URL relationships.
    """
    if artist_results is None:
        artist_results = [
            {
                "id": 1,
                "name": "Artist",
                "nb_album": 3,
                "nb_fan": 10,
                "picture_xl": "https://cdn.example/artist-1000.jpg",
            }
        ]
    if mb_artists is None:
        names = list(
            dict.fromkeys(
                str(record.get("name"))
                for record in artist_results
                if isinstance(record, dict) and record.get("name")
            )
        )
        mb_artists = [{"id": f"mbid-{index}", "name": name} for index, name in enumerate(names, 1)]
    if mb_release_groups is None:
        mb_release_groups = [{"id": "rg"}] if album else []
    artist_images = dict(artist_images or {})
    artist_albums = {int(k): list(v) for k, v in (artist_albums or {}).items()}
    calls = calls if calls is not None else []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if "api.deezer.com" in url:
            if url.endswith("/albums"):
                releases = artist_albums.get(int(url.split("/")[-2]), [])
                return _FakeJsonResponse({"data": [{"title": t} for t in releases]})
            parts = url.rstrip("/").split("/")
            if len(parts) >= 2 and parts[-2] == "artist":
                for record in artist_results:
                    if str(record.get("id")) == parts[-1]:
                        return _FakeJsonResponse(record)
                return _FakeJsonResponse({"error": {"code": 800, "message": "no data"}})
            return _FakeJsonResponse({"data": list(artist_results)})
        if "musicbrainz.org" in url:
            if "/artist/" in url:
                return _FakeJsonResponse(mb_artist or {"relations": []})
            if url.endswith("/artist"):
                return _FakeJsonResponse({"artists": mb_artists})
            if url.endswith("/recording"):
                return _FakeJsonResponse({"recordings": []})
            return _FakeJsonResponse({"release-groups": mb_release_groups})
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
        return _FakeImageResponse(artist_images.get(url, artist_image))

    monkeypatch.setattr("spotm3u.artwork_sources.requests.get", fake_get)
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

    yield
    artwork.set_artist_artwork_enabled(True)


def test_artist_artwork_is_embedded_when_available(tmp_path, monkeypatch, artist_artwork_toggle):
    """The artist's profile image is embedded as an APIC artist picture (type 8)."""
    _install_artwork_requests(monkeypatch, artist_albums={1: ["Album"]})
    track = Track("Song", ["Artist"], album="Album")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_embedded is True
    assert result.artist_artwork_source == "deezer-artist:1:verified:deezer-release"
    assert result.artwork_embedded is True
    frames = _apic_frames(mp3)
    assert frames[8].data == b"artist-image"
    assert frames[3].data == b"album-image"


def test_album_and_artist_artwork_coexist(tmp_path, monkeypatch, artist_artwork_toggle):
    """Embedding artist artwork keeps the album cover and unrelated tags intact."""
    _install_artwork_requests(monkeypatch, album=False, artist_albums={1: ["Album"]})
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

    _install_artwork_requests(monkeypatch, album=False, artist_albums={1: ["Album"]})
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

    monkeypatch.setattr("spotm3u.artwork_sources.requests.get", unreachable)
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
            {
                "id": 42,
                "name": "Someone Else Entirely",
                "nb_fan": 999,
                "picture_xl": "https://cdn.example/wrong.jpg",
            }
        ],
    )
    track = Track("Song", ["Artist"], album="Album")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_embedded is False
    assert "artist artwork not found" in result.errors


def test_artist_artwork_rejects_exact_name_namesakes_without_evidence(
    tmp_path, monkeypatch, artist_artwork_toggle, caplog
):
    """Regression: an exact name plus a fan count is not an identity.

    ``q=adele`` returns several artists named exactly "Adele", the first of them an
    unrelated act with a few hundred fans. Ranking by fans picked that impostor's
    image, and when nothing corroborated any of them the most popular one was used
    anyway -- both embed a stranger's face permanently. With no release or track
    evidence the answer is now no artist artwork at all.
    """
    impostor = "https://cdn.example/impostor.jpg"
    real = "https://cdn.example/real.jpg"
    calls = _install_artwork_requests(
        monkeypatch,
        artist_results=[
            {"id": 61817012, "name": "Adele", "nb_fan": 326, "picture_xl": impostor},
            {"id": 5673798, "name": "Adele", "nb_fan": 12404, "picture_xl": impostor},
            {"id": 75798, "name": "Adele", "nb_fan": 99999999, "picture_xl": real},
        ],
        artist_images={impostor: b"impostor-image", real: b"real-image"},
    )
    track = Track("Song", ["Adele"], album="Album")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    with caplog.at_level(logging.DEBUG, logger="spotm3u.artwork_sources"):
        result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_embedded is False
    assert result.artwork_embedded is True
    assert 8 not in _apic_frames(mp3)
    assert not any(url in calls for url in (impostor, real)), "no namesake image may be downloaded"
    # The rejection names the candidates it refused, so a wrong image can be
    # traced later without digging through cache files.
    assert "Artist artwork rejected" in caplog.text
    assert "61817012" in caplog.text and "75798" in caplog.text


def test_artist_artwork_prefers_the_artist_that_has_the_album(
    tmp_path, monkeypatch, artist_artwork_toggle
):
    """The track's own album settles a namesake that fame alone cannot.

    Both artists are named exactly "Adele" and the impostor is the more popular
    one, so only Deezer's release list separates them: the real Adele has "19".
    That release match is the identity evidence, not a preference.
    """
    impostor = "https://cdn.example/impostor.jpg"
    real = "https://cdn.example/real.jpg"
    calls = _install_artwork_requests(
        monkeypatch,
        artist_results=[
            {"id": 91111, "name": "Adele", "nb_fan": 99999999, "picture_xl": impostor},
            {"id": 75798, "name": "Adele", "nb_fan": 15475601, "picture_xl": real},
        ],
        artist_images={impostor: b"impostor-image", real: b"real-image"},
        artist_albums={91111: ["Be divine"], 75798: ["19", "21", "25"]},
    )
    track = Track("Song", ["Adele"], album="19")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_source == "deezer-artist:75798:verified:deezer-release"
    assert _apic_frames(mp3)[8].data == b"real-image"
    assert any(url.endswith("/75798/albums") for url in calls)


def test_artist_artwork_matches_the_track_when_two_namesakes_have_the_album(
    tmp_path, monkeypatch, artist_artwork_toggle
):
    """Regression for the Jungkook report: the track, not the fan count, decides.

    Both Deezer artists are named exactly "Jungkook" and both list the requested
    album, so the album alone cannot separate them. Only the artist whose release
    carries the track title is the artist behind the track; the unrelated album
    holder (a much more popular account here) must not be substituted.
    """
    wrong = "https://cdn.example/unrelated.jpg"
    right = "https://cdn.example/jungkook.jpg"
    calls = _install_artwork_requests(
        monkeypatch,
        artist_results=[
            {"id": 66666, "name": "Jungkook", "nb_fan": 9999999, "picture_xl": wrong},
            {"id": 67890, "name": "Jungkook", "nb_fan": 120, "picture_xl": right},
        ],
        artist_albums={66666: ["GOLDEN (Remixes)"], 67890: ["GOLDEN", "Standing Next to You"]},
        artist_images={wrong: b"wrong-image", right: b"right-image"},
    )
    track = Track("Standing Next to You", ["Jungkook"], album="GOLDEN")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    # Both the album and the track appear in that artist's releases, and neither
    # appears in the other candidate's.
    assert (
        result.artist_artwork_source == "deezer-artist:67890:verified:deezer-release-deezer-track"
    )
    assert _apic_frames(mp3)[8].data == b"right-image"
    assert wrong not in calls


@pytest.mark.parametrize(
    ("releases", "expected_marker"),
    [
        # Both namesakes carry the album and nothing else: nothing to choose
        # between, however different their fan counts are.
        ({11: ["19"], 12: ["19"]}, None),
        # Only one also carries the track, which is what identifies it.
        (
            {11: ["19"], 12: ["19", "Song"]},
            "deezer-artist:12:verified:deezer-release-deezer-track",
        ),
    ],
)
def test_artist_artwork_settles_two_corroborated_namesakes(
    tmp_path, monkeypatch, artist_artwork_toggle, releases, expected_marker
):
    """The stronger corroboration wins, and a tie is left unresolved."""
    first = "https://cdn.example/first.jpg"
    second = "https://cdn.example/second.jpg"
    _install_artwork_requests(
        monkeypatch,
        artist_results=[
            {"id": 11, "name": "Adele", "nb_fan": 900, "picture_xl": first},
            {"id": 12, "name": "Adele", "nb_fan": 100, "picture_xl": second},
        ],
        artist_albums=releases,
        artist_images={first: b"first-image", second: b"second-image"},
    )
    track = Track("Song", ["Adele"], album="19")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    if expected_marker is None:
        assert result.artist_artwork_embedded is False
        assert 8 not in _apic_frames(mp3)
    else:
        assert result.artist_artwork_source == expected_marker
        assert _apic_frames(mp3)[8].data == b"second-image"


def test_artist_artwork_uses_the_musicbrainz_artist_identity(
    tmp_path, monkeypatch, artist_artwork_toggle
):
    """A MusicBrainz artist URL relationship gives the exact Deezer artist.

    The relationship names the artist's Deezer page, so the image is fetched by
    id and no display-name search happens at all -- which is what makes this the
    strongest form of evidence available. Two pages are linked, as real
    MusicBrainz data does for a canonical page plus a duplicate, and the credited
    name comes from ``artist-credit`` alone because that is all the release-group
    *search* returns: a missing ``artist-credit-phrase`` must not turn a real
    MusicBrainz match into no match.
    """
    calls = _install_artwork_requests(
        monkeypatch,
        artist_results=[
            {
                "id": 555,
                "name": "Jungkook",
                "nb_fan": 3,
                "picture_xl": "https://cdn.example/duplicate.jpg",
            },
            {
                "id": 67890,
                "name": "Jungkook",
                "nb_fan": 900000,
                "picture_xl": "https://cdn.example/jk.jpg",
            },
        ],
        mb_release_groups=[
            {
                "id": "rg-1",
                "title": "GOLDEN",
                "artist-credit": [{"artist": {"id": "mbid-jk", "name": "Jungkook"}}],
            }
        ],
        mb_artist={
            "id": "mbid-jk",
            "name": "Jungkook",
            "relations": [
                {
                    "type": "free streaming",
                    "url": {"resource": "https://www.deezer.com/artist/555"},
                },
                {
                    "type": "free streaming",
                    "url": {"resource": "https://www.deezer.com/artist/67890"},
                },
            ],
        },
        artist_images={"https://cdn.example/jk.jpg": b"jungkook-image"},
    )
    track = Track("Standing Next to You", ["Jungkook"], album="GOLDEN")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_source == "deezer-artist:67890:verified:mb-artist-url"
    assert _apic_frames(mp3)[8].data == b"jungkook-image"
    assert not any(url.endswith("/search/artist") for url in calls)


def test_artist_artwork_rejects_a_sole_candidate_musicbrainz_does_not_know(
    tmp_path, monkeypatch, artist_artwork_toggle
):
    """One exact-name candidate is not enough when nothing else confirms it.

    A unique name is still only a name: with no corroborating release and no
    MusicBrainz artist either, there is nothing to establish who this is.
    """
    _install_artwork_requests(monkeypatch, mb_artists=[])
    track = Track("Song", ["Artist"], album="Album")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_embedded is False
    assert "artist artwork not found" in result.errors
    assert 8 not in _apic_frames(mp3)


def test_artist_artwork_accepts_a_sole_candidate_musicbrainz_agrees_with(
    tmp_path, monkeypatch, artist_artwork_toggle
):
    """An obscure artist still gets an image when both catalogs know one name."""
    calls = _install_artwork_requests(
        monkeypatch,
        artist_results=[
            {"id": 7, "name": "Obscure", "nb_fan": 12, "picture_xl": "https://cdn.example/o.jpg"}
        ],
        mb_artists=[{"id": "mbid-o", "name": "Obscure"}],
        artist_images={"https://cdn.example/o.jpg": b"obscure-image"},
    )
    track = Track("Song", ["Obscure"], album="Album")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_source == "deezer-artist:7:verified:sole-candidate"
    assert _apic_frames(mp3)[8].data == b"obscure-image"
    assert any(url.endswith("/artist") for url in calls)


def test_artist_artwork_rejects_a_partial_name_match(tmp_path, monkeypatch, artist_artwork_toggle):
    """A name that merely contains the requested one is not the requested artist.

    "Adèle & Zalem" scored a perfect 100 because the requested name is a substring of
    it, which let a different act through the identity gate.
    """
    _install_artwork_requests(
        monkeypatch,
        artist_results=[
            {
                "id": 5203567,
                "name": "Adèle & Zalem",
                "nb_fan": 1807,
                "picture_xl": "https://cdn.example/zalem.jpg",
            }
        ],
    )
    track = Track("Song", ["Adele"], album="Album")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_embedded is False
    assert "artist artwork not found" in result.errors


@pytest.mark.parametrize("marker", ["deezer-artist", "deezer-artist:1"])
def test_artist_artwork_without_an_identity_marker_is_reverified(
    tmp_path, monkeypatch, artist_artwork_toggle, marker
):
    """An image authorized by name and popularity is re-resolved, not served.

    Markers written before identities were verified recorded the provider name, or
    later only the artist id, and could hold any namesake's image -- the reported
    Jungkook failure was cached exactly that way. Both forms are ignored so a
    future run replaces the wrong face instead of trusting it forever.
    """
    artist_dir = artwork_cache._artist_cache_dir(tmp_path)
    key = artwork_cache._artist_cache_key("Artist")
    artwork_cache._write_cached_image(artist_dir, key, b"wrong-namesake-image")
    artwork_cache._write_cached_source(artist_dir, key, marker)

    _install_artwork_requests(monkeypatch, artist_albums={1: ["Album"]})
    track = Track("Song", ["Artist"], album="Album")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_source == "deezer-artist:1:verified:deezer-release"
    assert artwork_cache._read_cached_image(artist_dir, key) == b"artist-image"


def test_verified_artist_artwork_is_served_from_the_cache(
    tmp_path, monkeypatch, artist_artwork_toggle
):
    """A verified cache entry is reused without asking any provider again."""
    artist_dir = artwork_cache._artist_cache_dir(tmp_path)
    key = artwork_cache._artist_cache_key("Artist")
    artwork_cache._write_cached_image(artist_dir, key, b"cached-artist-image")
    artwork_cache._write_cached_source(artist_dir, key, "deezer-artist:1:verified:deezer-release")

    calls = _install_artwork_requests(monkeypatch)
    track = Track("Song", ["Artist"], album="Album")
    mp3 = tmp_path / "track.mp3"
    mp3.write_bytes(b"fake-mp3-data")

    result = enrich_metadata(mp3, track, tmp_path)

    assert result.artist_artwork_source == "cache"
    assert _apic_frames(mp3)[8].data == b"cached-artist-image"
    assert not any("deezer.com" in url for url in calls)


def test_same_artist_artwork_is_downloaded_once(tmp_path, monkeypatch, artist_artwork_toggle):
    """Tracks credited to the same artist reuse one cached profile image."""
    calls: list[str] = []
    _install_artwork_requests(
        monkeypatch, calls=calls, artist_albums={1: ["Album 0", "Album 1", "Album 2"]}
    )
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
    assert sum(1 for url in calls if url.endswith("/search/artist")) == 1
    assert (tmp_path / "artwork_cache" / "artists" / "artist_artist.jpg").is_file()


def test_artist_artwork_can_be_disabled(tmp_path, monkeypatch, artist_artwork_toggle):
    """With artist artwork off, no artist lookup happens at all."""

    artwork.set_artist_artwork_enabled(False)
    calls: list[str] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        assert "api.deezer.com" not in url, "artist lookup must not run when disabled"
        return _FakeJsonResponse({})

    monkeypatch.setattr("spotm3u.artwork_sources.requests.get", fake_get)
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

    artwork.set_artwork_verify_local(False)
    cache = tmp_path / "artwork_cache"
    cache.mkdir()
    key = _cache_key("Dua Lipa", "Future Nostalgia", "Levitating")
    (cache / f"{key}.jpg").write_bytes(b"cached-cover")
    calls: list[str] = []

    def failing_get(url, **kwargs):
        calls.append(url)
        raise AssertionError(f"network must not be consulted: {url}")

    monkeypatch.setattr("spotm3u.artwork_sources.requests.get", failing_get)

    data, source = _find_album_artwork(tmp_path, "Dua Lipa", "Future Nostalgia", "Levitating")

    assert data == b"cached-cover"
    assert source == "cache"
    assert calls == []


@pytest.fixture
def embedding_toggles():
    """Run a test with the embedding flags, restoring the defaults after."""

    yield
    metadata.set_metadata_enabled(True)
    metadata.set_id3_tags_enabled(True)
    artwork.set_album_artwork_enabled(True)


def test_album_artwork_can_be_disabled(tmp_path, monkeypatch, embedding_toggles):
    """With album artwork off, no cover is looked up, downloaded or embedded."""

    artwork.set_album_artwork_enabled(False)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("cover lookup must not run when album artwork is disabled")

    monkeypatch.setattr("spotm3u.artwork._find_album_artwork", unexpected)
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

    metadata.set_metadata_enabled(False)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("no lookup may run when metadata embedding is disabled")

    monkeypatch.setattr("spotm3u.artwork._find_album_artwork", unexpected)
    monkeypatch.setattr("spotm3u.artwork._find_artist_artwork", unexpected)
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


# --- playlist-level artist prefetch -------------------------------------------


def test_unique_artwork_artists_deduplicates_and_keeps_order() -> None:
    """A playlist's artists are collected once each, without joining names."""
    from spotm3u.artwork import unique_artwork_artists

    tracks = [
        Track("A", ["Jungkook"], album="GOLDEN"),
        Track("B", ["Jungkook"], album="GOLDEN"),
        Track("C", ["IVE"], album="IVE SWITCH"),
        Track("D", []),
    ]

    assert unique_artwork_artists(tracks) == ["Jungkook", "IVE"]


def test_prefetch_resolves_each_unique_artist_once(tmp_path, monkeypatch, artist_artwork_toggle):
    """Regression: a 50-track playlist must not resolve an artist per track.

    Three Jungkook tracks, two IVE tracks and one NewJeans track are three
    resolutions, not six.
    """
    calls: list[str] = []

    def fake_find(download_dir, artist, *, album=None, title=None):
        calls.append(artist)
        return b"artist-image", "deezer-artist:1:verified:test"

    monkeypatch.setattr(artwork, "_find_artist_artwork", fake_find)
    tracks = [
        Track("S1", ["Jungkook"], album="GOLDEN"),
        Track("S2", ["Jungkook"], album="GOLDEN"),
        Track("S3", ["Jungkook"], album="GOLDEN"),
        Track("S4", ["IVE"], album="IVE SWITCH"),
        Track("S5", ["IVE"], album="IVE SWITCH"),
        Track("S6", ["NewJeans"], album="Get Up"),
    ]

    resolved = artwork.prefetch_artist_artwork(tmp_path, tracks)

    assert resolved == 3
    assert sorted(calls) == ["IVE", "Jungkook", "NewJeans"]


def test_prefetch_uses_the_best_evidenced_track_for_identity(
    tmp_path, monkeypatch, artist_artwork_toggle
):
    """The album-carrying track supplies the identity, not an album-less one."""
    calls: list[tuple[str, str | None, str | None]] = []

    def fake_find(download_dir, artist, *, album=None, title=None):
        calls.append((artist, album, title))
        return b"artist-image", "deezer-artist:1:verified:test"

    monkeypatch.setattr(artwork, "_find_artist_artwork", fake_find)
    tracks = [
        Track("Only Title", ["Artist"]),
        Track("Song", ["Artist"], album="Album"),
        Track("Another", ["Artist"], album="Album"),
    ]

    artwork.prefetch_artist_artwork(tmp_path, tracks)

    assert calls == [("Artist", "Album", "Song")]


def test_prefetch_concurrency_is_bounded(tmp_path, monkeypatch, artist_artwork_toggle):
    """Many artists resolve in parallel, but never more than ``max_workers`` at once."""
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def fake_find(download_dir, artist, *, album=None, title=None):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep(0.05)
            return b"artist-image", "deezer-artist:1:verified:test"
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setattr(artwork, "_find_artist_artwork", fake_find)
    tracks = [Track(f"S{index}", [f"Artist {index}"], album="Album") for index in range(8)]

    artwork.prefetch_artist_artwork(tmp_path, tracks, max_workers=2)

    assert state["peak"] == 2


def test_prefetch_is_skipped_when_artist_artwork_is_disabled(
    tmp_path, monkeypatch, artist_artwork_toggle
):
    """With artist artwork off, the prefetch makes no requests at all."""
    artwork.set_artist_artwork_enabled(False)
    called: list[str] = []
    monkeypatch.setattr(artwork, "_find_artist_artwork", lambda *args, **kwargs: called.append("x"))

    resolved = artwork.prefetch_artist_artwork(tmp_path, [Track("S", ["Artist"], album="Album")])

    assert resolved == 0
    assert called == []
