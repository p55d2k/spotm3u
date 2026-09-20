"""Generate platform icon formats from the canonical ``assets/icon.png``.

`assets/icon.png` is the single source of truth for the spotm3u icon. PyInstaller
needs ICO/ICNS rather than PNG, so the build (``uv run build``) regenerates:

    assets/generated/icon.ico    Windows executable icon
    assets/generated/icon.icns   macOS .app bundle icon

Only the standard library is used: the 8-bit RGBA PNG source is decoded, scaled
with bilinear resampling, and re-encoded directly into the ICO (PNG-compressed,
Vista+) and ICNS (PNG element) containers. Generation is deterministic, so a
local build and a GitHub Actions build always produce identical assets, and the
files are never edited by hand. Run it as:

    python packaging/generate_icons.py [source.png] [output-dir]

or just invoke ``uv run build``, which generates the icons before packaging.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICNS_MAGIC = b"icns"
# Element types with the pixel size they hold; both 1x and @2x members are
# included so macOS picks the sharpest element for the current display scale.
ICNS_ELEMENTS = (
    ("ic10", 128),
    ("ic07", 256),
    ("ic13", 256),
    ("ic11", 512),
    ("ic14", 512),
)
ICO_SIZES = (16, 32, 48, 64, 128, 256)
DEFAULT_SOURCE = Path("assets") / "icon.png"
DEFAULT_OUTPUT = Path("assets") / "generated"


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


def _build_ico(scaled: dict[int, bytes]) -> bytes:
    """Package PNG-compressed entries into a Windows .ico container."""
    sizes = [size for size in ICO_SIZES]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = bytearray()
    for size in sizes:
        data = scaled[size]
        dimension = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    body = b"".join(scaled[size] for size in sizes)
    return header + bytes(entries) + body


def _build_icns(scaled: dict[int, bytes]) -> bytes:
    """Package PNG elements into a macOS .icns container."""
    chunks = bytearray()
    for element, size in ICNS_ELEMENTS:
        data = scaled[size]
        if len(data) % 2:
            data += b"\x00"
        chunks += element.encode() + struct.pack(">I", 8 + len(data)) + data
    return ICNS_MAGIC + struct.pack(">I", 8 + len(chunks)) + bytes(chunks)


def generate(source: str | Path, output_dir: str | Path) -> dict[str, Path]:
    """Write ``icon.ico`` and ``icon.icns`` derived from ``source``.

    ``output_dir`` is created if needed. Returns the generated file paths.
    """
    source = Path(source)
    output = Path(output_dir)
    rgba, width, height = read_png(source)
    min_side = min(width, height)
    sizes = sorted({*(size for size in ICO_SIZES if size <= 256), *(s for _, s in ICNS_ELEMENTS)})
    if min_side < 16:
        raise IconError(f"source image too small: {width}x{height}")

    scaled = {
        size: _encode_png(_resize(rgba, width, height, size, size), size, size) for size in sizes
    }
    output.mkdir(parents=True, exist_ok=True)
    ico = output / "icon.ico"
    icns = output / "icon.icns"
    ico.write_bytes(_build_ico(scaled))
    icns.write_bytes(_build_icns(scaled))
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
