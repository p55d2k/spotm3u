"""UI preferences that outlive a run (the chosen theme, and whatever follows it).

The pages keep their theme in ``localStorage``, which is fine for a browser and
useless for the desktop shell: pywebview's macOS backend ignores ``storage_path``
and drops every WebView cookie and localStorage entry when the process exits, so
a theme chosen in the sidebar came back reset on every single launch. A
preference the user set deliberately therefore belongs to the application,
which owns a small JSON file it can always write and read again.

The store is forgiving on purpose. A preference is never worth an error: an
unreadable, malformed, or partly invalid file reads as "nothing stored", and a
failed write is reported (``False``) rather than raised, leaving the previously
stored values in place. Only known keys with known values are ever kept, so a
hand-edited file cannot push arbitrary state into the application.

Location: ``preferences.json`` under :func:`spotm3u.runtime.user_data_dir`, or
under ``SPOTM3U_STATE_DIR`` when that is set (used by tests and by a one-off run
that must not touch the user's real preferences).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path

from .runtime import user_data_dir

logger = logging.getLogger(__name__)

PREFERENCES_FILENAME = "preferences.json"

# Directory override, for tests and for running without touching real state.
STATE_DIR_ENV = "SPOTM3U_STATE_DIR"

# The theme as the UI can choose it. ``light`` and ``dark`` are explicit
# choices; "follow the system" is the absence of a stored theme rather than a
# third value, because that is also what a fresh install has.
THEMES: tuple[str, ...] = ("light", "dark")

# Every stored preference: the key and the values it accepts. Anything else in
# the file is ignored on read and refused on write.
_ALLOWED_VALUES: dict[str, tuple[str, ...]] = {"theme": THEMES}

# The preference names the store knows, for callers that reject unknown ones.
PREFERENCE_KEYS: tuple[str, ...] = tuple(_ALLOWED_VALUES)


def preferences_path() -> Path:
    """The file the UI preferences are stored in."""
    override = os.environ.get(STATE_DIR_ENV)
    directory = Path(override).expanduser() if override else user_data_dir()
    return directory / PREFERENCES_FILENAME


def is_valid(key: str, value: object) -> bool:
    """Whether ``value`` is an accepted value for the preference ``key``."""
    allowed = _ALLOWED_VALUES.get(key)
    return isinstance(value, str) and allowed is not None and value in allowed


def read_preferences() -> dict[str, str]:
    """Return the stored preferences, ignoring anything unknown or malformed.

    An empty mapping means "nothing has been chosen yet", which is also what a
    missing, unreadable, or corrupt file means: the caller falls back to its
    default rather than surfacing a storage problem.
    """
    path = preferences_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.debug("ignoring unreadable preferences at %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        logger.debug("ignoring preferences at %s: expected a JSON object", path)
        return {}
    return {
        key: value for key, value in raw.items() if isinstance(key, str) and is_valid(key, value)
    }


def write_preferences(values: Mapping[str, str]) -> bool:
    """Store ``values``, keeping everything already stored, and report success.

    Unknown keys and invalid values are ignored (they are never written), the
    existing file's contents are preserved so a future preference is not lost by
    an older screen saving its own, and the write is atomic so a crash cannot
    leave a half-written file behind. Returns ``False`` when the file could not
    be written, having logged why; the caller's workflow continues either way.
    """
    accepted = {key: value for key, value in values.items() if is_valid(key, value)}
    if not accepted:
        return False
    merged = {**read_preferences(), **accepted}
    path = preferences_path()
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        logger.warning("could not save preferences to %s: %s", path, exc)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best-effort cleanup
            logger.debug("could not remove the temporary preferences file %s", temporary)
        return False
    return True


__all__ = [
    "PREFERENCES_FILENAME",
    "PREFERENCE_KEYS",
    "STATE_DIR_ENV",
    "THEMES",
    "is_valid",
    "preferences_path",
    "read_preferences",
    "write_preferences",
]
