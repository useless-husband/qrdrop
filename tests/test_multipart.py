import random
import unittest

from qrdrop.multipart import MultipartError, MultipartParser, parse_boundary, parse_part_headers


def build(parts, boundary="XyZ123", preamble=b"", epilogue=b""):
    out = preamble
    for name, filename, data, extra in parts:
        out += b"--" + boundary.encode() + b"\r\n"
        disp = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disp += f'; filename="{filename}"'
        out += disp.encode("utf-8") + b"\r\n" + extra + b"\r\n" + data + b"\r\n"
    out += b"--" + boundary.encode() + b"--\r\n" + epilogue
    return out


def parse(body, boundary="XyZ123", chunk=None):
    got = []
    cur = {}

    def on_part(h):
        cur.clear()
        cur.update(h, data=bytearray())

    def on_data(b):
        cur["data"] += b

    def on_end():
        got.append(dict(cur, data=bytes(cur["data"])))

    p = MultipartParser(boundary.encode(), on_part, on_data, on_end)
    if chunk is None:
        p.feed(body)
    else:
        for i in range(0, len(body), chunk):
            p.feed(body[i:i + chunk])
    p.close()
    return got


class TestBoundary(unittest.TestCase):
    def test_parse_boundary(self):
        self.assertEqual(parse_boundary("multipart/form-data; boundary=abc"), b"abc")
        self.assertEqual(parse_boundary('multipart/form-data; boundary="a b:c"'), b"a b:c")
        self.assertEqual(parse_boundary("Multipart/Form-Data;charset=x; BOUNDARY=zz"), b"zz")

    def test_bad_content_type(self):
        for bad in (None, "", "text/plain", "multipart/form-data", "application/json; boundary=x", "multipart/mixed; boundary=x"):
            with self.assertRaises(MultipartError):
                parse_boundary(bad)


class TestParser(unittest.TestCase):
    def test_single_file(self):
        got = parse(build([("files", "a.txt", b"hello", b"Content-Type: text/plain\r\n")]))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["filename"], "a.txt")
        self.assertEqual(got[0]["name"], "files")
        self.assertEqual(got[0]["data"], b"hello")
        self.assertEqual(got[0]["content_type"], "text/plain")

    def test_multiple_parts_and_field(self):
        got = parse(build([("note", None, b"hi", b""), ("files", "a.bin", b"\x00\x01\r\n--XyZ12\r\n", b""), ("files", "b.bin", b"", b"")]))
        self.assertEqual([g["filename"] for g in got], [None, "a.bin", "b.bin"])
        self.assertEqual(got[1]["data"], b"\x00\x01\r\n--XyZ12\r\n")
        self.assertEqual(got[2]["data"], b"")

    def test_every_chunk_size(self):
        payload = bytes(random.Random(5).randrange(256) for _ in range(3000))
        body = build([("files", "x.bin", payload, b""), ("files", "y.bin", payload[::-1], b"")], preamble=b"junk before\r\n", epilogue=b"tail")
        for chunk in (1, 2, 3, 7, 13, 64, 1000, 5000):
            got = parse(body, chunk=chunk)
            self.assertEqual([g["data"] for g in got], [payload, payload[::-1]], chunk)

    def test_binary_containing_partial_boundary(self):
        data = b"\r\n--Xy" * 50 + b"\r\n-" + b"--XyZ12"
        got = parse(build([("files", "p", data, b"")]), chunk=5)
        self.assertEqual(got[0]["data"], data)

    def test_utf8_filenames(self):
        for name in ("照片 1.jpg", "🙂.png", "ü.txt"):
            got = parse(build([("files", name, b"z", b"")]))
            self.assertEqual(got[0]["filename"], name)

    def test_escaped_quote_in_filename(self):
        got = parse(build([("files", 'a\\"b.txt', b"z", b"")]))
        self.assertEqual(got[0]["filename"], 'a"b.txt')

    def test_traversal_filename_preserved_for_sanitizer(self):
        got = parse(build([("files", "../../etc/passwd", b"z", b"")]))
        self.assertEqual(got[0]["filename"], "../../etc/passwd")

    def test_empty_filename(self):
        got = parse(build([("files", "", b"", b"")]))
        self.assertEqual(got[0]["filename"], "")

    def test_truncated_body_raises(self):
        body = build([("files", "a", b"x" * 100, b"")])
        with self.assertRaises(MultipartError):
            parse(body[:-30])

    def test_garbage_raises(self):
        with self.assertRaises(MultipartError):
            parse(b"not multipart at all")

    def test_missing_disposition(self):
        with self.assertRaises(MultipartError):
            parse(b"--XyZ123\r\nContent-Type: text/plain\r\n\r\nx\r\n--XyZ123--\r\n")

    def test_header_too_long(self):
        with self.assertRaises(MultipartError):
            parse(b"--XyZ123\r\n" + b"X-Junk: " + b"a" * 40000)

    def test_callback_exception_propagates(self):
        def boom(_):
            raise RuntimeError("stop")

        p = MultipartParser(b"XyZ123", lambda h: None, boom, lambda: None)
        with self.assertRaises(RuntimeError):
            p.feed(build([("files", "a", b"hello", b"")]))

    def test_parse_part_headers_requires_form_data(self):
        with self.assertRaises(MultipartError):
            parse_part_headers(b'Content-Disposition: attachment; filename="a"\r\n')


if __name__ == "__main__":
    unittest.main()
