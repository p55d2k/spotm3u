"""Verify that a packaged SpotM3U bundle carries the generated application icon.

`assets/icon.png` is the master artwork and ``assets/generated/`` holds the
platform formats the build derives from it (see ``packaging/generate_icons.py``).
The macOS ``.app`` is covered by ``packaging/verify_macos_bundle.py``; this script
covers the other two platforms, where the icon is consumed in different ways:

* **Windows** -- PyInstaller embeds ``icon.ico`` into ``SpotM3U.exe`` as PE
  resources, which is what Explorer, the taskbar, and shortcuts draw. The
  executable's resource directory is parsed and compared member for member
  against the generated ``.ico``, so an executable built with a stale or missing
  icon fails the build instead of shipping.
* **Linux** -- the GTK/WebKit window icon is the canonical ``icon.png`` collected
  into the bundle and read at runtime by ``spotm3u.desktop.webview_icon_path``.
  It must sit where the frozen app looks for it (the same candidates
  ``spotm3u.runtime.bundle_roots`` reports: ``_internal/`` then the bundle root)
  and be byte-identical to the tracked master artwork.

Both checks run against the built bundle before it is archived, so a release
never ships an application whose icon is missing or wrong. Accepts a one-folder
bundle (``dist/SpotM3U``), which is the layout on both platforms.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path
from typing import NoReturn

_ROOT = Path(__file__).resolve().parents[1]
MASTER_ICON = _ROOT / "assets" / "icon.png"
GENERATED_ICO = _ROOT / "assets" / "generated" / "icon.ico"
ICON_RELATIVE = "icon.png"
# PyInstaller collects data files into ``_internal`` in a one-folder bundle and
# the frozen app also accepts files sitting next to the executable.
_ICON_CANDIDATES = (Path("_internal") / ICON_RELATIVE, Path(ICON_RELATIVE))
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# PE resource type ids for the icon image and the group that describes it.
RT_ICON = 3
RT_GROUP_ICON = 14
_SUBDIRECTORY = 0x80000000
_NAMED_ENTRY = 0x80000000
_OFFSET_MASK = 0x7FFFFFFF
_PE32_PLUS_MAGIC = 0x20B
# The resource data directory is the third entry of the optional header's data
# directory array, which starts at these offsets in each optional header flavor.
_DATA_DIRECTORY_OFFSET = {0x10B: 96, _PE32_PLUS_MAGIC: 112}
_RESOURCE_DIRECTORY_INDEX = 2


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"invalid packaged icon: {message}")


def _find_executable(bundle: Path, suffix: str = "") -> Path:
    executable = bundle / f"SpotM3U{suffix}"
    if not executable.is_file():
        _fail(f"packaged executable {executable.name} is missing from {bundle}")
    return executable


def read_ico(data: bytes) -> list[tuple[int, bytes]]:
    """Return ``(pixel size, payload)`` for every member of a Windows .ico."""
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if (reserved, kind) != (0, 1):
        _fail("generated icon.ico is not a Windows icon container")
    members: list[tuple[int, bytes]] = []
    for index in range(count):
        width, height, _colors, _reserved, _planes, _bpp, size, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * index
        )
        width = 256 if width == 0 else width
        height = 256 if height == 0 else height
        if width != height:
            _fail(f"generated icon.ico member {index} is not square: {width}x{height}")
        if offset + size > len(data):
            _fail(f"generated icon.ico member {index} runs past the end of the file")
        members.append((width, data[offset : offset + size]))
    if not members:
        _fail("generated icon.ico contains no image members")
    return members


def group_icon_members(payload: bytes) -> list[tuple[int, int, int]]:
    """Return ``(pixel size, resource id, declared byte size)`` for a GRPICONDIR."""
    if len(payload) < 6:
        _fail("embedded group icon resource is truncated")
    _reserved, kind, count = struct.unpack_from("<HHH", payload, 0)
    if kind != 1:
        _fail("embedded group icon resource is not an icon directory")
    members: list[tuple[int, int, int]] = []
    for index in range(count):
        width, height, _colors, _reserved, _planes, _bpp, size, identifier = struct.unpack_from(
            "<BBBBHHIH", payload, 6 + 14 * index
        )
        width = 256 if width == 0 else width
        height = 256 if height == 0 else height
        if width != height:
            _fail(f"embedded group icon member {index} is not square: {width}x{height}")
        members.append((width, identifier, size))
    return members


def _resource_entries(data: bytes, offset: int) -> list[tuple[int, int]]:
    """``(name or id, child offset)`` pairs of a PE resource directory."""
    named, identifiers = struct.unpack_from("<HH", data, offset + 12)
    return [
        struct.unpack_from("<II", data, offset + 16 + 8 * index)
        for index in range(named + identifiers)
    ]


def pe_icon_resources(path: Path) -> tuple[bytes, dict[int, bytes]]:
    """Return the group icon and the icon images embedded in a Windows executable.

    The first ``RT_GROUP_ICON`` resource is returned together with every
    ``RT_ICON`` image keyed by its resource id.
    """
    data = path.read_bytes()
    if data[:2] != b"MZ":
        _fail(f"{path.name} is not a Windows executable")
    if len(data) < 0x40:
        _fail(f"{path.name} is truncated")
    (pe_offset,) = struct.unpack_from("<I", data, 0x3C)
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        _fail(f"{path.name} has no PE header")
    coff = pe_offset + 4
    _machine, section_count, _stamp, _symbols, _symbol_count, optional_size, _flags = (
        struct.unpack_from("<HHIIIHH", data, coff)
    )
    optional = coff + 20
    (magic,) = struct.unpack_from("<H", data, optional)
    if magic not in _DATA_DIRECTORY_OFFSET:
        _fail(f"{path.name} uses an unsupported PE optional header (0x{magic:x})")
    directories = optional + _DATA_DIRECTORY_OFFSET[magic]
    resource_rva, resource_size = struct.unpack_from(
        "<II", data, directories + 8 * _RESOURCE_DIRECTORY_INDEX
    )
    if not resource_rva or not resource_size:
        _fail(f"{path.name} embeds no icon resources")

    sections = [
        struct.unpack_from("<8sIIII", data, optional + optional_size + 40 * index)
        for index in range(section_count)
    ]

    def to_offset(rva: int) -> int:
        for _name, virtual_size, virtual_address, raw_size, raw_offset in sections:
            if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
                return raw_offset + (rva - virtual_address)
        _fail(f"{path.name}: resource address 0x{rva:x} is outside every section")

    base = to_offset(resource_rva)
    images: dict[int, bytes] = {}
    groups: dict[int, bytes] = {}
    for type_id, type_child in _resource_entries(data, base):
        if type_id & _NAMED_ENTRY or not type_child & _SUBDIRECTORY:
            continue
        for name_id, child in _resource_entries(data, base + (type_child & _OFFSET_MASK)):
            if name_id & _NAMED_ENTRY or not child & _SUBDIRECTORY:
                continue
            for _language, leaf in _resource_entries(data, base + (child & _OFFSET_MASK)):
                rva, size = struct.unpack_from("<II", data, base + leaf)
                start = to_offset(rva)
                payload = data[start : start + size]
                if len(payload) != size:
                    _fail(f"{path.name}: icon resource {name_id} is truncated")
                if type_id == RT_ICON:
                    images.setdefault(name_id, payload)
                elif type_id == RT_GROUP_ICON:
                    groups.setdefault(name_id, payload)
    if not groups:
        _fail(f"{path.name} embeds no application icon")
    if not images:
        _fail(f"{path.name} embeds an icon directory with no images")
    return groups[sorted(groups)[0]], images


def verify_windows_bundle(bundle: Path, generated_ico: Path = GENERATED_ICO) -> str:
    """Check the packaged executable embeds exactly the generated .ico."""
    executable = _find_executable(bundle, ".exe")
    if not generated_ico.is_file():
        _fail(f"missing generated icon {generated_ico}; run `uv run build` first")
    exe, source = executable.name, generated_ico.name
    wanted = dict(read_ico(generated_ico.read_bytes()))
    group, images = pe_icon_resources(executable)
    embedded = {
        size: (identifier, size_in_bytes)
        for size, identifier, size_in_bytes in group_icon_members(group)
    }

    missing = sorted(set(wanted) - set(embedded))
    if missing:
        _fail(f"{exe} does not embed the icon sizes {missing} from {source}")
    stale = sorted(set(embedded) - set(wanted))
    if stale:
        _fail(f"{exe} embeds icon sizes {stale} that {source} does not contain")
    for size, payload in wanted.items():
        identifier, declared = embedded[size]
        image = images.get(identifier)
        if image is None:
            _fail(f"{exe} declares a {size}x{size} icon but embeds no image for it")
        if declared != len(payload) or image != payload:
            _fail(f"{exe} embeds a {size}x{size} icon that differs from {source}")
    return f"{exe} embeds all {len(wanted)} icon sizes from {source}"


def _png_size(data: bytes) -> tuple[int, int]:
    if not data.startswith(PNG_SIGNATURE) or len(data) < 24:
        _fail("packaged window icon is not a PNG image")
    width, height = struct.unpack_from(">II", data, 16)
    if width != height:
        _fail(f"packaged window icon is not square: {width}x{height}")
    return width, height


def verify_linux_bundle(bundle: Path, master_icon: Path = MASTER_ICON) -> str:
    """Check the packaged window icon is the canonical master artwork."""
    if not master_icon.is_file():
        _fail(f"master artwork is missing: {master_icon}")
    for relative in _ICON_CANDIDATES:
        candidate = bundle / relative
        if not candidate.is_file():
            continue
        packaged = candidate.read_bytes()
        if packaged != master_icon.read_bytes():
            _fail(f"{relative} does not match the master artwork {master_icon.name}")
        width, height = _png_size(packaged)
        return f"{relative} is the {width}x{height} master artwork"
    expected = ", ".join(str(relative) for relative in _ICON_CANDIDATES)
    _fail(f"packaged window icon {ICON_RELATIVE} is missing from {bundle} (looked in {expected})")


def verify_bundle(
    bundle: Path,
    *,
    platform: str = sys.platform,
    master_icon: Path = MASTER_ICON,
    generated_ico: Path = GENERATED_ICO,
) -> str:
    """Verify the packaged icon for ``platform`` and return a summary line."""
    bundle = bundle.expanduser().resolve()
    if not bundle.is_dir():
        _fail(f"not a bundle directory: {bundle}")
    if platform == "darwin":
        _fail("macOS bundles are validated by packaging/verify_macos_bundle.py")
    if platform == "win32":
        return verify_windows_bundle(bundle, generated_ico=generated_ico)
    return verify_linux_bundle(bundle, master_icon=master_icon)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <bundle-dir>")
    print(f"packaged icon validated: {verify_bundle(Path(sys.argv[1]))}")
