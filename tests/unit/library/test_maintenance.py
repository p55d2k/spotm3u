"""Tests for the developer clear: what it counts, and what it may remove."""

import os
from pathlib import Path

import pytest

from spotm3u import artwork_cache, maintenance
from spotm3u.maintenance import CLEAR_ITEMS, CONFIRM_PHRASE, UnsafeTarget, clear, inventory


def populated_download_dir(root: Path) -> Path:
    """A download folder holding one of everything the clear knows about."""
    download = root / "SpotM3U"
    (download / "artwork_cache" / "artists").mkdir(parents=True)
    (download / "lyrics_cache").mkdir()
    (download / "Artist - Song.mp3").write_bytes(b"x" * 100)
    (download / "Other - Song.mp3").write_bytes(b"y" * 50)
    (download / "playlist-1.m3u").write_text("song", encoding="utf-8")
    (download / ".spotm3u-cache.json").write_text("{}", encoding="utf-8")
    (download / "artwork_cache" / "cover.jpg").write_bytes(b"a" * 10)
    (download / "artwork_cache" / "cover.src").write_text("url", encoding="utf-8")
    (download / "artwork_cache" / "artists" / "artist.jpg").write_bytes(b"b" * 20)
    (download / "lyrics_cache" / "song.lrc").write_text("[00:00]x", encoding="utf-8")
    return download


def populated_upload_root(root: Path) -> Path:
    upload = root / "uploads"
    job = upload / "job-abc"
    (job / "extracted").mkdir(parents=True)
    (job / "extracted" / "playlist.csv").write_text("title", encoding="utf-8")
    (upload / "keep-me").mkdir()
    return upload


def measured(name: str, report: dict) -> tuple[int, int]:
    """The inventory row of one item."""
    return next((item["files"], item["bytes"]) for item in report["items"] if item["name"] == name)


def test_inventory_counts_every_part_with_its_size(tmp_path: Path) -> None:
    download = populated_download_dir(tmp_path)
    upload = populated_upload_root(tmp_path)

    report = inventory(download, upload)

    assert report["download_dir"] == str(download)
    assert report["confirm_phrase"] == CONFIRM_PHRASE
    assert [item["name"] for item in report["items"]] == list(CLEAR_ITEMS)
    assert measured("songs", report) == (2, 150)
    assert measured("playlists", report) == (1, 4)
    assert measured("manifest", report) == (1, 2)
    assert measured("artwork", report) == (3, 33)
    assert measured("lyrics", report) == (1, 8)
    assert measured("uploads", report) == (1, 5)


def test_inventory_reports_empty_parts_of_a_bare_folder(tmp_path: Path) -> None:
    (tmp_path / "SpotM3U").mkdir()

    report = inventory(tmp_path / "SpotM3U", tmp_path / "no-uploads")

    assert all(item["files"] == 0 and item["bytes"] == 0 for item in report["items"])


def test_clear_removes_only_the_selected_parts(tmp_path: Path) -> None:
    download = populated_download_dir(tmp_path)

    report = clear(download, tmp_path / "uploads", ["songs", "lyrics"], music_library=tmp_path)

    assert report["removed"] == {"songs": 2, "lyrics": 1}
    assert report["bytes_freed"] == 158
    assert not list(download.glob("*.mp3"))
    assert not (download / "lyrics_cache").exists()
    assert (download / "playlist-1.m3u").is_file()
    assert (download / ".spotm3u-cache.json").is_file()
    assert (download / "artwork_cache" / "cover.jpg").is_file()


def test_clear_everything_empties_the_own_folders_and_keeps_the_rest(tmp_path: Path) -> None:
    download = populated_download_dir(tmp_path)
    upload = populated_upload_root(tmp_path)
    # A file and a folder the user put there themselves.
    (download / "notes.txt").write_text("mine", encoding="utf-8")
    mine = download / "my-album"
    mine.mkdir()
    (mine / "Live - Song.mp3").write_bytes(b"z" * 30)

    report = clear(download, upload, CLEAR_ITEMS, music_library=tmp_path)

    assert report["removed"] == {
        "songs": 2,
        "playlists": 1,
        "manifest": 1,
        "artwork": 3,
        "lyrics": 1,
        "uploads": 1,
    }
    assert sorted(entry.name for entry in download.iterdir()) == ["my-album", "notes.txt"]
    assert (mine / "Live - Song.mp3").is_file()
    assert (upload / "keep-me").is_dir()
    assert not (upload / "job-abc").exists()


def test_clear_never_follows_a_link_out_of_the_download_folder(tmp_path: Path) -> None:
    library = tmp_path / "Music"
    library.mkdir()
    precious = library / "Precious.mp3"
    precious.write_bytes(b"p" * 40)
    download = populated_download_dir(tmp_path)
    os.symlink(precious, download / "Linked.mp3")

    report = clear(download, tmp_path / "uploads", ["songs", "playlists"], music_library=library)

    assert report["removed"] == {"songs": 2, "playlists": 1}
    assert precious.is_file()
    assert (download / "Linked.mp3").is_symlink()


def test_clear_refuses_a_download_folder_that_is_the_music_library(tmp_path: Path) -> None:
    library = tmp_path / "Music"
    library.mkdir()
    (library / "Precious.mp3").write_bytes(b"p")

    with pytest.raises(UnsafeTarget, match="music library itself"):
        clear(library, tmp_path / "uploads", ["songs"], music_library=library)

    assert (library / "Precious.mp3").is_file()


def test_clear_refuses_a_download_folder_above_the_music_library(tmp_path: Path) -> None:
    library = tmp_path / "Music"
    library.mkdir()
    (library / "Precious.mp3").write_bytes(b"p")

    with pytest.raises(UnsafeTarget, match="contains the music library"):
        clear(tmp_path, tmp_path / "uploads", ["songs"], music_library=library)

    assert (library / "Precious.mp3").is_file()


def test_clearing_artwork_also_forgets_the_in_process_memo(tmp_path: Path) -> None:
    download = populated_download_dir(tmp_path)
    cache_dir = artwork_cache._cache_dir(download)
    artwork_cache._write_cached_image(cache_dir, "cover", b"image-bytes")
    memo_key = f"{cache_dir}::cover"

    clear(download, tmp_path / "uploads", ["artwork"], music_library=tmp_path)

    assert memo_key not in artwork_cache._ARTWORK_MEMORY
    assert not artwork_cache.artwork_cache_dir(download).exists()


def test_clearing_twice_reports_nothing_removed(tmp_path: Path) -> None:
    download = populated_download_dir(tmp_path)

    clear(download, tmp_path / "uploads", ["songs"], music_library=tmp_path)
    report = clear(download, tmp_path / "uploads", ["songs"], music_library=tmp_path)

    assert report["removed"] == {"songs": 0}
    assert report["bytes_freed"] == 0


def test_unknown_items_are_named_for_the_caller() -> None:
    assert maintenance.unknown_items(["songs", "nope", "ALSO-NOT"]) == ["ALSO-NOT", "nope"]
    assert maintenance.unknown_items(list(CLEAR_ITEMS)) == []
