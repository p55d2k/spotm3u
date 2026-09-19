import subprocess

import pytest

from spotm3u import apple_music


def test_apple_music_is_unavailable_off_macos(monkeypatch) -> None:
    monkeypatch.setattr(apple_music.sys, "platform", "linux")

    assert not apple_music.apple_music_available()


def test_add_to_apple_music_preserves_order_and_duplicates(tmp_path, monkeypatch) -> None:
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"audio")
    second.write_bytes(b"audio")
    captured = {}

    class Completed:
        stdout = "3|0\n"

    def runner(arguments, **options):
        captured["arguments"] = arguments
        captured["options"] = options
        return Completed()

    monkeypatch.setattr(apple_music.sys, "platform", "darwin")
    result = apple_music.add_to_apple_music(
        'Playlist "Test"',
        [first, second, first],
        runner=runner,
    )

    assert result.imported == 3
    assert result.failed == 0
    script = captured["arguments"][2]
    assert script.index(str(first)) < script.index(str(second))
    assert script.count(str(first)) == 2
    assert captured["options"]["timeout"] == 120
    assert captured["arguments"][:2] == ["osascript", "-e"]
    assert "reveal targetPlaylist" in script
    assert script.index("reveal targetPlaylist") > script.index(str(first))


def test_add_to_apple_music_reports_script_failures(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")

    class Completed:
        stdout = "0|1"

    monkeypatch.setattr(apple_music.sys, "platform", "darwin")
    result = apple_music.add_to_apple_music(
        "Playlist",
        [audio],
        runner=lambda *_args, **_kwargs: Completed(),
    )

    assert result.imported == 0
    assert result.failed == 1
    assert not result.complete


def test_add_to_apple_music_rejects_missing_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(apple_music.sys, "platform", "darwin")

    with pytest.raises(apple_music.AppleMusicError, match="no longer available"):
        apple_music.add_to_apple_music("Playlist", [tmp_path / "missing.mp3"])


def test_add_to_apple_music_reports_osascript_errors(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(apple_music.sys, "platform", "darwin")

    def runner(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "osascript")

    with pytest.raises(apple_music.AppleMusicError, match="import failed"):
        apple_music.add_to_apple_music("Playlist", [audio], runner=runner)
