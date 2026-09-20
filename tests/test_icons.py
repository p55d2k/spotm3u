"""Tests for deriving Windows/macOS icons from the canonical PNG.

``assets/icon.png`` is the single source of truth; ``packaging/generate_icons.py``
turns it into the ``.ico`` and ``.icns`` files the packaged builds embed. These
tests parse the generated containers structurally and pin determinism.
"""

import importlib.util
import struct
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_ICON_PNG = _REPO / "assets" / "icon.png"
_PACKAGING = _REPO / "packaging"

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _load() -> object:
    path = _PACKAGING / "generate_icons.py"
    spec = importlib.util.spec_from_file_location("generate_icons", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


icons = _load()


def test_source_icon_exists_with_sufficient_resolution() -> None:
    assert _ICON_PNG.is_file()
    head = _ICON_PNG.read_bytes()[:24]
    width, height = struct.unpack(">II", head[16:24])
    assert width >= 256 and height >= 256
    assert _ICON_PNG.read_bytes()[25] in (2, 6)  # RGB or RGBA


def test_generate_produces_ico_and_icns(tmp_path) -> None:
    generated = icons.generate(_ICON_PNG, tmp_path / "generated")

    assert generated["ico"].is_file()
    assert generated["icns"].is_file()
    assert generated["ico"].read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert generated["icns"].read_bytes()[:4] == b"icns"


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
    assert seen == [16, 32, 48, 64, 128, 256]


def test_icns_contains_png_elements_for_retina_sizes(tmp_path) -> None:
    icns = tmp_path / "generated" / "icon.icns"
    icons.generate(_ICON_PNG, icns.parent)
    data = icns.read_bytes()

    assert data[:4] == b"icns"
    total = struct.unpack_from(">I", data, 4)[0]
    assert total == len(data)
    elements: dict[str, int] = {}
    pos = 8
    while pos < len(data):
        element = data[pos : pos + 4].decode("ascii")
        length = struct.unpack_from(">I", data, pos + 4)[0]
        payload = data[pos + 8 : pos + length]
        assert payload.startswith(_PNG_MAGIC)
        width = struct.unpack_from(">I", payload, 16)[0]
        elements[element] = width
        pos += length
    assert set(elements) == {"ic10", "ic07", "ic13", "ic11", "ic14"}
    assert elements == {
        "ic10": 128,
        "ic07": 256,
        "ic13": 256,
        "ic11": 512,
        "ic14": 512,
    }


def test_generation_is_deterministic(tmp_path) -> None:
    first = icons.generate(_ICON_PNG, tmp_path / "a")
    second = icons.generate(_ICON_PNG, tmp_path / "b")

    assert first["ico"].read_bytes() == second["ico"].read_bytes()
    assert first["icns"].read_bytes() == second["icns"].read_bytes()


def test_generate_rejects_non_png_source(tmp_path) -> None:
    bad = tmp_path / "icon.png"
    bad.write_bytes(b"not a png")

    with pytest.raises(icons.IconError, match="PNG"):
        icons.generate(bad, tmp_path / "generated")
