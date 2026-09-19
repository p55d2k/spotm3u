"""Tests for the platform-aware "Add to Media Player" integration."""

import subprocess

import pytest

from spotm3u import media_player
from spotm3u.media_player import macos, windows


class Completed:
    """Stand-in for ``subprocess.CompletedProcess`` with AppleScript output."""

    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.stderr = ""


def test_media_player_is_unavailable_off_macos_and_windows(tmp_path, monkeypatch) -> None:
    playlist = tmp_path / "playlist.m3u"
    playlist.write_text("#EXTM3U\n", encoding="utf-8")
    # Every platform module imports the same ``sys``, so patching it once
    # switches the whole integration.
    monkeypatch.setattr(macos.sys, "platform", "linux")

    assert not media_player.media_player_available()
    assert not macos.apple_music_available()
    assert not windows.windows_media_player_available()
    with pytest.raises(media_player.MediaPlayerError, match="only available on macOS and Windows"):
        media_player.add_to_media_player("Playlist", playlist)


def test_media_player_is_available_on_macos_and_windows(monkeypatch) -> None:
    monkeypatch.setattr(macos.sys, "platform", "darwin")
    assert media_player.media_player_available()

    monkeypatch.setattr(windows.sys, "platform", "win32")
    assert media_player.media_player_available()


def test_add_to_media_player_uses_apple_music_on_macos(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")
    captured = {}

    def runner(arguments, **options):
        captured["arguments"] = arguments
        return Completed("1|0|0|created\n")

    monkeypatch.setattr(macos.sys, "platform", "darwin")
    result = media_player.add_to_media_player(
        "Playlist",
        tmp_path / "playlist.m3u",
        [audio],
        runner=runner,
    )

    assert captured["arguments"][0] == "osascript"
    assert result.action == "created"
    assert result.imported == 1
    assert result.message.startswith("Added to Media Player.")


def test_add_to_media_player_opens_playlist_on_windows(tmp_path, monkeypatch) -> None:
    # Spaces and Unicode must survive the handoff to the default association.
    folder = tmp_path / "My Music" / "プレイリスト"
    folder.mkdir(parents=True)
    playlist = folder / "playlist.m3u"
    playlist.write_text("#EXTM3U\n", encoding="utf-8")
    opened = []

    monkeypatch.setattr(windows.sys, "platform", "win32")
    result = media_player.add_to_media_player(
        "Playlist",
        playlist,
        opener=opened.append,
    )

    assert opened == [playlist]
    assert result.opened
    assert result.action == "opened"
    assert result.message == "Playlist opened in your default media player."


def test_add_to_apple_music_preserves_order_and_duplicates(tmp_path, monkeypatch) -> None:
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"audio")
    second.write_bytes(b"audio")
    captured = {}

    def runner(arguments, **options):
        captured["arguments"] = arguments
        captured["options"] = options
        return Completed("3|0|0|created\n")

    monkeypatch.setattr(macos.sys, "platform", "darwin")
    result = macos.add_to_apple_music(
        'Playlist "Test"',
        [first, second, first],
        runner=runner,
    )

    assert result.imported == 3
    assert result.failed == 0
    assert result.skipped == 0
    assert result.action == "created"
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

    def runner(arguments, **options):
        captured["arguments"] = arguments
        return Completed("1|0|2|skipped-duplicates\n")

    monkeypatch.setattr(macos.sys, "platform", "darwin")
    result = macos.add_to_apple_music(
        "Playlist",
        [audio, audio, audio],
        runner=runner,
    )

    assert result.imported == 1
    assert result.skipped == 2
    assert result.action == "skipped-duplicates"
    assert not result.cancelled
    assert "2 already-present track(s) were skipped." in result.message
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

    monkeypatch.setattr(macos.sys, "platform", "darwin")
    result = macos.add_to_apple_music(
        "Playlist",
        [audio],
        runner=lambda *_args, **_kwargs: Completed("0|0|0|cancelled"),
    )

    assert result.imported == 0
    assert result.skipped == 0
    assert result.cancelled
    assert result.action == "cancelled"
    assert "was cancelled" in result.message


def test_add_to_apple_music_reports_script_failures(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")

    monkeypatch.setattr(macos.sys, "platform", "darwin")
    result = macos.add_to_apple_music(
        "Playlist",
        [audio],
        runner=lambda *_args, **_kwargs: Completed("0|1|0|appended"),
    )

    assert result.imported == 0
    assert result.failed == 1
    assert not result.complete
    assert "1 track(s) could not be imported." in result.message


def test_add_to_apple_music_rejects_missing_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(macos.sys, "platform", "darwin")

    with pytest.raises(media_player.MediaPlayerError, match="no longer available"):
        macos.add_to_apple_music("Playlist", [tmp_path / "missing.mp3"])


def test_add_to_apple_music_reports_osascript_errors(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(macos.sys, "platform", "darwin")

    def runner(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "osascript")

    with pytest.raises(media_player.MediaPlayerError, match="import failed"):
        macos.add_to_apple_music("Playlist", [audio], runner=runner)


def test_add_to_apple_music_rejects_invalid_result(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(macos.sys, "platform", "darwin")

    with pytest.raises(media_player.MediaPlayerError, match="invalid import result"):
        macos.add_to_apple_music(
            "Playlist",
            [audio],
            runner=lambda *_args, **_kwargs: Completed("not-a-count|boom"),
        )


def test_add_to_apple_music_rejects_unknown_action(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(macos.sys, "platform", "darwin")

    with pytest.raises(media_player.MediaPlayerError, match="invalid import result"):
        macos.add_to_apple_music(
            "Playlist",
            [audio],
            # "opened" is a valid result action, but not a valid Music mode.
            runner=lambda *_args, **_kwargs: Completed("1|0|0|opened"),
        )


def test_windows_handoff_opens_a_resolved_absolute_path(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "My Music" / "پلی لیست"
    folder.mkdir(parents=True)
    playlist = folder / "playlist.m3u"
    playlist.write_text("#EXTM3U\n", encoding="utf-8")
    opened = []
    monkeypatch.setattr(windows.sys, "platform", "win32")

    result = windows.add_to_windows_media_player(
        tmp_path / "My Music" / ".." / "My Music" / "پلی لیست" / "playlist.m3u",
        opener=opened.append,
    )

    assert opened == [playlist]
    assert result.opened
    assert result.message == "Playlist opened in your default media player."


def test_windows_handoff_reports_missing_playlist(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(windows.sys, "platform", "win32")

    with pytest.raises(media_player.MediaPlayerError, match="no longer available"):
        windows.add_to_windows_media_player(tmp_path / "missing.m3u", opener=lambda _path: None)

    with pytest.raises(media_player.MediaPlayerError, match="not available"):
        windows.add_to_windows_media_player(None, opener=lambda _path: None)


def test_windows_handoff_reports_missing_association(tmp_path, monkeypatch) -> None:
    playlist = tmp_path / "playlist.m3u"
    playlist.write_text("#EXTM3U\n", encoding="utf-8")
    monkeypatch.setattr(windows.sys, "platform", "win32")

    def opener(_path):
        raise OSError("no association")

    with pytest.raises(media_player.MediaPlayerError, match="Download the playlist"):
        windows.add_to_windows_media_player(playlist, opener=opener)


def test_windows_handoff_is_unavailable_off_windows(tmp_path, monkeypatch) -> None:
    playlist = tmp_path / "playlist.m3u"
    playlist.write_text("#EXTM3U\n", encoding="utf-8")
    monkeypatch.setattr(windows.sys, "platform", "darwin")

    with pytest.raises(media_player.MediaPlayerError, match="only available on Windows"):
        windows.add_to_windows_media_player(playlist, opener=lambda _path: None)


def test_default_windows_opener_uses_the_file_association(tmp_path, monkeypatch) -> None:
    playlist = tmp_path / "My Music" / "playlist.m3u"
    playlist.parent.mkdir()
    playlist.write_text("#EXTM3U\n", encoding="utf-8")
    started = []
    monkeypatch.setattr(windows.os, "startfile", started.append, raising=False)

    windows._default_opener(playlist)

    assert started == [str(playlist)]
