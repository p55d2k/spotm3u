"""Permissive metadata ranking for online source candidates.

Two questions are evaluated independently:

A. Is this the requested song/recording by the requested artist?
   -> recording identity (artist evidence across title/explicit/uploader/description)
B. Is this upload a good source for the song audio?
   -> source-quality preference (official audio > lyrics > official song > MV > generic)

A wrong artist outranks every title/source-quality advantage. The source-quality
preference only operates strongly among candidates that already appear to be
the correct recording. Discovery stays permissive: missing metadata
is not proof of a wrong candidate.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from ..models import Track
from ..normalization import normalize_artists, normalize_cjk
from .search import SourceCandidate
from .source_quality import quality_label, quality_points, source_profile

logger = logging.getLogger(__name__)

Confidence = Literal["strong", "plausible", "uncertain", "rejected"]

_AUDIO_LABEL_RE = re.compile(
    r"\b(?:official\s+audio|official\s+music\s+videos?|official\s+video|"
    r"official\s*mv\b|official|\bmv\b|audio|lyric\s+video|lyrics|歌词)\b",
    re.IGNORECASE,
)
_REMASTER_RE = re.compile(r"\bremaster(?:ed)?\b", re.IGNORECASE)
_VERSION_KEYS = (
    "live",
    "现场",
    "演唱会",
    "concert",
    "acoustic",
    "instrumental",
    "vocal",
    "radio edit",
    "extended",
    "deluxe",
    "single version",
    "demo",
    "reprise",
    "remix",
    "cover",
    "翻唱",
)
_CONFLICT_MARKERS = frozenset(_VERSION_KEYS)
_VERSION_KEY_PATTERNS = tuple(
    (key, re.compile(rf"\b{re.escape(key)}\b", re.IGNORECASE)) for key in _VERSION_KEYS
)

_POSITIVE_SOURCE_RE = re.compile(
    r"\b(?:official\s+audio|official|audio|topic|artist|records?|vevo)\b",
    re.IGNORECASE,
)

# Title-only evidence that the requested artist appears somewhere in the title.
# Kept as a lightweight helper; recording identity primarily uses the artist
# evidence tiers computed from candidate fields.


@dataclass(frozen=True)
class ScoreComponents:
    """Separated scoring dimensions for one candidate decision."""

    identity: float
    title: float
    version: float
    duration: float
    source_quality: float
    uploader: float
    penalties: float
    total: float

    def as_dict(self) -> dict[str, float]:
        return {
            "identity": round(self.identity, 2),
            "title": round(self.title, 2),
            "version": round(self.version, 2),
            "duration": round(self.duration, 2),
            "source_quality": round(self.source_quality, 2),
            "uploader": round(self.uploader, 2),
            "penalties": round(self.penalties, 2),
            "total": round(self.total, 2),
        }


@dataclass(frozen=True)
class CandidateRanking:
    """A candidate's score and decision, without accepting it implicitly."""

    candidate: SourceCandidate
    score: float
    confidence: Confidence
    accepted: bool
    reasons: tuple[str, ...] = ()
    components: ScoreComponents | None = None


def rank_source_candidates(
    track: Track, candidates: list[SourceCandidate] | tuple[SourceCandidate, ...]
) -> tuple[CandidateRanking, ...]:
    """Rank candidates from most to least plausible for ``track``."""
    return tuple(
        sorted(
            (rank_source_candidate(track, candidate) for candidate in candidates),
            key=lambda result: result.score,
            reverse=True,
        )
    )


def rank_source_candidate(track: Track, candidate: SourceCandidate) -> CandidateRanking:
    """Rank a single candidate: identity first, then source quality."""
    requested_core, requested_versions = split_title(track.title)
    candidate_core, candidate_versions = split_title(candidate.title)
    candidate_text = _candidate_text(candidate)
    profile = source_profile(candidate)
    requested_instrumental = _is_instrumental_title(track.title)
    requested_vocal = _is_vocal_title(track.title)
    requested_artists = _artist_keys(track)

    # Strip the requested artist's name from candidate-title cores so that
    # ``薛之谦 演员`` and a bare ``演员`` compare against the same underlying
    # identity (an artist attribution is never a title difference).
    candidate_core_for_title = _strip_artist_phrases(candidate_core, requested_artists)
    requested_core_for_title = _strip_artist_phrases(requested_core, requested_artists)

    candidate_artist_text = _artist_text(candidate.artist) if candidate.artist else ""
    creator_text = (
        _artist_text(candidate.metadata.get("creator")) if candidate.metadata.get("creator") else ""
    )
    uploader_text = _artist_text(candidate.uploader) if candidate.uploader else ""
    channel_text = (
        _artist_text(candidate.metadata.get("channel")) if candidate.metadata.get("channel") else ""
    )
    title_text = _cjk_text(candidate.title)
    description_text = " ".join(
        part
        for part in (
            _cjk_text(str(candidate.metadata.get("description") or "")),
            _cjk_text(" ".join(str(tag) for tag in (candidate.metadata.get("tags") or []) if tag)),
        )
        if part
    )

    title_similarity = _similarity(requested_core_for_title, candidate_core_for_title)

    artist_in_explicit = _artist_present(
        requested_artists, f"{candidate_artist_text} {creator_text}"
    )
    artist_in_title = _artist_present(requested_artists, title_text)
    artist_in_uploader = _artist_present(requested_artists, f"{uploader_text} {channel_text}")
    artist_in_description = _artist_present(requested_artists, description_text)

    if artist_in_explicit or artist_in_title:
        identity_evidence = 1.0
    elif artist_in_uploader:
        identity_evidence = 0.7
    elif artist_in_description:
        identity_evidence = 0.3
    else:
        identity_evidence = 0.0

    reasons: list[str] = []
    if title_similarity >= 0.98:
        reasons.append("title matches")
    elif title_similarity < 0.45:
        reasons.append("title mismatch")

    if artist_in_explicit:
        reasons.append("artist matches (explicit metadata)")
    elif artist_in_title:
        reasons.append("artist matches (title attribution)")
    elif artist_in_uploader:
        reasons.append("artist matches (uploader/channel)")
    elif artist_in_description:
        reasons.append("artist matches (description/tags)")
    elif requested_artists:
        if candidate_artist_text:
            reasons.append("artist mismatch")
        elif uploader_text:
            reasons.append("uploader differs (artist identity not confirmed)")
        else:
            reasons.append("artist identity not confirmed")

    identity_score = 40 * identity_evidence
    title_score = 35 * title_similarity

    duration_score = 0.0
    if track.duration_ms and candidate.duration_s is not None:
        difference = abs(candidate.duration_s - track.duration_ms / 1000)
        if difference <= 5:
            duration_score = 10
            reasons.append("duration matches")
        elif difference <= 15:
            duration_score = 7
            reasons.append("duration is close")
        elif difference <= 40:
            duration_score = 3
        else:
            duration_score = -15
            reasons.append("duration differs substantially")
    elif candidate.duration_s is None:
        reasons.append("duration is unavailable")

    source_quality_score = quality_points(profile.quality)
    source_label = quality_label(profile.quality)
    reasons.append(f"source quality: {source_label}")

    version_score = 0.0
    if requested_versions and requested_versions <= candidate_versions:
        version_score += 5.0
    if requested_instrumental:
        if profile.instrumental:
            version_score += 12.0
            reasons.append("instrumental version matches")
        elif profile.vocal:
            version_score -= 20.0
            reasons.append("vocal version conflicts with instrumental request")
        else:
            reasons.append("instrumental status unavailable")
    elif requested_vocal and profile.instrumental:
        version_score -= 12.0
        reasons.append("instrumental version conflicts with vocal request")

    uploader_score = 3.0 if artist_in_uploader or artist_in_explicit else 0.0

    music_video = profile.music_video
    penalties = 0.0
    if music_video:
        penalties -= 2
        reasons.append("music video indicator")

    album_key = _cjk_text(track.album) if track.album else ""
    if album_key and album_key in candidate_text:
        identity_score += 3
        reasons.append("album metadata matches")
    if _POSITIVE_SOURCE_RE.search(candidate_text):
        reasons.append("audio or official source indicator")

    non_music_markers = profile.non_music
    if non_music_markers:
        reasons.append("non-music content indicator: " + ", ".join(non_music_markers))
    alt_markers = profile.alternate
    if alt_markers:
        reasons.append("alternate version indicator: " + ", ".join(alt_markers))
    performance_markers = profile.performance
    if performance_markers:
        reasons.append("live/performance indicator: " + ", ".join(performance_markers))

    conflict = _version_conflict(requested_versions, candidate_versions)
    if conflict:
        reasons.append(f"version conflict: {conflict}")

    score = (
        identity_score
        + title_score
        + version_score
        + duration_score
        + source_quality_score
        + uploader_score
        + penalties
    )
    score = max(0.0, min(100.0, score))

    alternate_wrong = any(
        not _requested_allows_marker(track.title, marker, requested_versions)
        for marker in alt_markers
    )
    performance_wrong = any(
        not _requested_allows_marker(track.title, marker, requested_versions)
        for marker in performance_markers
    )
    version_wrong = (
        conflict is not None
        and not _requested_has_marker(track.title, conflict)
        and not (requested_instrumental and conflict == "vocal")
        and not (requested_vocal and conflict == "instrumental")
    )
    instrumental_wrong = requested_instrumental and profile.vocal and not profile.instrumental
    vocal_wrong = requested_vocal and profile.instrumental
    explicit_conflict = bool(
        candidate_artist_text
        and not artist_in_explicit
        and not artist_in_title
        and title_similarity >= 0.5
    )
    duration_wrong = bool(
        track.duration_ms
        and candidate.duration_s is not None
        and abs(candidate.duration_s - track.duration_ms / 1000) > 60
    )
    weak_evidence = title_similarity < 0.35 or (title_similarity < 0.5 and score < 30)

    rejection_reason: str | None = None
    if weak_evidence:
        rejection_reason = "title does not match the requested track"
    elif explicit_conflict:
        rejection_reason = "explicit artist conflicts with requested artist"
    elif non_music_markers:
        rejection_reason = "non-music content indicator"
    elif alternate_wrong:
        rejection_reason = (
            "alternate version indicator"
            if not alt_markers
            else f"alternate version indicator: {', '.join(alt_markers)}"
        )
    elif performance_wrong:
        rejection_reason = "live/performance version conflicts with requested version"
    elif version_wrong:
        rejection_reason = f"version conflict: {conflict}"
    elif instrumental_wrong:
        rejection_reason = "vocal version conflicts with instrumental request"
    elif vocal_wrong:
        rejection_reason = "instrumental version conflicts with vocal request"
    elif duration_wrong:
        rejection_reason = "duration differs substantially"

    if rejection_reason is not None:
        reasons.append(rejection_reason)
        confidence: Confidence = "rejected"
        accepted = False
    elif (
        title_similarity >= 0.85
        and identity_evidence >= 0.7
        and score >= 70
        and not non_music_markers
        and not music_video
        and not alternate_wrong
        and not performance_wrong
        and not instrumental_wrong
        and not vocal_wrong
    ):
        confidence = "strong"
        accepted = True
    elif (
        not non_music_markers
        and not alternate_wrong
        and not performance_wrong
        and not version_wrong
        and not instrumental_wrong
        and not vocal_wrong
    ):
        confidence = "plausible"
        accepted = True
    else:
        confidence = "uncertain"
        accepted = False

    components = ScoreComponents(
        identity=identity_score,
        title=title_score,
        version=version_score,
        duration=duration_score,
        source_quality=source_quality_score,
        uploader=uploader_score,
        penalties=penalties,
        total=score,
    )

    logger.debug(
        "rank url=%s query=%s confidence=%s accepted=%s score=%.2f "
        "components=%s artist_evidence=%.2f (explicit=%s title=%s uploader=%s desc=%s) "
        "source_quality=%s reasons=%s",
        candidate.url,
        candidate.source_query,
        confidence,
        accepted,
        score,
        components.as_dict(),
        identity_evidence,
        artist_in_explicit,
        artist_in_title,
        artist_in_uploader,
        artist_in_description,
        source_label,
        "; ".join(reasons),
    )
    return CandidateRanking(
        candidate, round(score, 2), confidence, accepted, tuple(reasons), components
    )


def split_title(value: str | None) -> tuple[str, frozenset[str]]:
    """Return (core title, version markers) with labels and versions removed."""
    if not value:
        return "", frozenset()
    title = _AUDIO_LABEL_RE.sub(" ", _cjk_text(value))
    versions: set[str] = set()
    for key, pattern in _VERSION_KEY_PATTERNS:
        if pattern.search(title):
            versions.add(key)
    if _REMASTER_RE.search(title):
        versions.add("remastered")
    for _, pattern in _VERSION_KEY_PATTERNS:
        title = pattern.sub(" ", title)
    title = _REMASTER_RE.sub(" ", title)
    return _collapse(title), frozenset(versions)


def _version_conflict(requested: frozenset[str], candidate: frozenset[str]) -> str | None:
    conflicting = (candidate & _CONFLICT_MARKERS) - requested
    return min(conflicting) if conflicting else None


def _requested_allows_marker(
    requested_title: str, marker: str, requested_versions: frozenset[str]
) -> bool:
    return marker in _cjk_text(requested_title) or marker in requested_versions


def _requested_has_marker(requested_title: str, marker: str) -> bool:
    return marker in _cjk_text(requested_title)


def _is_instrumental_title(title: str | None) -> bool:
    return bool(
        re.search(
            r"\b(?:instrumental(?:\s+version)?|inst\.?|no vocals?)\b", title or "", re.IGNORECASE
        )
    )


def _is_vocal_title(title: str | None) -> bool:
    return bool(re.search(r"\b(?:vocal(?:\s+version)?|with vocals?)\b", title or "", re.IGNORECASE))


def _candidate_text(candidate: SourceCandidate) -> str:
    tags = candidate.metadata.get("tags")
    if isinstance(tags, (list, tuple)):
        tags_text = " ".join(str(tag) for tag in tags)
    else:
        tags_text = str(tags) if tags else ""
    values = (
        candidate.title,
        candidate.artist,
        candidate.uploader,
        candidate.source_type,
        candidate.metadata.get("creator"),
        candidate.metadata.get("channel"),
        candidate.metadata.get("description"),
        candidate.metadata.get("category"),
        candidate.metadata.get("genre"),
        tags_text,
    )
    return _cjk_text(" ".join(str(value) for value in values if value))


def _artist_present(keys: list[str], text: str) -> bool:
    """True when a requested artist identity appears in ``text``.

    Substring matching handles CJK artist names such as ``薛之谦`` and channel
    suffixes such as ``Oasis - Topic`` without requiring exact equality.
    ``text`` is expected to be CJK-normalized already.
    """
    if not keys or not text:
        return False
    return any(key in text for key in keys)


def _artist_keys(track: Track) -> list[str]:
    """Return request-artist identity keys, CJK script-normalized."""
    return [key for artist in track.artists if (key := _artist_text(artist))]


def _artist_text(value: str) -> str:
    """Normalize an artist/uploader/channel string for identity matches."""
    return _cjk_text(normalize_artists(value))


def _cjk_text(value: str | None) -> str:
    """Return a CJK-script-normalized comparison string."""
    return normalize_cjk(value)


def _strip_artist_phrases(core: str, artist_keys: list[str]) -> str:
    """Remove requested-artist names from a title core.

    YouTube titles often embed the artist (``薛之谦 演员``). The artist name is
    identity evidence, not title text, so removing it lets a bare ``演员`` and
    ``薛之谦 演员`` compare against the same underlying identity.
    """
    if not core or not artist_keys:
        return core
    result = core
    for artist in sorted(artist_keys, key=len, reverse=True):
        if artist:
            result = result.replace(artist, " ")
    return _collapse(result)


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    ratio = SequenceMatcher(None, left, right).ratio()
    containment = _token_containment(left, right)
    return max(ratio, containment)


def _token_containment(left: str, right: str) -> float:
    """1.0 when every ``left`` token appears in order within ``right``.

    A requested core such as ``演员`` is considered a full title match when it
    appears in a stripped candidate core such as ``joker xue 演员``, even when
    leftover non-title tokens (romanized artist names, extra words) remain.
    """
    left_tokens = left.split()
    right_tokens = right.split()
    if not left_tokens:
        return 0.0
    needle_idx = 0
    for tok in right_tokens:
        if needle_idx < len(left_tokens) and tok == left_tokens[needle_idx]:
            needle_idx += 1
            if needle_idx == len(left_tokens):
                return 1.0
    return 0.0


def _collapse(value: str) -> str:
    return " ".join(value.split())


__all__ = [
    "CandidateRanking",
    "Confidence",
    "ScoreComponents",
    "rank_source_candidate",
    "rank_source_candidates",
    "split_title",
]
