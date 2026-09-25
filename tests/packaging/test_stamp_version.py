"""Tests for the release-version stamping helper."""

import importlib.util

import pytest
from conftest import REPO_ROOT


def _load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stamp_version = _load("stamp_version")

_SOURCE = '"""Spotify to local M3U playlist tooling."""\n\n__version__ = "0.1.0"\n'


def test_stamp_replaces_the_version_in_place() -> None:
    stamped = stamp_version.stamp(_SOURCE, "2.1.0")

    assert '__version__ = "2.1.0"' in stamped
    assert "0.1.0" not in stamped
    # The rest of the module is untouched.
    assert stamped.startswith('"""Spotify to local M3U playlist tooling."""')


def test_stamp_accepts_a_prerelease_suffix() -> None:
    assert '__version__ = "1.2.3-rc.1"' in stamp_version.stamp(_SOURCE, "1.2.3-rc.1")


@pytest.mark.parametrize("version", ["1.2", "2.0-rc", "v1.2.3", "latest", "1.2.3-beta!"])
def test_stamp_rejects_versions_the_update_check_cannot_parse(version: str) -> None:
    # update.parse_version only understands plain semantic versions; stamping
    # anything else would leave the running app comparing nothing at all. The
    # release workflow's tag guard (check_release_tag) is stricter still, but
    # the two must never disagree about a shape like ``2.0-rc``.
    with pytest.raises(SystemExit, match="not a semantic version"):
        stamp_version.stamp(_SOURCE, version)


def test_stamp_fails_when_the_constant_is_missing() -> None:
    with pytest.raises(SystemExit, match="exactly one __version__"):
        stamp_version.stamp('"""no version here"""\n', "1.0.0")


def test_stamp_fails_when_the_constant_appears_twice() -> None:
    with pytest.raises(SystemExit, match="found 2"):
        stamp_version.stamp(_SOURCE + '__version__ = "9.9.9"\n', "1.0.0")


def test_package_version_line_is_stampable() -> None:
    # Guards the release workflow: if the real constant is ever renamed or
    # reformatted, stamping must fail in CI rather than silently not apply.
    text = (REPO_ROOT / "src" / "spotm3u" / "__init__.py").read_text(encoding="utf-8")

    assert stamp_version.stamp(text, "1.2.3") != text


def test_main_writes_the_default_target(monkeypatch, tmp_path) -> None:
    target = tmp_path / "__init__.py"
    target.write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(stamp_version, "DEFAULT_TARGET", target)

    stamp_version.main(["3.0.0"])

    assert target.read_text(encoding="utf-8") == stamp_version.stamp(_SOURCE, "3.0.0")


def test_main_requires_exactly_one_argument() -> None:
    with pytest.raises(SystemExit, match="usage"):
        stamp_version.main([])
    with pytest.raises(SystemExit, match="usage"):
        stamp_version.main(["1.0.0", "2.0.0"])
