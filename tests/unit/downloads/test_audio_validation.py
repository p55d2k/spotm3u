import sys
import types
import wave
from pathlib import Path

from spotm3u.models import Track
from spotm3u.online import validate_downloaded_audio

TRACK = Track("Song", ["Artist"], duration_ms=200_000)


class Info:
    length = 200


class Parsed:
    info = Info()
    tags = {"title": ["Song"], "artist": ["Artist"]}


def install_mutagen(monkeypatch, parsed):
    monkeypatch.setitem(
        sys.modules,
        "mutagen",
        types.SimpleNamespace(File=lambda *a, **k: parsed, MutagenError=Exception),
    )


def test_valid_audio_is_valid_and_metadata_is_optional(tmp_path, monkeypatch):
    path = tmp_path / "song.mp3"
    path.write_bytes(b"audio")
    install_mutagen(monkeypatch, Parsed())

    result = validate_downloaded_audio(TRACK, path)

    assert result.status == "valid"
    assert result.duration_s == 200


def test_duration_mismatch_is_invalid(tmp_path, monkeypatch):
    path = tmp_path / "song.mp3"
    path.write_bytes(b"audio")
    parsed = types.SimpleNamespace(info=types.SimpleNamespace(length=20), tags=None)
    install_mutagen(monkeypatch, parsed)

    assert validate_downloaded_audio(TRACK, path).status == "invalid"


def test_moderate_duration_difference_is_a_warning(tmp_path, monkeypatch):
    path = tmp_path / "song.mp3"
    path.write_bytes(b"audio")
    parsed = types.SimpleNamespace(info=types.SimpleNamespace(length=180), tags=None)
    install_mutagen(monkeypatch, parsed)

    result = validate_downloaded_audio(TRACK, path)

    assert result.status == "valid"
    assert "moderate duration difference" in result.reasons


def test_corrupt_and_missing_files_are_invalid(tmp_path, monkeypatch):
    install_mutagen(monkeypatch, None)
    corrupt = tmp_path / "corrupt.mp3"
    corrupt.write_bytes(b"not audio")

    assert validate_downloaded_audio(TRACK, corrupt).status == "invalid"
    assert validate_downloaded_audio(TRACK, Path("missing.mp3")).status == "invalid"


def test_obvious_speech_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "dialogue.mp3"
    path.write_bytes(b"audio")
    parsed = types.SimpleNamespace(
        info=types.SimpleNamespace(length=200),
        tags={"comment": ["spoken dialogue"]},
    )
    install_mutagen(monkeypatch, parsed)

    result = validate_downloaded_audio(TRACK, path)

    assert result.status == "invalid"
    assert "dialogue" in result.reasons[0]


def test_cinematic_metadata_warning_does_not_reject_audio(tmp_path, monkeypatch):
    path = tmp_path / "cornfield-chase.mp3"
    path.write_bytes(b"audio")
    parsed = types.SimpleNamespace(
        info=types.SimpleNamespace(length=200),
        tags={"genre": ["soundtrack"]},
    )
    install_mutagen(monkeypatch, parsed)

    result = validate_downloaded_audio(TRACK, path)

    assert result.status == "valid"
    assert result.reasons == ("suspected speech, dialogue, or sound effects",)


def test_silent_audio_is_invalid(tmp_path, monkeypatch):
    path = tmp_path / "silent.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(10)
        output.writeframes(b"\0\0" * 20)
    install_mutagen(monkeypatch, types.SimpleNamespace(info=Info(), tags=None))

    assert validate_downloaded_audio(TRACK, path).status == "invalid"
