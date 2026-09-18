from pathlib import Path
import sys
import time
import types

import pytest

from spotm3u.models import Track
from spotm3u.online import DownloadError, download_track


TRACK = Track(title="Song / Name", artists=["An Artist"], spotify_id="track-1")


class FakeYoutubeDL:
    options: dict = {}
    result = 0
    writes_mp3 = True

    def __init__(self, options: dict) -> None:
        type(self).options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def download(self, urls):
        if self.writes_mp3:
            output = Path(self.options["outtmpl"].replace("%(ext)s", "mp3"))
            output.write_bytes(b"valid mp3")
        return self.result


def install_fake_yt_dlp(monkeypatch, fake=FakeYoutubeDL):
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=fake))
    monkeypatch.setattr(
        "spotm3u.online.downloader.validate_downloaded_audio",
        lambda track, path: types.SimpleNamespace(status="valid", reasons=()),
    )


def test_download_produces_safe_mp3_inside_output_directory(tmp_path, monkeypatch):
    install_fake_yt_dlp(monkeypatch)

    result = download_track(TRACK, "https://example.com/source", tmp_path)

    assert result.parent == tmp_path.resolve()
    assert result.suffix == ".mp3"
    assert result.exists()
    assert "/" not in result.stem
    assert "FFmpegExtractAudio" in FakeYoutubeDL.options["postprocessors"][0]["key"]


def test_download_name_follows_spotify_convention(tmp_path, monkeypatch):
    install_fake_yt_dlp(monkeypatch)
    track = Track(title="Wonderwall (Remastered)", artists=["Oasis"])

    result = download_track(track, "https://example.com/source", tmp_path)

    assert result.name == "Wonderwall (Remastered) - Oasis.mp3"


def test_download_embeds_spotify_metadata(tmp_path, monkeypatch):
    install_fake_yt_dlp(monkeypatch)
    track = Track(
        title="Wonderwall (Remastered)",
        artists=["Oasis"],
        album="(What's the Story) Morning Glory?",
    )

    result = download_track(track, "https://example.com/source", tmp_path)

    from mutagen.easyid3 import EasyID3

    tags = EasyID3(str(result))
    assert tags["title"] == ["Wonderwall (Remastered)"]
    assert tags["artist"] == ["Oasis"]
    assert tags["album"] == ["(What's the Story) Morning Glory?"]


def test_same_track_and_source_have_deterministic_name(tmp_path, monkeypatch):
    install_fake_yt_dlp(monkeypatch)
    first = download_track(TRACK, "https://example.com/source", tmp_path)
    first.unlink()
    second = download_track(TRACK, "https://example.com/source", tmp_path)
    assert first == second


def test_invalid_url_is_rejected_without_downloader(tmp_path):
    with pytest.raises(DownloadError, match="HTTP"):
        download_track(TRACK, "not a URL", tmp_path)


def test_downloader_failure_is_explicit(tmp_path, monkeypatch):
    class Failing(FakeYoutubeDL):
        result = 1

    install_fake_yt_dlp(monkeypatch, Failing)
    with pytest.raises(DownloadError, match="download failed"):
        download_track(TRACK, "https://example.com/source", tmp_path)


def test_missing_output_is_not_success(tmp_path, monkeypatch):
    class NoOutput(FakeYoutubeDL):
        writes_mp3 = False

    install_fake_yt_dlp(monkeypatch, NoOutput)
    with pytest.raises(DownloadError, match="complete MP3"):
        download_track(TRACK, "https://example.com/source", tmp_path)


def test_hung_download_is_abandoned_after_wall_clock_timeout(tmp_path, monkeypatch):
    class Hung(FakeYoutubeDL):
        def download(self, urls):
            time.sleep(2)
            return 0

    install_fake_yt_dlp(monkeypatch, Hung)

    with pytest.raises(DownloadError, match="timed out"):
        download_track(TRACK, "https://example.com/source", tmp_path, timeout=0.2)
