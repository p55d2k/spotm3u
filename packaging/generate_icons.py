"""Generate platform icon formats from the canonical ``assets/icon.png``.

`assets/icon.png` is the single source of truth for the SpotM3U icon. PyInstaller
needs ICO/ICNS rather than PNG, so the build (``uv run build``) regenerates:

    assets/generated/icon.ico    Windows executable icon
    assets/generated/icon.icns   macOS .app bundle icon

The source PNG is validated first (exists, valid 8-bit RGB/RGBA PNG, square, and
large enough for the required representations) so a bad master artwork fails the
build instead of silently shipping a broken application icon. Stale generated
artifacts are removed before the fresh ones are written, so a build can never
package an icon left over from an earlier run.

On macOS the ICNS is produced by the system's ``iconutil``, which is the only
tool that reliably writes every representation macOS expects (the icon must carry
both the 1x and @2x members for 16/32/128/256/512). Where ``iconutil`` is not
available - Linux and Windows builds never consume the ICNS - a pure-standard
library writer emits the same element set instead, so the file can still be
inspected and tested off-macOS. Run it as:

    python packaging/generate_icons.py [source.png] [output-dir]

or just invoke ``uv run build``, which generates the icons before packaging.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICNS_MAGIC = b"icns"
# Windows ICO members: the small sizes are used by Explorer's list views and
# shortcuts, 256x256 by the extra-large tile view.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# ``.iconset`` members handed to ``iconutil``, with the pixel size each one must
# hold. The @2x entries are what make the icon sharp on Retina displays.
ICONSET_ENTRIES = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)
# ICNS element types paired with the pixel size macOS expects each one to hold.
# Getting this pairing wrong yields an .icns whose representations are scaled
# from the wrong member (for example a 128px image in the 512pt slot), which is
# how the bundled icon ends up rendered as a smeared square.
ICNS_ELEMENTS = (
    ("icp4", 16),
    ("icp5", 32),
    ("icp6", 64),
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),
    ("ic11", 32),
    ("ic12", 64),
    ("ic13", 256),
    ("ic14", 512),
)
# macOS renders the 512pt slot from a 1024px master, so anything smaller would
# have to be upscaled. Never upscale the master artwork.
MIN_SOURCE_SIZE = 512
DEFAULT_SOURCE = Path("assets") / "icon.png"
DEFAULT_OUTPUT = Path("assets") / "generated"
ICONSET_DIRNAME = "icon.iconset"


class IconError(RuntimeError):
    """Raised when the source image cannot be turned into build icons."""


def read_png(path: Path) -> tuple[bytes, int, int]:
    """Decode an 8-bit RGB/RGBA, non-interlaced PNG into RGBA pixels."""
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise IconError(f"not a PNG image: {path}")

    width = height = color_type = None
    idat = bytearray()
    pos = len(PNG_SIGNATURE)
    while pos < len(data):
        if pos + 8 > len(data):
            raise IconError("truncated PNG chunk header")
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        chunk_type = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if chunk_type == b"IHDR":
            if len(chunk) != 13:
                raise IconError("invalid IHDR chunk")
            width, height, bit_depth, color_type, *_ = struct.unpack(">IIBBBBB", chunk)
            if bit_depth != 8:
                raise IconError(f"unsupported bit depth {bit_depth}; expected 8")
        elif chunk_type == b"IDAT":
            idat.extend(chunk)
        elif chunk_type == b"IEND":
            break

    if width is None or not idat:
        raise IconError("PNG has no image data")
    raw = zlib.decompress(bytes(idat))
    channels = {2: 3, 6: 4}.get(color_type)
    if channels is None:
        raise IconError(f"unsupported color type {color_type}; expected RGB or RGBA")
    stride = channels * width
    if len(raw) != (stride + 1) * height:
        raise IconError("PNG pixel data length does not match its dimensions")

    rows: list[bytes] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        kind = raw[pos]
        pos += 1
        line = bytearray(raw[pos : pos + stride])
        pos += stride
        if kind == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif kind == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif kind == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif kind == 4:  # Paeth
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = prev[i]
                upper_left = prev[i - channels] if i >= channels else 0
                estimate = left + up - upper_left
                pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - upper_left)
                if pa <= pb and pa <= pc:
                    predictor = left
                elif pb <= pc:
                    predictor = up
                else:
                    predictor = upper_left
                line[i] = (line[i] + predictor) & 0xFF
        elif kind != 0:  # None
            raise IconError(f"unknown PNG filter type {kind}")
        rows.append(bytes(line))
        prev = line

    if color_type == 6:
        return b"".join(rows), width, height
    rgba = bytearray(width * height * 4)
    for y, row in enumerate(rows):
        for x in range(width):
            offset = (y * width + x) * 4
            pixel = row[x * 3 : x * 3 + 3]
            rgba[offset : offset + 3] = pixel
            rgba[offset + 3] = 0xFF
    return bytes(rgba), width, height


def validate_source(path: Path) -> tuple[bytes, int, int]:
    """Return the decoded master artwork, or fail the build with a clear reason.

    A non-square or undersized source is rejected rather than cropped or
    upscaled: silently reshaping the master artwork is how an application icon
    ends up looking wrong on a platform.
    """
    if not path.is_file():
        raise IconError(f"source icon is missing: {path}")
    try:
        rgba, width, height = read_png(path)
    except OSError as error:
        raise IconError(f"cannot read source icon {path}: {error}") from error
    if width != height:
        raise IconError(f"source icon must be square, got {width}x{height}: {path}")
    if width < MIN_SOURCE_SIZE:
        raise IconError(
            f"source icon must be at least {MIN_SOURCE_SIZE}x{MIN_SOURCE_SIZE}, "
            f"got {width}x{height}: {path}"
        )
    return rgba, width, height


def _resize(rgba: bytes, source_w: int, source_h: int, width: int, height: int) -> bytes:
    """Bilinear resize of a packed RGBA image."""
    if width == source_w and height == source_h:
        return rgba
    out = bytearray(width * height * 4)
    x_ratio = source_w / width
    y_ratio = source_h / height
    for y in range(height):
        sy = (y + 0.5) * y_ratio - 0.5
        y0 = max(int(sy), 0)
        y1 = min(y0 + 1, source_h - 1)
        fy = sy - y0
        for x in range(width):
            sx = (x + 0.5) * x_ratio - 0.5
            x0 = max(int(sx), 0)
            x1 = min(x0 + 1, source_w - 1)
            fx = sx - x0
            p00 = (y0 * source_w + x0) * 4
            p10 = (y0 * source_w + x1) * 4
            p01 = (y1 * source_w + x0) * 4
            p11 = (y1 * source_w + x1) * 4
            for channel in range(4):
                top = rgba[p00 + channel] + (rgba[p10 + channel] - rgba[p00 + channel]) * fx
                bottom = rgba[p01 + channel] + (rgba[p11 + channel] - rgba[p01 + channel]) * fx
                sample = top + (bottom - top) * fy
                out[(y * width + x) * 4 + channel] = max(0, min(255, round(sample)))
    return bytes(out)


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    inner = chunk_type + payload
    return struct.pack(">I", len(payload)) + inner + struct.pack(">I", zlib.crc32(inner))


def _encode_png(rgba: bytes, width: int, height: int) -> bytes:
    """Encode packed RGBA pixels as an 8-bit, non-interlaced PNG."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = bytearray(width * height * 4 + height)
    for y in range(height):
        row = y * (width * 4 + 1)
        raw[row] = 0
        raw[row + 1 : row + 1 + width * 4] = rgba[y * width * 4 : (y + 1) * width * 4]
    idat = zlib.compress(bytes(raw), 9)
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def required_sizes(source_size: int) -> list[int]:
    """Every pixel size the generated formats need, capped at the source size."""
    wanted = {*ICO_SIZES, *(size for _, size in ICNS_ELEMENTS), *(s for _, s in ICONSET_ENTRIES)}
    return sorted(size for size in wanted if size <= source_size)


def build_ico(scaled: dict[int, bytes]) -> bytes:
    """Package PNG-compressed entries into a Windows .ico container."""
    header = struct.pack("<HHH", 0, 1, len(ICO_SIZES))
    offset = 6 + 16 * len(ICO_SIZES)
    entries = bytearray()
    for size in ICO_SIZES:
        data = scaled[size]
        dimension = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    body = b"".join(scaled[size] for size in ICO_SIZES)
    return header + bytes(entries) + body


def build_icns(scaled: dict[int, bytes]) -> bytes:
    """Package PNG elements into a macOS .icns container.

    Used where ``iconutil`` is unavailable. Elements larger than the source
    artwork are skipped rather than upscaled.
    """
    chunks = bytearray()
    for element, size in ICNS_ELEMENTS:
        data = scaled.get(size)
        if data is None:
            continue
        if len(data) % 2:
            data += b"\x00"
        chunks += element.encode() + struct.pack(">I", 8 + len(data)) + data
    return ICNS_MAGIC + struct.pack(">I", 8 + len(chunks)) + bytes(chunks)


def _iconutil() -> str | None:
    """The macOS ``iconutil`` executable, when this build can use it."""
    return shutil.which("iconutil")


def write_icns(dest: Path, scaled: dict[int, bytes]) -> None:
    """Write ``dest`` as a native macOS .icns.

    Prefers ``iconutil``, which builds the standard ``.iconset`` layout and lets
    macOS itself assemble the container. Falls back to the standard library
    writer on platforms where ``iconutil`` does not exist.
    """
    iconutil = _iconutil()
    if iconutil is None:
        dest.write_bytes(build_icns(scaled))
        return

    iconset = dest.parent / ICONSET_DIRNAME
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    try:
        for name, size in ICONSET_ENTRIES:
            data = scaled.get(size)
            if data is not None:
                (iconset / name).write_bytes(data)
        result = subprocess.run(
            [iconutil, "-c", "icns", "-o", str(dest), str(iconset)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not dest.is_file():
            detail = (result.stderr or result.stdout).strip()
            raise IconError(f"iconutil failed (exit {result.returncode}): {detail}")
    finally:
        shutil.rmtree(iconset, ignore_errors=True)


def clean(output: Path) -> None:
    """Remove previously generated icons so a stale artifact is never packaged."""
    shutil.rmtree(output / ICONSET_DIRNAME, ignore_errors=True)
    for name in ("icon.ico", "icon.icns"):
        (output / name).unlink(missing_ok=True)


def generate(source: str | Path, output_dir: str | Path) -> dict[str, Path]:
    """Write ``icon.ico`` and ``icon.icns`` derived from ``source``.

    ``output_dir`` is created if needed. Returns the generated file paths.
    """
    source = Path(source)
    output = Path(output_dir)
    rgba, width, height = validate_source(source)

    scaled = {
        size: _encode_png(_resize(rgba, width, height, size, size), size, size)
        for size in required_sizes(width)
    }

    clean(output)
    output.mkdir(parents=True, exist_ok=True)
    ico = output / "icon.ico"
    icns = output / "icon.icns"
    ico.write_bytes(build_ico(scaled))
    write_icns(icns, scaled)
    return {"ico": ico, "icns": icns}


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    source = Path(args[0]) if args else DEFAULT_SOURCE
    output = Path(args[1]) if len(args) > 1 else DEFAULT_OUTPUT
    try:
        generated = generate(source, output)
    except IconError as error:
        print(f"icon generation failed: {error}", file=sys.stderr)
        return 1
    for path in generated.values():
        print(f"generated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
