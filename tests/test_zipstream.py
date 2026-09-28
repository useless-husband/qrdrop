import io
import os
import tempfile
import unittest
import zipfile

from qrdrop import zipstream
from qrdrop.zipstream import stream_zip


class TestZipStream(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.files = {}
        rng_bytes = bytes(range(256)) * 1000
        for name, data in {"a.txt": b"hello " * 5000, "b.jpg": rng_bytes, "空.txt": "你好".encode(), "empty.bin": b"", "sub.dir.txt": b"x"}.items():
            with open(os.path.join(self.d, name), "wb") as fh:
                fh.write(data)
            self.files[name] = data

    def tearDown(self):
        self.tmp.cleanup()

    def build(self):
        return [(("folder/" + n) if n == "sub.dir.txt" else n, os.path.join(self.d, n)) for n in self.files]

    def test_readable_by_zipfile(self):
        data = b"".join(stream_zip(self.build()))
        zf = zipfile.ZipFile(io.BytesIO(data))
        self.assertIsNone(zf.testzip())
        self.assertEqual(zf.namelist(), ["a.txt", "b.jpg", "空.txt", "empty.bin", "folder/sub.dir.txt"])
        for arc, content in [("a.txt", self.files["a.txt"]), ("空.txt", self.files["空.txt"]), ("folder/sub.dir.txt", b"x"), ("empty.bin", b"")]:
            self.assertEqual(zf.read(arc), content)

    def test_stored_vs_deflated(self):
        zf = zipfile.ZipFile(io.BytesIO(b"".join(stream_zip(self.build()))))
        self.assertEqual(zf.getinfo("b.jpg").compress_type, zipfile.ZIP_STORED)
        self.assertEqual(zf.getinfo("a.txt").compress_type, zipfile.ZIP_DEFLATED)
        self.assertLess(zf.getinfo("a.txt").compress_size, 1000)

    def test_is_streaming_generator(self):
        gen = stream_zip(self.build())
        first = next(gen)
        self.assertTrue(first.startswith(b"PK\x03\x04"))
        # 大檔不會一次全部 yield
        big = os.path.join(self.d, "big.txt")
        with open(big, "wb") as fh:
            fh.write(b"0123456789abcdef" * 200_000)
        chunks = list(stream_zip([("big.txt", big)]))
        self.assertGreater(len(chunks), 3)
        self.assertLess(max(map(len, chunks)), 1_000_000)

    def test_unicode_flag_set(self):
        data = b"".join(stream_zip(self.build()))
        zf = zipfile.ZipFile(io.BytesIO(data))
        self.assertTrue(zf.getinfo("空.txt").flag_bits & 0x800)

    def test_zip64_mode_forced(self):
        old = zipstream.ZIP64_THRESHOLD
        zipstream.ZIP64_THRESHOLD = 1000
        try:
            data = b"".join(stream_zip(self.build()))
        finally:
            zipstream.ZIP64_THRESHOLD = old
        zf = zipfile.ZipFile(io.BytesIO(data))
        self.assertIsNone(zf.testzip())
        self.assertEqual(zf.read("a.txt"), self.files["a.txt"])
        self.assertEqual(zf.read("b.jpg"), self.files["b.jpg"])

    def test_no_files(self):
        zf = zipfile.ZipFile(io.BytesIO(b"".join(stream_zip([]))))
        self.assertEqual(zf.namelist(), [])

    def test_leading_slash_and_backslash_normalized(self):
        p = os.path.join(self.d, "a.txt")
        zf = zipfile.ZipFile(io.BytesIO(b"".join(stream_zip([("/x\\y.txt", p)]))))
        self.assertEqual(zf.namelist(), ["x/y.txt"])


if __name__ == "__main__":
    unittest.main()
