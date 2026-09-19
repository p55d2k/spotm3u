from pathlib import Path

from spotm3u.audio import LocalAudioResolver
from spotm3u.models import Track
from spotm3u.online import DownloadCache, SourceCandidate, cache_metadata_key, source_identity
from spotm3u.resolution import TrackResolver

TRACK = Track("Song", ["Artist"], duration_ms=200_000)


def install_fake_validation(monkeypatch, status="valid"):
    def fake(track, path):
        return type("Validation", (), {"status": status, "reasons": ()})()

    monkeypatch.setattr("spotm3u.online.cache.validate_downloaded_audio", fake)
    monkeypatch.setattr("spotm3u.resolution.validate_downloaded_audio", fake)


class Searcher:
    def __init__(self, candidates):
        self.candidates = candidates

    def search(self, track):
        return self.candidates


def candidate(**overrides) -> SourceCandidate:
    values = {
        "url": "https://www.youtube.com/watch?v=abc123XYZ",
        "title": "Song - Official Audio",
        "artist": "Artist",
        "uploader": "Artist",
        "duration_s": 200,
        "source_type": "youtube",
    }
    values.update(overrides)
    return SourceCandidate(**values)


def make_resolver(tmp_path, monkeypatch, *, cache, track=TRACK, url, downloader):
    install_fake_validation(monkeypatch)
    return TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        cache.download_dir,
        searcher=Searcher((candidate(url=url),)),
        downloader=downloader,
        cache=cache,
    ).resolve(track)


def downloading(calls, output_dir):
    def downloader(track, url, output):
        calls.append(url)
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        path = output / "down.mp3"
        path.write_bytes(b"audio")
        return path

    return downloader


def test_identity_helpers():
    assert source_identity("https://youtu.be/AbCdEf12345") == source_identity(
        "https://www.youtube.com/watch?v=AbCdEf12345"
    )
    assert source_identity("https://www.youtube.com/watch?v=AbCdEf12345") != source_identity(
        "https://www.youtube.com/watch?v=zyx6543210"
    )
    assert cache_metadata_key(Track("Wonderwall (Remastered)", ["Oasis"])) == cache_metadata_key(
        Track("wonderwall (Remastered)", [" Oasis "])
    )
    assert cache_metadata_key(Track("Wonderwall (Live)", ["Oasis"])) != cache_metadata_key(
        Track("Wonderwall (Remastered)", ["Oasis"])
    )


def test_same_source_is_reused_without_redownloading(tmp_path, monkeypatch):
    cache = DownloadCache(tmp_path / "downloads")
    calls: list[str] = []
    first = make_resolver(
        tmp_path,
        monkeypatch,
        cache=cache,
        url="https://www.youtube.com/watch?v=abc123XYZ",
        downloader=downloading(calls, cache.download_dir),
    )
    second = make_resolver(
        tmp_path,
        monkeypatch,
        cache=cache,
        url="https://www.youtube.com/watch?v=abc123XYZ",
        downloader=downloading(calls, cache.download_dir),
    )

    assert first.status == "downloaded" and first.successful
    assert second.status == "downloaded" and second.successful
    assert second.local_path == first.local_path
    assert calls == ["https://www.youtube.com/watch?v=abc123XYZ"]


def test_metadata_identity_reuse_with_different_source(tmp_path, monkeypatch):
    cache = DownloadCache(tmp_path / "downloads")
    calls: list[str] = []
    first = make_resolver(
        tmp_path,
        monkeypatch,
        cache=cache,
        url="https://www.youtube.com/watch?v=abc123XYZ",
        downloader=downloading(calls, cache.download_dir),
    )
    second = make_resolver(
        tmp_path,
        monkeypatch,
        cache=cache,
        url="https://www.youtube.com/watch?v=zyx6543210",
        downloader=downloading(calls, cache.download_dir),
    )

    assert first.successful and second.successful
    assert second.local_path == first.local_path
    assert "reused cached download" in second.reason
    assert len(calls) == 1


def install_cache_validation(monkeypatch):
    monkeypatch.setattr(
        "spotm3u.online.cache.validate_downloaded_audio",
        lambda track, path: type("Validation", (), {"status": "valid", "reasons": ()})(),
    )


def test_near_match_with_different_title_core_is_not_reused(tmp_path, monkeypatch):
    install_cache_validation(monkeypatch)
    cache = DownloadCache(tmp_path / "downloads")
    path = cache.download_dir / "entry.mp3"
    cache.store(
        Track("Wonderwall", ["Oasis"], duration_ms=200_000),
        "https://www.youtube.com/watch?v=abc123XYZ",
        path,
    )

    assert (
        cache.lookup(
            Track("Champagne Supernova", ["Oasis"], duration_ms=200_000),
            "https://www.youtube.com/watch?v=zyx6543210",
        )
        is None
    )


def test_different_artist_is_not_reused(tmp_path, monkeypatch):
    install_cache_validation(monkeypatch)
    cache = DownloadCache(tmp_path / "downloads")
    path = cache.download_dir / "entry.mp3"
    cache.store(
        Track("Song", ["Artist"], duration_ms=200_000),
        "https://www.youtube.com/watch?v=abc123XYZ",
        path,
    )

    assert (
        cache.lookup(
            Track("Song", ["Other Artist"], duration_ms=200_000),
            "https://www.youtube.com/watch?v=zyx6543210",
        )
        is None
    )


def test_duration_conflict_blocks_metadata_reuse(tmp_path, monkeypatch):
    install_cache_validation(monkeypatch)
    cache = DownloadCache(tmp_path / "downloads")
    path = cache.download_dir / "entry.mp3"
    cache.store(
        Track("Song", ["Artist"], duration_ms=200_000),
        "https://www.youtube.com/watch?v=abc123XYZ",
        path,
    )

    assert (
        cache.lookup(
            Track("Song", ["Artist"], duration_ms=300_000),
            "https://www.youtube.com/watch?v=zyx6543210",
        )
        is None
    )


def test_stored_source_is_reused_across_resolver_instances(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    calls: list[str] = []
    cache = DownloadCache(downloads)
    first = make_resolver(
        tmp_path,
        monkeypatch,
        cache=cache,
        url="https://www.youtube.com/watch?v=abc123XYZ",
        downloader=downloading(calls, downloads),
    )

    other_cache = DownloadCache(downloads)
    second = make_resolver(
        tmp_path,
        monkeypatch,
        cache=other_cache,
        url="https://www.youtube.com/watch?v=abc123XYZ",
        downloader=downloading(calls, downloads),
    )

    assert first.successful and second.successful
    assert len(calls) == 1


def test_duplicate_playlist_entries_share_one_download(tmp_path, monkeypatch):
    cache = DownloadCache(tmp_path / "downloads")
    calls: list[str] = []
    resolver = TrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        cache.download_dir,
        searcher=Searcher((candidate(),)),
        downloader=downloading(calls, cache.download_dir),
        cache=cache,
    )
    install_fake_validation(monkeypatch)

    results = resolver.resolve_all([TRACK, TRACK])

    assert [result.status for result in results] == ["downloaded", "downloaded"]
    assert results[0].local_path == results[1].local_path
    assert calls == ["https://www.youtube.com/watch?v=abc123XYZ"]


def test_corrupt_cached_file_is_not_reused(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    cache = DownloadCache(downloads)
    path = downloads / "entry.mp3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"definitely not audio")
    cache.store(TRACK, "https://www.youtube.com/watch?v=abc123XYZ", path)

    assert cache.lookup(TRACK, "https://youtu.be/abc123XYZ") is None


def test_lookup_rejects_payload_outside_download_dir(tmp_path):
    cache = DownloadCache(tmp_path / "downloads")
    cache.download_dir.mkdir(parents=True)
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"audio")
    cache._write_entries(
        [
            {
                "key": cache_metadata_key(TRACK),
                "source_url": "https://www.youtube.com/watch?v=zzz999999",
                "file": "../../outside.mp3",
            }
        ]
    )
    assert cache.lookup(TRACK, "https://www.youtube.com/watch?v=zzz999999") is None
