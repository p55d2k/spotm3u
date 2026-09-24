"""Explicit metadata work items for the download and enrichment pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .models import Track

if TYPE_CHECKING:
    from .metadata import MetadataResult


@dataclass(frozen=True)
class MetadataJob:
    """Metadata work that can be queued independently of a resolver worker.

    ``audio_path`` may be assigned only once the download completes. Keeping
    the track and destination on the job means a scheduler can retain the
    work item while audio and metadata progress independently.
    """

    track: Track
    download_dir: Path
    audio_path: Path | None = None

    def run(self) -> MetadataResult:
        """Apply this job to its audio file and return an explicit result."""
        if self.audio_path is None:
            raise ValueError("metadata job requires an audio path before it can run")
        from .metadata import enrich_metadata

        return enrich_metadata(self.audio_path, self.track, self.download_dir)

    def with_audio_path(self, audio_path: str | Path) -> MetadataJob:
        """Return the same pending job with its completed audio path attached."""
        return MetadataJob(self.track, self.download_dir, Path(audio_path))


__all__ = ["MetadataJob"]
