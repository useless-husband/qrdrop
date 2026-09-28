"""把 QRCode 畫成終端機文字、PNG（自己寫）或 SVG。"""

from __future__ import annotations

import struct
import zlib
from typing import List

from .qrcode import QRCode

_ANSI_BLACK_ON_WHITE = "\x1b[38;2;0;0;0m\x1b[48;2;255;255;255m"
_ANSI_RESET = "\x1b[0m"


def _padded(qr: QRCode, border: int) -> List[List[bool]]:
    w = qr.size + 2 * border
    rows = [[False] * w for _ in range(border)]
    rows += [[False] * border + list(r) + [False] * border for r in qr.modules]
    rows += [[False] * w for _ in range(border)]
    return rows


def to_terminal(qr: QRCode, border: int = 4, color: bool = True, invert: bool = False) -> str:
    """用 Unicode 半格方塊（上下各一個模組）畫 QR Code。

    color=True：明確指定黑字白底的 24 位元色，quiet zone 也是白的，
    所以不論終端機是深色或淺色背景都能掃。
    color=False：不用 ANSI；預設假設終端機是深色背景（淺色模組畫成實心方塊），
    淺色背景請加 invert=True。
    """
    rows = _padded(qr, border)
    if len(rows) % 2:
        rows.append([False] * len(rows[0]))
    lines = []
    for y in range(0, len(rows), 2):
        top, bot = rows[y], rows[y + 1]
        chars = []
        for t, b in zip(top, bot):
            if color:
                # 前景黑：深色模組 = 有墨水
                chars.append("█" if t and b else "▀" if t else "▄" if b else " ")
            else:
                # 無色：以「發光」表示淺色（深色背景）；invert 則以「發光」表示深色
                t2, b2 = (t, b) if invert else (not t, not b)
                chars.append("█" if t2 and b2 else "▀" if t2 else "▄" if b2 else " ")
        line = "".join(chars)
        lines.append(f"{_ANSI_BLACK_ON_WHITE}{line}{_ANSI_RESET}" if color else line)
    return "\n".join(lines) + "\n"


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def to_png(qr: QRCode, scale: int = 8, border: int = 4) -> bytes:
    """輸出 1 位元灰階 PNG（黑白），不依賴任何影像函式庫。"""
    if scale < 1 or border < 0:
        raise ValueError("scale 必須 >= 1、border 必須 >= 0")
    rows = _padded(qr, border)
    width = len(rows[0]) * scale
    height = len(rows) * scale
    raw = bytearray()
    for row in rows:
        bits = "".join(("0" if dark else "1") * scale for dark in row)  # 0=黑 1=白
        bits += "1" * (-len(bits) % 8)
        line = b"\x00" + int(bits, 2).to_bytes(len(bits) // 8, "big")
        raw += line * scale
    ihdr = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def to_svg(qr: QRCode, border: int = 4, scale: int = 10) -> str:
    """輸出 SVG：白底加一條由多個橫向線段組成的黑色路徑。"""
    if border < 0:
        raise ValueError("border 必須 >= 0")
    w = qr.size + 2 * border
    parts = []
    for y, row in enumerate(qr.modules):
        x = 0
        while x < qr.size:
            if row[x]:
                start = x
                while x < qr.size and row[x]:
                    x += 1
                parts.append(f"M{start + border},{y + border}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    px = w * scale
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {w}" width="{px}" height="{px}" '
        'shape-rendering="crispEdges">\n'
        f'<rect width="{w}" height="{w}" fill="#ffffff"/>\n'
        f'<path d="{"".join(parts)}" fill="#000000"/>\n'
        "</svg>\n"
    )
