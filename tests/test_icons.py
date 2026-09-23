"""Tests for deriving Windows/macOS icons from the canonical PNG.

``assets/icon.png`` is the single source of truth; ``packaging/generate_icons.py``
turns it into the ``.ico`` and ``.icns`` files the packaged builds embed. These
tests parse the generated containers structurally, check that every ICNS element
holds the pixel size macOS expects for its type, and pin the source validation.

Generating a full icon set from the 1024x1024 master costs a couple of seconds
(mostly re-encoding the largest members), so the container tests share one
generated set built from the smallest accepted master, and only the tests about
the shipped artwork itself pay for the real asset.
"""

import importlib.util
import struct
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_ICON_PNG = _REPO / "assets" / "icon.png"
_PACKAGING = _REPO / "packaging"

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_FLAT_PIXEL = bytes([0x40, 0x80, 0xC0, 0xFF])
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


def _flat_png(size: int) -> bytes:
    """A valid opaque square PNG of the requested size."""
    return icons._encode_png(_FLAT_PIXEL * (size * size), size, size)


def _ico_members(ico: Path) -> list[tuple[int, bytes]]:
    """``(pixel size, payload)`` for every member of a generated .ico."""
    data = ico.read_bytes()
    reserved, icon_type, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, icon_type) == (0, 1)
    members: list[tuple[int, bytes]] = []
    for index in range(count):
        width, height, _colors, _reserved, planes, bpp, size, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * index
        )
        width = 256 if width == 0 else width
        height = 256 if height == 0 else height
        assert width == height
        assert planes == 1 and bpp == 32
        members.append((width, data[offset : offset + size]))
    return members


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


@pytest.fixture(scope="module")
def master(tmp_path_factory) -> Path:
    """A valid master at the smallest accepted size.

    Icon generation is dominated by re-encoding the largest members, and what
    these tests assert about the generated containers does not depend on the
    master's size. Only the tests about the shipped artwork itself use
    ``assets/icon.png``.
    """
    path = tmp_path_factory.mktemp("master") / "icon.png"
    path.write_bytes(_flat_png(icons.MIN_SOURCE_SIZE))
    return path


@pytest.fixture(scope="module")
def generated(master, tmp_path_factory) -> dict[str, Path]:
    """One generated icon set, shared by the container tests."""
    return icons.generate(master, tmp_path_factory.mktemp("generated"))


def test_source_icon_exists_as_a_square_high_resolution_png() -> None:
    rgba, width, height = icons.validate_source(_ICON_PNG)

    assert width == height
    assert width >= icons.MIN_SOURCE_SIZE
    assert len(rgba) == width * height * 4


def test_generate_uses_the_shipped_master_artwork(tmp_path) -> None:
    """The real master must reach both containers end to end."""
    shipped = icons.generate(_ICON_PNG, tmp_path / "generated")

    assert shipped["ico"].read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert shipped["icns"].read_bytes()[:4] == b"icns"
    assert [size for size, _ in _ico_members(shipped["ico"])] == [16, 24, 32, 48, 64, 128, 256]
    # The 512pt slot is a 1024px image, so the master's full resolution is used.
    assert _icns_elements(shipped["icns"])["ic10"] == 1024


def test_generate_places_artifacts_under_the_given_directory(generated) -> None:
    output = generated["ico"].parent

    assert generated["icns"].parent == output
    assert sorted(path.name for path in output.iterdir()) == ["icon.icns", "icon.ico"]


def test_ico_contains_png_compressed_size_entries(generated) -> None:
    members = _ico_members(generated["ico"])

    assert [size for size, _ in members] == [16, 24, 32, 48, 64, 128, 256]
    assert all(payload.startswith(_PNG_MAGIC) for _, payload in members)


def test_icns_entries_hold_the_size_their_type_declares(generated) -> None:
    elements = _icns_elements(generated["icns"])

    assert _REQUIRED_ELEMENTS <= set(elements)
    for element, width in elements.items():
        assert element in _ICNS_ELEMENT_SIZES, f"unexpected ICNS element {element}"
        assert width == _ICNS_ELEMENT_SIZES[element], f"{element} holds {width}px"


def test_icns_includes_retina_members(generated) -> None:
    elements = _icns_elements(generated["icns"])

    # 128pt, 256pt and 512pt each need a 1x and a @2x representation; without
    # the @2x members the Dock renders the icon from an upscaled small image.
    assert {"ic07", "ic08", "ic09", "ic13", "ic14"} <= set(elements)


def test_generation_is_deterministic(generated, master, tmp_path) -> None:
    again = icons.generate(master, tmp_path / "again")

    assert generated["ico"].read_bytes() == again["ico"].read_bytes()
    assert generated["icns"].read_bytes() == again["icns"].read_bytes()


def test_generate_replaces_stale_artifacts(master, tmp_path) -> None:
    output = tmp_path / "generated"
    output.mkdir()
    (output / "icon.ico").write_bytes(b"stale")
    (output / "icon.icns").write_bytes(b"stale")
    stale_iconset = output / icons.ICONSET_DIRNAME
    stale_iconset.mkdir()
    (stale_iconset / "icon_16x16.png").write_bytes(b"stale")

    icons.generate(master, output)

    assert (output / "icon.ico").read_bytes().startswith(b"\x00\x00\x01\x00")
    assert (output / "icon.icns").read_bytes().startswith(b"icns")
    assert not stale_iconset.exists()


def test_iconutil_builds_a_standard_iconset_when_available(tmp_path, monkeypatch) -> None:
    # Uses the shipped artwork: the 1024px @2x member only exists for it.
    output = tmp_path / "generated"
    iconset = output / icons.ICONSET_DIRNAME
    calls: list[tuple[list[str], list[str]]] = []

    def fake_run(command, **_kwargs):
        calls.append((command, sorted(path.name for path in iconset.iterdir())))
        Path(command[command.index("-o") + 1]).write_bytes(b"icns" + b"\x00" * 12)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(icons.shutil, "which", lambda name: "/usr/bin/iconutil")
    monkeypatch.setattr(icons.subprocess, "run", fake_run)

    generated = icons.generate(_ICON_PNG, output)

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


def test_iconutil_failure_fails_icon_generation(master, tmp_path, monkeypatch) -> None:
    def fake_run(_command, **_kwargs):
        return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "bad iconset"})()

    monkeypatch.setattr(icons.shutil, "which", lambda name: "/usr/bin/iconutil")
    monkeypatch.setattr(icons.subprocess, "run", fake_run)

    with pytest.raises(icons.IconError, match="iconutil failed"):
        icons.generate(master, tmp_path / "generated")


def test_generate_rejects_non_png_source(tmp_path) -> None:
    bad = tmp_path / "icon.png"
    bad.write_bytes(b"not a png")

    with pytest.raises(icons.IconError, match="PNG"):
        icons.generate(bad, tmp_path / "generated")


def test_generate_rejects_a_non_square_source(tmp_path) -> None:
    bad = tmp_path / "icon.png"
    bad.write_bytes(icons._encode_png(_FLAT_PIXEL * (600 * 512), 600, 512))

    with pytest.raises(icons.IconError, match="square"):
        icons.generate(bad, tmp_path / "generated")


def test_generate_rejects_an_undersized_source(tmp_path) -> None:
    bad = tmp_path / "icon.png"
    bad.write_bytes(_flat_png(256))

    with pytest.raises(icons.IconError, match="at least"):
        icons.generate(bad, tmp_path / "generated")


def test_generate_rejects_a_missing_source(tmp_path) -> None:
    with pytest.raises(icons.IconError, match="missing"):
        icons.generate(tmp_path / "icon.png", tmp_path / "generated")
