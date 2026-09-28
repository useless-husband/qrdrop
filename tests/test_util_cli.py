import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from qrdrop import cli, render
from qrdrop.multipart import parse_part_headers
from qrdrop.qrcode import encode
from qrdrop.util import human_duration, human_size, lan_ip, parse_duration, parse_size
from tests.test_render import read_png


class TestParsing(unittest.TestCase):
    def test_parse_size(self):
        self.assertEqual(parse_size("100"), 100)
        self.assertEqual(parse_size("1K"), 1024)
        self.assertEqual(parse_size("500m"), 500 * 1024 ** 2)
        self.assertEqual(parse_size("2G"), 2 * 1024 ** 3)
        self.assertEqual(parse_size("1.5GB"), int(1.5 * 1024 ** 3))
        self.assertEqual(parse_size(" 3 mb "), 3 * 1024 ** 2)

    def test_parse_size_bad(self):
        for bad in ("", "abc", "-1", "0", "10X", "1e3", "M"):
            with self.assertRaises(ValueError, msg=bad):
                parse_size(bad)

    def test_parse_duration(self):
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("10m"), 600)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("600"), 600)
        self.assertEqual(parse_duration("0.5m"), 30)
        for off in ("0", "off", "none"):
            self.assertIsNone(parse_duration(off))

    def test_parse_duration_bad(self):
        for bad in ("x", "10x", "-5m", "m"):
            with self.assertRaises(ValueError, msg=bad):
                parse_duration(bad)

    def test_human(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(1023), "1023 B")
        self.assertEqual(human_size(1536), "1.5 KB")
        self.assertEqual(human_size(5 * 1024 ** 2), "5.0 MB")
        self.assertEqual(human_size(3 * 1024 ** 3), "3.0 GB")
        self.assertEqual(human_duration(600), "10 分鐘")
        self.assertEqual(human_duration(7200), "2 小時")
        self.assertEqual(human_duration(90), "90 秒")

    def test_lan_ip_shape(self):
        ip = lan_ip()
        if ip is not None:
            parts = ip.split(".")
            self.assertEqual(len(parts), 4)
            self.assertFalse(ip.startswith("127."))

    def test_rfc5987_filename(self):
        h = parse_part_headers(b"Content-Disposition: form-data; name=\"f\"; filename*=UTF-8''%E7%85%A7%E7%89%87.jpg\r\n")
        self.assertEqual(h["filename"], "照片.jpg")

    def test_folded_header(self):
        h = parse_part_headers(b"Content-Disposition: form-data;\r\n name=\"f\"; filename=\"a.txt\"\r\nContent-Type: text/plain\r\n")
        self.assertEqual((h["name"], h["filename"], h["content_type"]), ("f", "a.txt", "text/plain"))


class TestCli(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_qr_prints_terminal(self):
        code, out, _ = self.run_cli("qr", "hello", "--plain")
        self.assertEqual(code, 0)
        self.assertEqual(out, render.to_terminal(encode("hello", "M"), 4, color=False))

    def test_qr_png_and_svg_files(self):
        with tempfile.TemporaryDirectory() as d:
            png, svg = os.path.join(d, "a.png"), os.path.join(d, "a.svg")
            code, out, err = self.run_cli("qr", "你好 QR", "--ecc", "H", "--png", png, "--svg", svg, "--scale", "4", "--info")
            self.assertEqual(code, 0)
            self.assertEqual(out, "")  # 有輸出檔時不印終端機
            self.assertIn("等級 H", err)
            with open(png, "rb") as fh:
                w, h, depth, ctype, rows = read_png(fh.read())
            qr = encode("你好 QR", "H")
            self.assertEqual(w, (qr.size + 8) * 4)
            with open(svg, encoding="utf-8") as fh:
                self.assertIn("<svg", fh.read())

    def test_qr_show_with_file(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = self.run_cli("qr", "x", "--png", os.path.join(d, "a.png"), "--show", "--plain")
            self.assertEqual(code, 0)
            self.assertTrue(out)

    def test_qr_too_long(self):
        code, _, err = self.run_cli("qr", "a" * 5000, "--ecc", "L")
        self.assertEqual(code, 2)
        self.assertIn("太長", err)

    def test_qr_stdin(self):
        with mock.patch("sys.stdin", io.StringIO("from stdin\n")):
            code, out, _ = self.run_cli("qr", "-", "--plain")
        self.assertEqual(code, 0)
        self.assertEqual(out, render.to_terminal(encode("from stdin", "M"), 4, color=False))

    def test_qr_write_error(self):
        code, _, err = self.run_cli("qr", "x", "--png", "/nonexistent-dir-qrdrop/a.png")
        self.assertEqual(code, 1)

    def test_bad_pin_rejected(self):
        for pin in ("123", "12345", "abcd", "12 4"):
            code, _, err = self.run_cli("send", "--pin", pin, "somefile")
            self.assertEqual(code, 2, pin)
            self.assertIn("PIN", err)

    def test_bad_timeout_and_size(self):
        self.assertEqual(self.run_cli("receive", "--timeout", "abc")[0], 2)
        self.assertEqual(self.run_cli("receive", "--max-size", "lots")[0], 2)
        self.assertEqual(self.run_cli("receive", "--port", "99999")[0], 2)

    def test_send_missing_file(self):
        code, _, err = self.run_cli("send", "/definitely/not/here.txt")
        self.assertEqual(code, 2)
        self.assertIn("找不到", err)

    def test_version_and_help(self):
        code, out, _ = self.run_cli("--version")
        self.assertEqual(code, 0)
        self.assertIn("qrdrop", out)
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("send", out)


if __name__ == "__main__":
    unittest.main()
