import re
import struct
import unittest
import zlib
import xml.etree.ElementTree as ET

from qrdrop import qrcode as Q
from qrdrop import render


def read_png(data):
    """最小 PNG 解碼器（只支援本專案輸出的 1 位元灰階、filter 0）。"""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, []
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        (crc,) = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])
        assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF, tag
        chunks.append((tag, body))
        pos += 12 + n
    assert chunks[0][0] == b"IHDR" and chunks[-1][0] == b"IEND"
    w, h, depth, ctype, comp, filt, inter = struct.unpack(">IIBBBBB", chunks[0][1])
    raw = zlib.decompress(b"".join(b for t, b in chunks if t == b"IDAT"))
    stride = (w * depth + 7) // 8
    assert len(raw) == h * (stride + 1)
    rows = []
    for y in range(h):
        line = raw[y * (stride + 1):(y + 1) * (stride + 1)]
        assert line[0] == 0
        bits = "".join(f"{b:08b}" for b in line[1:])[:w]
        rows.append([c == "0" for c in bits])  # True = 黑
    return w, h, depth, ctype, rows


class TestPng(unittest.TestCase):
    def test_png_roundtrip_pixels(self):
        qr = Q.encode("png test 測試", "Q")
        for scale, border in ((1, 0), (3, 2), (8, 4), (5, 1)):
            w, h, depth, ctype, rows = read_png(render.to_png(qr, scale, border))
            self.assertEqual((depth, ctype), (1, 0))
            self.assertEqual(w, (qr.size + 2 * border) * scale)
            self.assertEqual(h, w)
            for y in range(qr.size):
                for x in range(qr.size):
                    self.assertEqual(rows[(y + border) * scale + scale // 2][(x + border) * scale + scale // 2], qr.modules[y][x])
            if border:
                self.assertFalse(any(rows[0]))
                self.assertFalse(any(r[0] for r in rows))

    def test_png_non_multiple_of_8_width(self):
        qr = Q.encode("1")
        w, h, *_ , rows = read_png(render.to_png(qr, 1, 0))
        self.assertEqual(w, 21)
        self.assertEqual([r for r in rows], qr.modules)

    def test_png_bad_args(self):
        qr = Q.encode("1")
        with self.assertRaises(ValueError):
            render.to_png(qr, 0)
        with self.assertRaises(ValueError):
            render.to_png(qr, 1, -1)

    def test_png_reasonable_size(self):
        self.assertLess(len(render.to_png(Q.encode("a" * 1000, "L"), 8, 4)), 100_000)


class TestSvg(unittest.TestCase):
    def test_svg_wellformed_and_matches(self):
        qr = Q.encode("svg 測試", "M")
        svg = render.to_svg(qr, 4)
        root = ET.fromstring(svg.split("?>", 1)[1])
        self.assertTrue(root.tag.endswith("svg"))
        w = qr.size + 8
        self.assertEqual(root.get("viewBox"), f"0 0 {w} {w}")
        d = [e for e in root if e.tag.endswith("path")][0].get("d")
        dark = set()
        for x, y, run in re.findall(r"M(\d+),(\d+)h(\d+)v1h-\d+z", d):
            for i in range(int(run)):
                dark.add((int(x) + i - 4, int(y) - 4))
        expect = {(x, y) for y in range(qr.size) for x in range(qr.size) if qr.modules[y][x]}
        self.assertEqual(dark, expect)

    def test_svg_no_script(self):
        self.assertNotIn("<script", render.to_svg(Q.encode("x")))


class TestTerminal(unittest.TestCase):
    def setUp(self):
        self.qr = Q.encode("HELLO", "L")

    def test_dimensions(self):
        out = render.to_terminal(self.qr, 4, color=False)
        lines = out.rstrip("\n").split("\n")
        w = self.qr.size + 8
        self.assertTrue(all(len(l) == w for l in lines))
        self.assertEqual(len(lines), (w + 1) // 2)

    def test_only_expected_chars(self):
        out = render.to_terminal(self.qr, 4, color=False)
        self.assertLessEqual(set(out) - {"\n"}, set(" ▀▄█"))

    def test_quiet_zone_is_light(self):
        # 非 invert：淺色=實心方塊，所以最上面 quiet zone 的行應全是 █
        lines = render.to_terminal(self.qr, 4, color=False).split("\n")
        self.assertEqual(set(lines[0]), {"█"})
        self.assertEqual(set(lines[1]), {"█"})
        inv = render.to_terminal(self.qr, 4, color=False, invert=True).split("\n")
        self.assertEqual(set(inv[0]), {" "})

    def test_color_mode_black_on_white(self):
        out = render.to_terminal(self.qr, 4, color=True)
        first = out.split("\n")[0]
        self.assertTrue(first.startswith("\x1b[38;2;0;0;0m\x1b[48;2;255;255;255m"))
        self.assertTrue(first.endswith("\x1b[0m"))
        body = re.sub(r"\x1b\[[0-9;]*m", "", first)
        self.assertEqual(set(body), {" "})  # 上方 quiet zone 全白

    def test_half_block_decoding_matches_modules(self):
        border = 2
        out = render.to_terminal(self.qr, border, color=True)
        lines = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in out.rstrip("\n").split("\n")]
        for y in range(self.qr.size):
            for x in range(self.qr.size):
                ch = lines[(y + border) // 2][x + border]
                top_row = (y + border) % 2 == 0
                dark = ch in ("█", "▀") if top_row else ch in ("█", "▄")
                self.assertEqual(dark, self.qr.modules[y][x])

    def test_plain_and_invert_are_complements(self):
        a = render.to_terminal(self.qr, 1, color=False).replace("█", "#").replace(" ", "█").replace("#", " ").replace("▀", "%").replace("▄", "▀").replace("%", "▄")
        b = render.to_terminal(self.qr, 1, color=False, invert=True)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
