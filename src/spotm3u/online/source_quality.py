"""Source intent detection for online candidates.

Separates *recording identity* (is this the requested song by the requested
artist?) from *source quality* (is this upload a good way to extract the clean
song audio?).

Source-quality preference is a ranking preference applied only among
candidates that already appear to be the correct recording. It is not an
identity rule and never overrides an artist mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import re

from ..normalization import normalize_cjk
from .search import SourceCandidate

# Ordered from worst to best as audio-oriented sources for the same recording.
class SourceQuality(IntEnum):
    REJECTED = 0
    COVER_ALTERNATE = 1
    PERFORMANCE = 2
    GENERIC = 3
    OFFICIAL_MV = 4
    OFFICIAL = 5
    LYRICS = 6
    OFFICIAL_AUDIO = 7


_NON_MUSIC_RE = re.compile(
    r"\b(?:reaction|interview|dialogue|dialog|movie|film|scene|trailer|teaser|"
    r"compilation|documentary|podcast|speech)\b",
    re.IGNORECASE,
)

_ALTERNATE_RE = re.compile(
    r"\b(?:cover|翻唱|karaoke|remix|mashup|parody|sped(?:[ -]up)|\bslowed\b|"
    r"nightcore|\b8d\b)\b",
    re.IGNORECASE,
)

_PERFORMANCE_RE = re.compile(
    r"\b(?:live|现场|演唱会|concert|acoustic|unplugged)\b",
    re.IGNORECASE,
)

_LYRICS_RE = re.compile(r"\b(?:lyric|lyrics|歌词)\b", re.IGNORECASE)

_AUDIO_RE = re.compile(r"\b(?:audio|音频|official\s+audio)\b", re.IGNORECASE)

_VIDEO_RE = re.compile(
    r"\b(?:official\s+(?:music\s+)?videos?|music\s+videos?|official\s*mv\b|"
    r"\bmv\b|video\s+clip|videoclip)\b",
    re.IGNORECASE,
)

_OFFICIAL_RE = re.compile(r"\b(?:official|官方|topic|vevo)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SourceProfile:
    """What a candidate upload appears to be, independent of its artist."""

    quality: SourceQuality
    alternate: tuple[str, ...] = ()
    performance: tuple[str, ...] = ()
    non_music: tuple[str, ...] = ()
    music_video: bool = False
    lyrics: bool = False
    audio: bool = False
    official: bool = False
    instrumental: bool = False
    vocal: bool = False
    markers: tuple[str, ...] = ()

    @property
    def always_reject(self) -> bool:
        return self.quality == SourceQuality.REJECTED


def source_profile(candidate: SourceCandidate) -> SourceProfile:
    """Classify a candidate into identity-independent source-quality tiers."""
    text = _intent_text(candidate)
    instrumental = bool(re.search(r"\b(?:instrumental|inst\.?|no vocals?)\b", text, re.IGNORECASE))
    vocal = bool(re.search(r"\b(?:vocal(?:s)?|with vocals?)\b", text, re.IGNORECASE))
    non_music = tuple(dict.fromkeys(marker for marker in _NON_MUSIC_RE.findall(text) if marker))
    alternate = _match(_ALTERNATE_RE, text)
    performance = _match(_PERFORMANCE_RE, text)
    markers = tuple(
        dict.fromkeys(
            marker
            for marker in (*non_music, *alternate, *performance)
            if marker
        )
    )

    if non_music:
        quality = SourceQuality.REJECTED
        audio = lyrics = official = False
        music_video = False
    elif alternate:
        quality = SourceQuality.COVER_ALTERNATE
        audio = lyrics = official = False
        music_video = False
    elif performance:
        quality = SourceQuality.PERFORMANCE
        audio = lyrics = official = False
        music_video = False
    else:
        audio = bool(_AUDIO_RE.search(text))
        lyrics = bool(_LYRICS_RE.search(text)) and not audio
        music_video = bool(_VIDEO_RE.search(text))
        official = bool(_OFFICIAL_RE.search(text))
        if audio and not music_video:
            quality = SourceQuality.OFFICIAL_AUDIO
        elif lyrics and not music_video:
            quality = SourceQuality.LYRICS
        elif music_video:
            quality = SourceQuality.OFFICIAL_MV
        elif official:
            quality = SourceQuality.OFFICIAL
        else:
            quality = SourceQuality.GENERIC

    return SourceProfile(
        quality=quality,
        alternate=alternate,
        performance=performance,
        non_music=non_music,
        music_video=music_video or quality == SourceQuality.OFFICIAL_MV,
        lyrics=lyrics,
        audio=audio,
        official=official,
        instrumental=instrumental,
        vocal=vocal,
        markers=tuple(markers),
    )


def quality_points(quality: SourceQuality) -> float:
    """Return the score contribution for a source-quality tier."""
    return {
        SourceQuality.REJECTED: 0,
        SourceQuality.COVER_ALTERNATE: 0,
        SourceQuality.PERFORMANCE: 1,
        SourceQuality.GENERIC: 4,
        SourceQuality.OFFICIAL_MV: 6,
        SourceQuality.OFFICIAL: 8,
        SourceQuality.LYRICS: 10,
        SourceQuality.OFFICIAL_AUDIO: 12,
    }[quality]


def quality_label(quality: SourceQuality) -> str:
    return {
        SourceQuality.REJECTED: "rejected content",
        SourceQuality.COVER_ALTERNATE: "cover or alternate version",
        SourceQuality.PERFORMANCE: "live/performance version",
        SourceQuality.GENERIC: "generic upload",
        SourceQuality.OFFICIAL_MV: "official music video",
        SourceQuality.OFFICIAL: "official song upload",
        SourceQuality.LYRICS: "lyrics version",
        SourceQuality.OFFICIAL_AUDIO: "official audio",
    }[quality]


def _intent_text(candidate: SourceCandidate) -> str:
    metadata = candidate.metadata or {}
    tags = metadata.get("tags")
    if isinstance(tags, (list, tuple)):
        tags_text = " ".join(str(tag) for tag in tags)
    else:
        tags_text = str(tags) if tags else ""
    parts = (
        candidate.title,
        candidate.uploader,
        candidate.artist,
        candidate.source_type,
        str(metadata.get("channel") or ""),
        str(metadata.get("channel_id") or ""),
        str(metadata.get("creator") or ""),
        str(metadata.get("description") or ""),
        str(metadata.get("category") or ""),
        str(metadata.get("genre") or ""),
        tags_text,
    )
    return normalize_cjk(" ".join(part for part in parts if part))


def _match(pattern: re.Pattern, text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match for match in pattern.findall(text) if match))


__all__ = [
    "SourceProfile",
    "SourceQuality",
    "quality_label",
    "quality_points",
    "source_profile",
]