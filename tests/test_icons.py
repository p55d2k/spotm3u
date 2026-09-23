"""Tests for deriving Windows/macOS icons from the canonical PNG.

``assets/icon.png`` is the single source of truth; ``packaging/generate_icons.py``
turns it into the ``.ico`` and ``.icns`` files the packaged builds embed. These
tests parse the generated containers structurally, check that every ICNS element
holds the pixel size macOS expects for its type, and pin the source validation.
"""

import importlib.util
import struct
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_ICON_PNG = _REPO / "assets" / "icon.png"
_PACKAGING = _REPO / "packaging"

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# The pixel size macOS expects each ICNS element type to hold. A mismatch (for
# example 128px data in the 512pt slot) makes macOS scale from the wrong member.
_ICNS_ELEMENT_SIZES = {
    "icp4": 16,
    "icp5": 32,
    "icp6": 64,
    "ic07": 128,
    "ic08": 256,
    "ic09": 512,
    "ic10": 1024,
    "ic11": 32,
    "ic12": 64,
    "ic13": 256,
    "ic14": 512,
}
# Elements a usable icon must carry regardless of which writer produced it.
_REQUIRED_ELEMENTS = {"ic07", "ic08", "ic09", "ic13", "ic14"}


def _load() -> object:
    path = _PACKAGING / "generate_icons.py"
    spec = importlib.util.spec_from_file_location("generate_icons", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


icons = _load()


def _png(width: int, height: int) -> bytes:
    """A valid opaque RGBA PNG of the requested size."""
    return icons._encode_png(bytes([0x40, 0x80, 0xC0, 0xFF]) * (width * height), width, height)


def _icns_elements(icns: Path) -> dict[str, int]:
    """Map every PNG image element type in ``icns`` to its pixel width.

    Non-PNG members are ignored: ``iconutil`` also emits ARGB members such as
    ``ic04``/``ic05`` and a ``info`` metadata block.
    """
    data = icns.read_bytes()
    assert data[:4] == b"icns"
    assert struct.unpack_from(">I", data, 4)[0] == len(data)
    elements: dict[str, int] = {}
    offset = 8
    while offset < len(data):
        element = data[offset : offset + 4].decode("ascii")
        length = struct.unpack_from(">I", data, offset + 4)[0]
        payload = data[offset + 8 : offset + length]
        if element.startswith("ic") and payload.startswith(_PNG_MAGIC):
            width = struct.unpack_from(">I", payload, 16)[0]
            assert width == struct.unpack_from(">I", payload, 20)[0]
            elements[element] = width
        offset += length
    assert offset == len(data)
    return elements


def test_source_icon_exists_as_a_square_high_resolution_png() -> None:
    rgba, width, height = icons.validate_source(_ICON_PNG)

    assert width == height
    assert width >= icons.MIN_SOURCE_SIZE
    assert len(rgba) == width * height * 4


def test_generate_produces_ico_and_icns(tmp_path) -> None:
    generated = icons.generate(_ICON_PNG, tmp_path / "generated")

    assert generated["ico"].is_file()
    assert generated["icns"].is_file()
    assert generated["ico"].read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert generated["icns"].read_bytes()[:4] == b"icns"


def test_generate_places_artifacts_under_the_given_directory(tmp_path) -> None:
    output = tmp_path / "assets" / "generated"

    generated = icons.generate(_ICON_PNG, output)

    assert generated["ico"].parent == output
    assert generated["icns"].parent == output
    assert sorted(path.name for path in output.iterdir()) == ["icon.icns", "icon.ico"]


def test_ico_contains_png_compressed_size_entries(tmp_path) -> None:
    ico = tmp_path / "generated" / "icon.ico"
    icons.generate(_ICON_PNG, ico.parent)
    data = ico.read_bytes()

    reserved, icon_type, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, icon_type) == (0, 1)
    offset = 6
    seen = []
    for _ in range(count):
        width, height, colors, reserved, planes, bpp, size, image_offset = struct.unpack_from(
            "<BBBBHHII", data, offset
        )
        width = 256 if width == 0 else width
        height = 256 if height == 0 else height
        assert width == height
        assert planes == 1 and bpp == 32
        payload = data[image_offset : image_offset + size]
        assert payload.startswith(_PNG_MAGIC)
        seen.append(width)
        offset += 16
    assert seen == [16, 24, 32, 48, 64, 128, 256]


def test_icns_entries_hold_the_size_their_type_declares(tmp_path) -> None:
    icns = tmp_path / "generated" / "icon.icns"
    icons.generate(_ICON_PNG, icns.parent)

    elements = _icns_elements(icns)

    assert _REQUIRED_ELEMENTS <= set(elements)
    for element, width in elements.items():
        assert element in _ICNS_ELEMENT_SIZES, f"unexpected ICNS element {element}"
        assert width == _ICNS_ELEMENT_SIZES[element], f"{element} holds {width}px"


def test_icns_includes_retina_members(tmp_path) -> None:
    icns = tmp_path / "generated" / "icon.icns"
    icons.generate(_ICON_PNG, icns.parent)

    elements = _icns_elements(icns)

    # 128pt, 256pt and 512pt each need a 1x and a @2x representation; without
    # the @2x members the Dock renders the icon from an upscaled small image.
    assert {"ic07", "ic08", "ic09", "ic13", "ic14"} <= set(elements)


def test_generation_is_deterministic(tmp_path) -> None:
    first = icons.generate(_ICON_PNG, tmp_path / "a")
    second = icons.generate(_ICON_PNG, tmp_path / "b")

    assert first["ico"].read_bytes() == second["ico"].read_bytes()
    assert first["icns"].read_bytes() == second["icns"].read_bytes()


def test_generate_replaces_stale_artifacts(tmp_path) -> None:
    output = tmp_path / "generated"
    output.mkdir()
    (output / "icon.ico").write_bytes(b"stale")
    (output / "icon.icns").write_bytes(b"stale")
    stale_iconset = output / icons.ICONSET_DIRNAME
    stale_iconset.mkdir()
    (stale_iconset / "icon_16x16.png").write_bytes(b"stale")

    icons.generate(_ICON_PNG, output)

    assert (output / "icon.ico").read_bytes().startswith(b"\x00\x00\x01\x00")
    assert (output / "icon.icns").read_bytes().startswith(b"icns")
    assert not stale_iconset.exists()


def test_iconutil_builds_a_standard_iconset_when_available(tmp_path, monkeypatch) -> None:
    iconset = tmp_path / "generated" / icons.ICONSET_DIRNAME
    calls: list[tuple[list[str], list[str]]] = []

    def fake_run(command, **_kwargs):
        calls.append((command, sorted(path.name for path in iconset.iterdir())))
        Path(command[command.index("-o") + 1]).write_bytes(b"icns" + b"\x00" * 12)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(icons.shutil, "which", lambda name: "/usr/bin/iconutil")
    monkeypatch.setattr(icons.subprocess, "run", fake_run)

    generated = icons.generate(_ICON_PNG, tmp_path / "generated")

    assert len(calls) == 1
    command, entries = calls[0]
    assert command == [
        "/usr/bin/iconutil",
        "-c",
        "icns",
        "-o",
        str(generated["icns"]),
        str(iconset),
    ]
    assert entries == sorted(name for name, _ in icons.ICONSET_ENTRIES)
    # The temporary .iconset directory must not survive into the packaged build.
    assert not iconset.exists()


def test_iconutil_failure_fails_icon_generation(tmp_path, monkeypatch) -> None:
    def fake_run(_command, **_kwargs):
        return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "bad iconset"})()

    monkeypatch.setattr(icons.shutil, "which", lambda name: "/usr/bin/iconutil")
    monkeypatch.setattr(icons.subprocess, "run", fake_run)

    with pytest.raises(icons.IconError, match="iconutil failed"):
        icons.generate(_ICON_PNG, tmp_path / "generated")


def test_generate_rejects_non_png_source(tmp_path) -> None:
    bad = tmp_path / "icon.png"
    bad.write_bytes(b"not a png")

    with pytest.raises(icons.IconError, match="PNG"):
        icons.generate(bad, tmp_path / "generated")


def test_generate_rejects_a_non_square_source(tmp_path) -> None:
    bad = tmp_path / "icon.png"
    bad.write_bytes(_png(600, 512))

    with pytest.raises(icons.IconError, match="square"):
        icons.generate(bad, tmp_path / "generated")


def test_generate_rejects_an_undersized_source(tmp_path) -> None:
    bad = tmp_path / "icon.png"
    bad.write_bytes(_png(256, 256))

    with pytest.raises(icons.IconError, match="at least"):
        icons.generate(bad, tmp_path / "generated")


def test_generate_rejects_a_missing_source(tmp_path) -> None:
    with pytest.raises(icons.IconError, match="missing"):
        icons.generate(tmp_path / "icon.png", tmp_path / "generated")
