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


# --- target platform ------------------------------------------------------------

_TARGET_SOURCE = '/** Doc comment. */\n\nexport const BUILD_TARGET_PLATFORM = "";\n'


def test_stamp_platform_sets_the_frontend_build_target() -> None:
    stamped = stamp_version.stamp_platform(_TARGET_SOURCE, "mac")

    assert 'export const BUILD_TARGET_PLATFORM = "mac";' in stamped
    # The rest of the module is untouched.
    assert stamped.startswith("/** Doc comment. */")


def test_stamp_platform_is_idempotent() -> None:
    once = stamp_version.stamp_platform(_TARGET_SOURCE, "windows")

    assert stamp_version.stamp_platform(once, "windows") == once


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("mac", "mac"),
        ("linux", "linux"),
        ("windows", "windows"),
        # The release matrix says ``macos`` and Python says ``darwin``; both
        # name macOS, and neither spelling may reach the frontend.
        ("macos", "mac"),
        ("darwin", "mac"),
        ("win32", "windows"),
        ("MAC", "mac"),
        ("  linux  ", "linux"),
    ],
)
def test_stamp_platform_accepts_the_names_the_build_uses(value: str, expected: str) -> None:
    stamped = stamp_version.stamp_platform(_TARGET_SOURCE, value)

    assert f'export const BUILD_TARGET_PLATFORM = "{expected}";' in stamped


@pytest.mark.parametrize("platform", ["", "freebsd14", "android", "unknown", "macosx"])
def test_stamp_platform_rejects_an_unsupported_platform(platform: str) -> None:
    # A bundle must never be produced with a frame platform nobody chose: on
    # macOS a value this rejects would otherwise surface as Windows/Linux window
    # controls drawn over the native traffic lights.
    with pytest.raises(SystemExit, match="unsupported target platform"):
        stamp_version.stamp_platform(_TARGET_SOURCE, platform)


def test_stamp_platform_fails_when_the_constant_is_missing() -> None:
    with pytest.raises(SystemExit, match="exactly one BUILD_TARGET_PLATFORM"):
        stamp_version.stamp_platform("/** no platform here */\n", "mac")


def test_stamp_platform_fails_when_the_constant_appears_twice() -> None:
    doubled = _TARGET_SOURCE + 'export const BUILD_TARGET_PLATFORM = "linux";\n'

    with pytest.raises(SystemExit, match="found 2"):
        stamp_version.stamp_platform(doubled, "mac")


def test_frontend_target_file_is_stampable() -> None:
    # Guards the release workflow: if the constant is ever renamed, reformatted
    # or split, stamping must fail in CI rather than silently not apply, which
    # would ship a bundle that guesses its frame.
    text = (REPO_ROOT / "frontend" / "src" / "lib" / "target.ts").read_text(encoding="utf-8")

    assert stamp_version.stamp_platform(text, "mac") != text


def test_committed_frontend_target_is_unstamped() -> None:
    # The committed value is not a platform: an unstamped bundle must draw no
    # window controls rather than one platform's. Every real build stamps it.
    text = (REPO_ROOT / "frontend" / "src" / "lib" / "target.ts").read_text(encoding="utf-8")

    assert 'export const BUILD_TARGET_PLATFORM = "";' in text


def test_main_stamps_the_platform_alone(tmp_path, monkeypatch) -> None:
    target = tmp_path / "target.ts"
    target.write_text(_TARGET_SOURCE, encoding="utf-8")
    monkeypatch.setattr(stamp_version, "DEFAULT_PLATFORM_TARGET", target)
    version_target = tmp_path / "__init__.py"
    version_target.write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(stamp_version, "DEFAULT_TARGET", version_target)

    stamp_version.main(["--platform", "linux"])

    assert target.read_text(encoding="utf-8") == stamp_version.stamp_platform(
        _TARGET_SOURCE, "linux"
    )
    # The version is left alone when only a platform was asked for.
    assert version_target.read_text(encoding="utf-8") == _SOURCE


def test_main_stamps_the_version_and_the_platform(tmp_path, monkeypatch) -> None:
    version_target = tmp_path / "__init__.py"
    version_target.write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(stamp_version, "DEFAULT_TARGET", version_target)
    platform_target = tmp_path / "target.ts"
    platform_target.write_text(_TARGET_SOURCE, encoding="utf-8")
    monkeypatch.setattr(stamp_version, "DEFAULT_PLATFORM_TARGET", platform_target)

    stamp_version.main(["3.0.0", "--platform", "mac"])

    assert '__version__ = "3.0.0"' in version_target.read_text(encoding="utf-8")
    assert 'BUILD_TARGET_PLATFORM = "mac";' in platform_target.read_text(encoding="utf-8")


def test_main_fails_the_build_for_an_unsupported_platform(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(stamp_version, "DEFAULT_PLATFORM_TARGET", tmp_path / "target.ts")

    with pytest.raises(SystemExit, match="unsupported target platform"):
        stamp_version.main(["--platform", "freebsd14"])


@pytest.mark.parametrize("argv", [[], ["1.0.0", "2.0.0"], ["--platform"], ["--nope"]])
def test_main_rejects_unusable_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit, match="usage"):
        stamp_version.main(argv)
