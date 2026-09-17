"""Validation of audio produced by an online download."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import wave
from typing import Literal

from ..models import Track

AudioValidationStatus = Literal["valid", "invalid", "uncertain"]

_CONTENT_WARNING_RE = re.compile(
    r"\b(?:speech|spoken|dialogue|dialog|interview|movie|scene|trailer|"
    r"podcast|reaction|sound effects?|soundtrack)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AudioValidation:
    """Conservative validation result for a downloaded audio file."""

    path: Path
    status: AudioValidationStatus
    reasons: tuple[str, ...] = ()
    duration_s: float | None = None
    format: str | None = None
    metadata: dict[str, str] | None = None

    @property
    def valid(self) -> bool:
        return self.status == "valid"


def validate_downloaded_audio(track: Track, path: str | Path) -> AudioValidation:
    """Inspect a downloaded file without claiming it contains music only."""
    audio_path = Path(path)
    if not audio_path.is_file():
        return _invalid(audio_path, "file does not exist")
    try:
        if audio_path.stat().st_size == 0:
            return _invalid(audio_path, "empty file")
    except OSError:
        return _invalid(audio_path, "file is unreadable")

    try:
        from mutagen import File  # type: ignore
        from mutagen import MutagenError  # type: ignore

        parsed = File(audio_path, easy=True)
    except (MutagenError, OSError, TypeError, ValueError):
        parsed = None
    except ImportError:
        return _uncertain(audio_path, "audio inspection library is unavailable")

    if parsed is None or getattr(parsed, "info", None) is None:
        return _invalid(audio_path, "unreadable file or no audio stream")

    info = parsed.info
    duration = _number(getattr(info, "length", None))
    if duration is None or duration <= 0:
        return _invalid(audio_path, "missing or invalid duration")

    audio_format = type(info).__name__ or audio_path.suffix.lstrip(".").lower() or None
    metadata = _metadata(parsed)
    reasons: list[str] = []

    expected = track.duration_ms / 1000 if track.duration_ms else None
    if expected and abs(duration - expected) > max(3.0, expected * 0.08):
        return AudioValidation(
            audio_path,
            "invalid",
            ("duration mismatch",),
            duration,
            audio_format,
            metadata,
        )

    content_text = " ".join((*metadata.values(), audio_path.stem))
    if _CONTENT_WARNING_RE.search(content_text):
        reasons.append("suspected speech, dialogue, or sound effects")

    if _is_silent_wav(audio_path):
        return AudioValidation(
            audio_path,
            "invalid",
            ("file contains no audible samples",),
            duration,
            audio_format,
            metadata,
        )

    return AudioValidation(
        audio_path,
        "uncertain" if reasons else "valid",
        tuple(reasons),
        duration,
        audio_format,
        metadata,
    )


def _metadata(parsed: object) -> dict[str, str]:
    tags = getattr(parsed, "tags", None)
    if not tags:
        return {}
    values: dict[str, str] = {}
    for key in ("title", "artist", "album", "comment", "description", "genre"):
        value = tags.get(key)
        if value:
            values[key] = str(value)
    return values


def _is_silent_wav(path: Path) -> bool:
    if path.suffix.casefold() != ".wav":
        return False
    try:
        with wave.open(str(path), "rb") as source:
            frames = source.readframes(min(source.getnframes(), source.getframerate() * 10))
            return bool(frames) and max(frames) == min(frames)
    except (OSError, wave.Error):
        return False


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _invalid(path: Path, reason: str) -> AudioValidation:
    return AudioValidation(path, "invalid", (reason,))


def _uncertain(path: Path, reason: str) -> AudioValidation:
    return AudioValidation(path, "uncertain", (reason,))


__all__ = ["AudioValidation", "AudioValidationStatus", "validate_downloaded_audio"]
