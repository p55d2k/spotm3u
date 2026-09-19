"""Tests for FFmpeg resolution and its use by the downloader."""

import sys
import types
from pathlib import Path

import pytest

from spotm3u import ffmpeg
from spotm3u.models import Track

TRACK = Track(title="Song", artists=["Artist"], spotify_id="track-1")


def _make_bundle(monkeypatch, tmp_path, *, contents=("ffmpeg", "ffprobe")):
    bundled = tmp_path / "ffmpeg"
    bundled.mkdir()
    entry = tmp_path / "bin"
    entry.mkdir()
    for name in contents:
        (bundled / name).write_bytes(b"binary")
    monkeypatch.setattr(ffmpeg, "is_frozen", lambda: True)
    monkeypatch.setattr(ffmpeg, "bundle_roots", lambda: (tmp_path,))
    return bundled


def test_bundle_roots_prefers_meipass_then_internal(monkeypatch, tmp_path):
    meipass = tmp_path / "meipass"
    exe = tmp_path / "exe" / "spotm3u"
    exe.mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    roots = ffmpeg.bundle_roots()

    assert roots == (meipass, exe.parent / "_internal", exe.parent)


def test_locate_is_none_in_development(monkeypatch):
    monkeypatch.setattr(ffmpeg, "is_frozen", lambda: False)

    assert ffmpeg.locate_ffmpeg_location() is None


def test_locate_prefers_bundled_ffmpeg_when_frozen(monkeypatch, tmp_path):
    bundled = _make_bundle(monkeypatch, tmp_path)

    assert ffmpeg.locate_ffmpeg_location() == str(bundled)


def test_locate_ignores_empty_bundled_directory(monkeypatch, tmp_path):
    (tmp_path / "ffmpeg").mkdir()
    monkeypatch.setattr(ffmpeg, "is_frozen", lambda: True)
    monkeypatch.setattr(ffmpeg, "bundle_roots", lambda: (tmp_path,))

    assert ffmpeg.locate_ffmpeg_location() is None


def test_locate_finds_ffmpeg_in_onedir_internal(monkeypatch, tmp_path):
    bundled = tmp_path / "ffmpeg"
    bundled.mkdir()
    (bundled / "ffmpeg.exe").write_bytes(b"binary")
    (bundled / "ffprobe.exe").write_bytes(b"binary")
    monkeypatch.setattr(ffmpeg, "is_frozen", lambda: True)
    monkeypatch.setattr(ffmpeg, "bundle_roots", lambda: (tmp_path / "_internal", tmp_path))

    assert ffmpeg.locate_ffmpeg_location() == str(bundled)


def test_locate_accepts_windows_executable_names(monkeypatch, tmp_path):
    bundled = _make_bundle(monkeypatch, tmp_path, contents=("ffmpeg.exe", "ffprobe.exe"))

    assert ffmpeg.locate_ffmpeg_location() == str(bundled)


def test_require_ffmpeg_returns_bundled_location_when_available(monkeypatch, tmp_path):
    bundled = _make_bundle(monkeypatch, tmp_path, contents=("ffmpeg",))

    assert ffmpeg.require_ffmpeg_location() == str(bundled)


def test_require_ffmpeg_raises_actionable_error_without_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(ffmpeg, "is_frozen", lambda: False)
    monkeypatch.setattr(ffmpeg, "bundle_root", lambda: tmp_path)

    with pytest.raises(ffmpeg.FFmpegMissingError) as excinfo:
        ffmpeg.require_ffmpeg_location()

    message = str(excinfo.value)
    assert "FFmpeg" in message
    assert "PATH" in message
    assert str(tmp_path / "ffmpeg") in message


def test_bundled_directory_is_bundle_root_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(ffmpeg, "bundle_root", lambda: tmp_path)

    assert ffmpeg.bundled_directory() == tmp_path / "ffmpeg"


class _RecordingYoutubeDL:
    options = {}

    def __init__(self, options):
        type(self).options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def download(self, urls):
        output = type(self).options["outtmpl"].replace("%(ext)s", "mp3")
        Path(output).write_bytes(b"valid mp3")
        return 0


def test_downloader_forwards_ffmpeg_location_to_yt_dlp(monkeypatch, tmp_path):
    from spotm3u.online import downloader

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_RecordingYoutubeDL))
    monkeypatch.setattr("spotm3u.online.downloader._validate_pot_provider", lambda url, home: None)
    monkeypatch.setattr(
        "spotm3u.online.downloader.validate_downloaded_audio",
        lambda track, path: types.SimpleNamespace(status="valid", reasons=()),
    )
    monkeypatch.setattr(
        "spotm3u.online.downloader.enrich_metadata",
        lambda path, track, download_dir: types.SimpleNamespace(
            artwork_embedded=False,
            artwork_source=None,
            fields_written=(),
            errors=(),
        ),
    )

    ffmpeg_dir = tmp_path / "ffmpeg-dir"
    ffmpeg_dir.mkdir()
    downloader.download_track(
        TRACK,
        "https://example.com/source",
        tmp_path,
        ffmpeg_location=str(ffmpeg_dir),
    )

    assert _RecordingYoutubeDL.options["ffmpeg_location"] == str(ffmpeg_dir)
