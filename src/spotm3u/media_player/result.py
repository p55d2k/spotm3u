"""Shared result types for the platform "Add to Media Player" integrations."""

from __future__ import annotations

from dataclasses import dataclass

# Library imports report how the target playlist was handled by the media
# library. ``created`` means the playlist did not exist and was created,
# ``appended`` means every file was appended to the existing playlist,
# ``skipped-duplicates`` means only files that were not already present were
# appended, and ``cancelled`` means the user cancelled and nothing changed.
IMPORT_ACTIONS = frozenset({"created", "appended", "skipped-duplicates", "cancelled"})

# File handoffs report that the generated playlist file was opened with the
# default associated media player instead of being imported into a library.
FILE_ACTIONS = frozenset({"opened"})

VALID_ACTIONS = IMPORT_ACTIONS | FILE_ACTIONS


class MediaPlayerError(RuntimeError):
    """The generated playlist could not be handed to a media player."""


@dataclass(frozen=True)
class MediaPlayerResult:
    """Outcome of adding a generated playlist to the platform media player.

    ``action`` records what the integration did (one of ``VALID_ACTIONS``).
    ``imported``, ``skipped``, and ``failed`` describe per-entry results when
    the playlist was imported into a media library; a plain file handoff
    reports zeros. ``message`` is the user-facing text for the result page.
    """

    action: str
    imported: int = 0
    failed: int = 0
    skipped: int = 0
    message: str = ""

    @property
    def cancelled(self) -> bool:
        return self.action == "cancelled"

    @property
    def opened(self) -> bool:
        return self.action == "opened"

    @property
    def complete(self) -> bool:
        return self.failed == 0


__all__ = [
    "FILE_ACTIONS",
    "IMPORT_ACTIONS",
    "MediaPlayerError",
    "MediaPlayerResult",
    "VALID_ACTIONS",
]
