import os
import pathlib
import tempfile
import unittest

from qrdrop.names import sanitize_filename, unique_path

EVIL = [
    "../../etc/passwd", "..\\..\\Windows\\System32\\config", "/etc/passwd", "C:\\Windows\\win.ini", "C:/Users/x/y.txt",
    "....//....//x", "..", ".", "...", "  ..  ", "foo/../bar", "a/b/c/d.txt", "\\\\server\\share\\f.txt",
    "file\x00.txt", "\x00", "a\x00/../../b", "nul", "CON", "con.txt", "Aux.tar.gz", "COM1", "lpt9.log", "NUL.", "prn ",
    "file.txt.", "file.txt   ", " leading.txt", ".hidden", ".bashrc", "name:stream", "a<b>c|d?e*f\"g.txt",
    "evil\u202egpj.exe", "\u200bzero.txt", "line\nbreak.txt", "tab\tname", "\x1b[31mred.txt", "%2e%2e%2fpasswd",
    "", None, "   ", "a" * 300 + ".jpg", "中" * 200 + ".txt", "🙂" * 100, "con\u00b9", "COM\u00b2.txt", "CONIN$", "..\\", "../", "./x",
    "x/", "/", "\\", "a\\b/c", "~root", "$(rm -rf).txt", "`id`.txt", "-rf", "file\r\n.txt",
]


class TestSanitize(unittest.TestCase):
    def test_evil_names_produce_safe_results(self):
        for raw in EVIL:
            with self.subTest(raw=raw):
                s = sanitize_filename(raw)
                self.assertTrue(s)
                self.assertNotIn("/", s)
                self.assertNotIn("\\", s)
                self.assertNotIn("\x00", s)
                self.assertNotIn(s, ("", ".", ".."))
                self.assertFalse(s.startswith("."))
                self.assertFalse(s.endswith((".", " ")))
                self.assertFalse(any(ord(c) < 32 or ord(c) == 127 for c in s))
                self.assertFalse(any(c in s for c in '<>:"|?*'))
                self.assertLessEqual(len(s.encode("utf-8")), 255)
                stem = s.split(".")[0].strip().upper()
                self.assertNotIn(stem, {"CON", "PRN", "AUX", "NUL", "COM1", "LPT9", "COM\u00b9", "COM\u00b2"})

    def test_specific_results(self):
        cases = {
            "../../etc/passwd": "passwd",
            "/etc/passwd": "passwd",
            "C:\\Windows\\win.ini": "win.ini",
            "photo.jpg": "photo.jpg",
            "我的照片 (1).HEIC": "我的照片 (1).HEIC",
            "..": "file",
            "": "file",
            None: "file",
            "file\x00.txt": "file.txt",
            "CON": "_CON",
            "con.txt": "_con.txt",
            "COM1": "_COM1",
            "report.txt.": "report.txt",
            ".bashrc": "bashrc",
            "a:b.txt": "a_b.txt",
            "x/": "file",
        }
        for raw, want in cases.items():
            self.assertEqual(sanitize_filename(raw), want, raw)

    def test_default_param(self):
        self.assertEqual(sanitize_filename("..", default="upload"), "upload")

    def test_long_name_keeps_extension(self):
        s = sanitize_filename("a" * 400 + ".jpeg")
        self.assertTrue(s.endswith(".jpeg"))
        self.assertLessEqual(len(s.encode()), 200)
        s = sanitize_filename("中" * 200 + ".txt")
        self.assertTrue(s.endswith(".txt"))
        self.assertLessEqual(len(s.encode()), 200)

    def test_bytes_input(self):
        self.assertEqual(sanitize_filename("你好.txt".encode()), "你好.txt")

    def test_idempotent(self):
        for raw in EVIL:
            once = sanitize_filename(raw)
            self.assertEqual(sanitize_filename(once), once, raw)


class TestUniquePath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_name_gets_suffix(self):
        names = []
        for _ in range(4):
            p, fh = unique_path(self.d, "a.txt")
            fh.close()
            names.append(os.path.basename(p))
        self.assertEqual(names, ["a.txt", "a (1).txt", "a (2).txt", "a (3).txt"])

    def test_no_extension(self):
        p1, f1 = unique_path(self.d, "README")
        p2, f2 = unique_path(self.d, "README")
        f1.close(); f2.close()
        self.assertEqual(os.path.basename(p2), "README (1)")

    def test_never_escapes_directory(self):
        for raw in EVIL:
            p, fh = unique_path(self.d, raw)
            fh.close()
            self.assertEqual(os.path.dirname(p), self.d, raw)
        self.assertFalse(os.path.exists(os.path.join(self.d, "..", "passwd")))

    def test_does_not_overwrite(self):
        p, fh = unique_path(self.d, "keep.txt")
        fh.write(b"original")
        fh.close()
        p2, fh2 = unique_path(self.d, "keep.txt")
        fh2.write(b"new")
        fh2.close()
        self.assertEqual(pathlib.Path(p).read_bytes(), b"original")

    @unittest.skipUnless(hasattr(os, "symlink") and os.name == "posix", "需要 symlink")
    def test_does_not_follow_planted_symlink(self):
        target = os.path.join(self.d, "target.txt")
        pathlib.Path(target).write_text("secret")
        os.symlink(target, os.path.join(self.d, "link.txt"))
        p, fh = unique_path(self.d, "link.txt")
        fh.write(b"x")
        fh.close()
        self.assertEqual(pathlib.Path(target).read_text(), "secret")
        self.assertEqual(os.path.basename(p), "link (1).txt")


if __name__ == "__main__":
    unittest.main()
