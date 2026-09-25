"""Tests for the runtime environment helpers.

The per-user data directory is where the application keeps state it owns (the
WebView's storage directory and the saved UI preferences), so it follows each
platform's convention rather than a hardcoded path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spotm3u import runtime


@pytest.mark.parametrize(
    ("platform_name", "environ", "expected"),
    [
        (
            "win32",
            {"LOCALAPPDATA": r"C:\Users\zk\AppData\Local"},
            Path(r"C:\Users\zk\AppData\Local") / "SpotM3U",
        ),
        ("darwin", {}, Path.home() / "Library" / "Application Support" / "SpotM3U"),
        ("linux", {"XDG_DATA_HOME": "/data"}, Path("/data") / "spotm3u"),
        ("linux", {}, Path.home() / ".local" / "share" / "spotm3u"),
    ],
)
def test_user_data_dir_follows_the_platform_convention(
    monkeypatch, platform_name: str, environ: dict[str, str], expected: Path
) -> None:
    for name in ("LOCALAPPDATA", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(runtime.sys, "platform", platform_name)

    assert runtime.user_data_dir() == expected


def test_user_data_dir_is_only_named_never_created(monkeypatch, tmp_path) -> None:
    """Naming the directory must not write anything; each writer creates it."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(runtime.sys, "platform", "linux")

    directory = runtime.user_data_dir()

    assert directory == tmp_path / "data" / "spotm3u"
    assert not directory.exists()
