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
        stdout = "3|0|0|created\n"

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
    assert result.skipped == 0
    assert result.mode == "created"
    assert not result.cancelled
    script = captured["arguments"][2]
    assert script.index(str(first)) < script.index(str(second))
    assert script.count(f'add POSIX file "{first}"') == 4
    assert captured["options"]["timeout"] == 120
    assert captured["arguments"][:2] == ["osascript", "-e"]
    assert "reveal targetPlaylist" in script
    assert script.index("reveal targetPlaylist") > script.index(str(first))


def test_apple_script_prompts_when_playlist_exists_and_skips_duplicates(
    tmp_path, monkeypatch
) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")
    captured = {}

    class Completed:
        stdout = "1|0|2|skipped-duplicates\n"

    def runner(arguments, **options):
        captured["arguments"] = arguments
        return Completed()

    monkeypatch.setattr(apple_music.sys, "platform", "darwin")
    result = apple_music.add_to_apple_music(
        "Playlist",
        [audio, audio, audio],
        runner=runner,
    )

    assert result.imported == 1
    assert result.skipped == 2
    assert result.mode == "skipped-duplicates"
    assert not result.cancelled
    script = captured["arguments"][2]
    assert "display dialog" in script
    assert "display dialog dialogMessage buttons" in script
    assert '{"Skip duplicates", "Add all", "Cancel"} default button "Skip duplicates"' in script
    assert 'default button "Skip duplicates"' in script
    assert "whose location is theURL" in script
    assert 'set skipDuplicates to (mode is "skipped-duplicates")' in script
    assert "on error errorMessage number errorNumber" in script
    assert "if errorNumber is -128" in script
    assert 'return "0|0|0|cancelled"' in script
    assert 'set skipDuplicates to (mode is "skipped-duplicates")' in script


def test_apple_script_reports_cancelled_import(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")

    class Completed:
        stdout = "0|0|0|cancelled"

    monkeypatch.setattr(apple_music.sys, "platform", "darwin")
    result = apple_music.add_to_apple_music(
        "Playlist",
        [audio],
        runner=lambda *_args, **_kwargs: Completed(),
    )

    assert result.imported == 0
    assert result.skipped == 0
    assert result.cancelled
    assert result.mode == "cancelled"


def test_add_to_apple_music_reports_script_failures(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")

    class Completed:
        stdout = "0|1|0|appended"

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


def test_add_to_apple_music_rejects_invalid_result(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")

    class Completed:
        stdout = "not-a-count|boom"

    monkeypatch.setattr(apple_music.sys, "platform", "darwin")

    with pytest.raises(apple_music.AppleMusicError, match="invalid import result"):
        apple_music.add_to_apple_music(
            "Playlist",
            [audio],
            runner=lambda *_args, **_kwargs: Completed(),
        )
