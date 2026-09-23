"""Tests for the packaged-artifact icon checks.

`packaging/verify_packaged_icons.py` inspects what the packaged application
actually carries: the icon resources embedded in the Windows executable, and the
canonical PNG the frozen Linux build reads for its GTK window icon. These tests
generate real icon assets, model the two containers the checker parses, and pin
the failure modes that would otherwise ship an application with the wrong icon.

Generating an icon set costs a couple of seconds, so the checker tests share one
``.ico`` built from the smallest accepted master; nothing they assert depends on
the size of the master artwork.
"""

import importlib.util
import struct
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_ICON_PNG = _REPO / "assets" / "icon.png"
_PACKAGING = _REPO / "packaging"


def _load(name: str) -> object:
    path = _PACKAGING / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load("verify_packaged_icons")
icons = _load("generate_icons")


@pytest.fixture(scope="module")
def generated_ico(tmp_path_factory) -> Path:
    """One generated .ico shared by the icon-resource tests."""
    directory = tmp_path_factory.mktemp("icons")
    master = directory / "icon.png"
    size = icons.MIN_SOURCE_SIZE
    master.write_bytes(
        icons._encode_png(bytes([0x40, 0x80, 0xC0, 0xFF]) * (size * size), size, size)
    )
    return icons.generate(master, directory / "generated")["ico"]


def _group_icon(members: list[tuple[int, bytes]]) -> bytes:
    """A GRPICONDIR describing ``members`` as Windows resource ids 1..n.

    A GRPICONDIR stores each dimension in a single byte, where 0 stands for 256.
    """
    payload = struct.pack("<HHH", 0, 1, len(members))
    for index, (size, data) in enumerate(members, start=1):
        assert 1 <= size <= 256
        dimension = 0 if size == 256 else size
        payload += struct.pack("<BBBBHHIH", dimension, dimension, 0, 0, 1, 32, len(data), index)
    return payload


def _windows_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "SpotM3U"
    bundle.mkdir()
    (bundle / "SpotM3U.exe").write_bytes(b"MZ")
    return bundle


def _patch_resources(monkeypatch, group: bytes, images: dict[int, bytes]) -> None:
    monkeypatch.setattr(checker, "pe_icon_resources", lambda _path: (group, images))


def _image_map(members: list[tuple[int, bytes]]) -> dict[int, bytes]:
    return {index: data for index, (_, data) in enumerate(members, start=1)}


def test_read_ico_lists_every_generated_member(generated_ico) -> None:
    members = checker.read_ico(generated_ico.read_bytes())

    assert [size for size, _ in members] == [16, 24, 32, 48, 64, 128, 256]
    assert all(data.startswith(b"\x89PNG") for _, data in members)


def test_windows_bundle_accepts_the_generated_icon(tmp_path, monkeypatch, generated_ico) -> None:
    ico = generated_ico
    members = checker.read_ico(ico.read_bytes())
    _patch_resources(monkeypatch, _group_icon(members), _image_map(members))

    summary = checker.verify_windows_bundle(_windows_bundle(tmp_path), ico)

    assert summary == f"SpotM3U.exe embeds all {len(members)} icon sizes from {ico.name}"


@pytest.mark.parametrize(
    ("resize_members", "message"),
    [
        pytest.param(
            lambda members: members[:-1],  # the 256x256 member is gone
            r"does not embed the icon sizes \[256\]",
            id="missing-size",
        ),
        pytest.param(
            lambda members: [*members, (20, b"\x89PNG stale artwork")],
            r"embeds icon sizes \[20\]",
            id="stale-extra-size",
        ),
    ],
)
def test_windows_bundle_rejects_a_size_set_that_differs(
    tmp_path, monkeypatch, generated_ico, resize_members, message
) -> None:
    ico = generated_ico
    members = resize_members(checker.read_ico(ico.read_bytes()))
    _patch_resources(monkeypatch, _group_icon(members), _image_map(members))

    with pytest.raises(SystemExit, match=message):
        checker.verify_windows_bundle(_windows_bundle(tmp_path), ico)


@pytest.mark.parametrize(
    ("alter_payloads", "message"),
    [
        pytest.param(
            lambda _group, _members, images: (
                _group,
                {**images, 1: b"\x89PNG different artwork"},
            ),
            "differs from",
            id="different-image-payload",
        ),
        pytest.param(
            lambda _group, members, images: (
                _group_icon([(members[0][0], members[0][1][:-1]), *members[1:]]),
                images,
            ),
            "differs from",
            id="declared-size-differs",
        ),
    ],
)
def test_windows_bundle_rejects_payloads_that_do_not_match(
    tmp_path, monkeypatch, generated_ico, alter_payloads, message
) -> None:
    ico = generated_ico
    members = checker.read_ico(ico.read_bytes())
    group, images = alter_payloads(_group_icon(members), members, _image_map(members))
    _patch_resources(monkeypatch, group, images)

    with pytest.raises(SystemExit, match=message):
        checker.verify_windows_bundle(_windows_bundle(tmp_path), ico)


def test_windows_bundle_rejects_a_missing_executable(tmp_path, generated_ico) -> None:
    bundle = tmp_path / "SpotM3U"
    bundle.mkdir()

    with pytest.raises(SystemExit, match="missing"):
        checker.verify_windows_bundle(bundle, generated_ico)


def test_windows_bundle_without_embedded_icons_fails(tmp_path, monkeypatch, generated_ico) -> None:
    def no_icon(_path):
        raise SystemExit("invalid packaged icon: SpotM3U.exe embeds no application icon")

    monkeypatch.setattr(checker, "pe_icon_resources", no_icon)

    with pytest.raises(SystemExit, match="embeds no application icon"):
        checker.verify_windows_bundle(_windows_bundle(tmp_path), generated_ico)


def test_pe_resource_walk_reads_a_real_windows_executable() -> None:
    # distlib ships real Windows launchers with icon resources; they are the only
    # Windows binaries available off Windows, and running the resource walk
    # against them is what keeps the parser honest outside the release matrix.
    executables = sorted(Path(sys.prefix).rglob("*.exe"))
    if not executables:
        pytest.skip("no Windows executable available under the interpreter prefix")

    group, images = checker.pe_icon_resources(executables[0])

    members = checker.group_icon_members(group)
    assert members
    assert images
    for size, identifier, declared in members:
        assert size in {16, 24, 32, 48, 64, 128, 256}
        assert identifier in images
        assert declared == len(images[identifier])


def test_pe_resource_walk_rejects_a_non_pe_file(tmp_path) -> None:
    not_an_exe = tmp_path / "SpotM3U.exe"
    not_an_exe.write_bytes(b"#!/bin/sh\n")

    with pytest.raises(SystemExit, match="not a Windows executable"):
        checker.pe_icon_resources(not_an_exe)


def _linux_bundle(tmp_path: Path, *, internal: bool = True) -> Path:
    bundle = tmp_path / "SpotM3U"
    directory = bundle / "_internal" if internal else bundle
    directory.mkdir(parents=True)
    (directory / "icon.png").write_bytes(_ICON_PNG.read_bytes())
    return bundle


@pytest.mark.parametrize(
    ("internal", "summary"),
    [
        pytest.param(
            True,
            "_internal/icon.png is the 1024x1024 master artwork",
            id="inside-internal",
        ),
        pytest.param(False, "icon.png is the 1024x1024 master artwork", id="beside-executable"),
    ],
)
def test_linux_bundle_accepts_the_master_artwork(tmp_path, internal, summary) -> None:
    assert checker.verify_linux_bundle(_linux_bundle(tmp_path, internal=internal)) == summary


def test_linux_bundle_rejects_a_missing_window_icon(tmp_path) -> None:
    bundle = tmp_path / "SpotM3U"
    bundle.mkdir()

    with pytest.raises(SystemExit, match="window icon icon.png is missing"):
        checker.verify_linux_bundle(bundle)


def test_linux_bundle_rejects_a_stale_window_icon(tmp_path) -> None:
    bundle = _linux_bundle(tmp_path)
    (bundle / "_internal" / "icon.png").write_bytes(icons._encode_png(bytes(16 * 16 * 4), 16, 16))

    with pytest.raises(SystemExit, match="does not match the master artwork"):
        checker.verify_linux_bundle(bundle)


def test_linux_bundle_rejects_a_missing_master_artwork(tmp_path) -> None:
    with pytest.raises(SystemExit, match="master artwork is missing"):
        checker.verify_linux_bundle(_linux_bundle(tmp_path), master_icon=tmp_path / "icon.png")


def test_verify_bundle_dispatches_per_platform(tmp_path) -> None:
    bundle = _linux_bundle(tmp_path)

    assert "master artwork" in checker.verify_bundle(bundle, platform="linux")
    with pytest.raises(SystemExit, match="verify_macos_bundle"):
        checker.verify_bundle(bundle, platform="darwin")


def test_verify_bundle_rejects_a_path_that_is_not_a_bundle(tmp_path) -> None:
    with pytest.raises(SystemExit, match="not a bundle directory"):
        checker.verify_bundle(tmp_path / "nope", platform="linux")
