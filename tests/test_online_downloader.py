from pathlib import Path
import sys
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
