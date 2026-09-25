"""Tests for the persisted UI preferences.

The desktop window keeps no WebView storage of its own (pywebview's macOS
backend drops it on exit), so the theme is stored by the application. These
tests pin the parts the UI depends on: a stored theme survives, anything
unknown is refused or ignored, and storage problems degrade to "nothing stored"
instead of failing a request.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spotm3u import preferences


@pytest.fixture(autouse=True)
def _state_dir(monkeypatch, tmp_path):
    """Keep every test's preferences inside its own temporary directory."""
    monkeypatch.setenv(preferences.STATE_DIR_ENV, str(tmp_path / "state"))


def test_read_preferences_is_empty_before_anything_is_stored() -> None:
    assert preferences.read_preferences() == {}


def test_write_preferences_round_trips_a_theme() -> None:
    assert preferences.write_preferences({"theme": "dark"}) is True

    assert preferences.read_preferences() == {"theme": "dark"}
    stored = json.loads(preferences.preferences_path().read_text(encoding="utf-8"))
    assert stored == {"theme": "dark"}


def test_write_preferences_merges_instead_of_replacing() -> None:
    preferences.write_preferences({"theme": "dark"})

    assert preferences.write_preferences({"theme": "light"}) is True

    assert preferences.read_preferences() == {"theme": "light"}


def test_invalid_values_are_never_stored() -> None:
    assert preferences.write_preferences({"theme": "neon"}) is False

    assert preferences.read_preferences() == {}
    assert not preferences.preferences_path().exists()


def test_unknown_keys_are_not_stored() -> None:
    assert preferences.write_preferences({"colour": "red"}) is False
    assert preferences.read_preferences() == {}


def test_hand_edited_files_are_filtered_on_read() -> None:
    path = preferences.preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"theme": "sideways", "colour": "red", "volume": 11}), encoding="utf-8"
    )

    assert preferences.read_preferences() == {}


def test_a_corrupt_file_reads_as_nothing_stored() -> None:
    path = preferences.preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert preferences.read_preferences() == {}


def test_a_non_object_file_reads_as_nothing_stored() -> None:
    path = preferences.preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert preferences.read_preferences() == {}


def test_an_unwritable_location_reports_failure_without_raising(tmp_path, monkeypatch) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv(preferences.STATE_DIR_ENV, str(blocker / "state"))

    assert preferences.write_preferences({"theme": "dark"}) is False
    assert preferences.read_preferences() == {}


def test_the_stored_file_is_validated_by_key_and_value() -> None:
    assert preferences.is_valid("theme", "light")
    assert preferences.is_valid("theme", "dark")
    assert not preferences.is_valid("theme", "LIGHT")
    assert not preferences.is_valid("theme", "system")
    assert not preferences.is_valid("theme", None)
    assert not preferences.is_valid("colour", "red")


def test_preferences_live_in_the_state_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(preferences.STATE_DIR_ENV, str(tmp_path / "elsewhere"))

    assert (
        preferences.preferences_path() == tmp_path / "elsewhere" / preferences.PREFERENCES_FILENAME
    )


def test_preferences_otherwise_live_in_the_application_data_directory(monkeypatch) -> None:
    monkeypatch.delenv(preferences.STATE_DIR_ENV, raising=False)
    monkeypatch.setattr(preferences, "user_data_dir", lambda: Path("/data/SpotM3U"))

    assert (
        preferences.preferences_path() == Path("/data/SpotM3U") / preferences.PREFERENCES_FILENAME
    )
