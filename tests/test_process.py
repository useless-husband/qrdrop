"""把 CLI 當成真的子程序跑一遍：啟動、上傳／下載、--once、--timeout、Ctrl+C。"""
import http.client
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit

from tests.test_server import multipart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def start(*args):
    env = dict(os.environ, PYTHONPATH=ROOT, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "qrdrop", *args, "--bind", "127.0.0.1", "--plain"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True, encoding="utf-8")
    killer = threading.Timer(30, p.kill)
    killer.start()
    url = None
    lines = []
    for line in p.stdout:
        lines.append(line)
        if line.startswith("網址："):
            url = line.split("：", 1)[1].strip()
            break
    if url is None:
        killer.cancel()
        raise AssertionError("沒有取得網址：" + "".join(lines))
    return p, url, killer


def finish(p, killer):
    out = p.stdout.read()
    code = p.wait(timeout=15)
    p.stdout.close()
    killer.cancel()
    return code, out


class TestProcess(unittest.TestCase):
    def test_receive_once_exits_after_upload(self):
        with tempfile.TemporaryDirectory() as d:
            p, url, killer = start("receive", "--dir", d, "--once", "--timeout", "0")
            u = urlsplit(url)
            body, ctype = multipart([("hello.txt", b"hi there")])
            c = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
            c.request("POST", u.path + "upload", body, {"Content-Type": ctype})
            self.assertEqual(c.getresponse().status, 200)
            c.close()
            code, out = finish(p, killer)
            self.assertEqual(code, 0)
            self.assertIn("已完成一次傳輸", out)
            with open(os.path.join(d, "hello.txt"), "rb") as fh:
                self.assertEqual(fh.read(), b"hi there")

    def test_send_timeout_closes_server(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.txt")
            with open(f, "w") as fh:
                fh.write("x")
            t0 = time.time()
            p, url, killer = start("send", f, "--timeout", "1s")
            code, out = finish(p, killer)
            self.assertEqual(code, 0)
            self.assertIn("逾時", out)
            self.assertLess(time.time() - t0, 15)

    @unittest.skipUnless(os.name == "posix", "需要 POSIX 訊號")
    def test_ctrl_c_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            p, url, killer = start("receive", "--dir", d)
            u = urlsplit(url)
            c = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
            c.request("GET", u.path)
            self.assertEqual(c.getresponse().status, 200)
            c.close()
            p.send_signal(signal.SIGINT)
            code, out = finish(p, killer)
            self.assertEqual(code, 0)
            self.assertIn("已中止", out)

    def test_send_prints_qr_url_and_warning(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.txt")
            with open(f, "w") as fh:
                fh.write("x")
            p, url, killer = start("send", f, "--once", "--pin", "1234")
            self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/[A-Za-z0-9_-]{16}/$")
            u = urlsplit(url)
            c = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
            c.request("GET", u.path + "f/0/a.txt?pin=1234")
            self.assertEqual(c.getresponse().read(), b"x")
            c.close()
            code, out = finish(p, killer)
            self.assertIn("沒有加密", out)
            self.assertIn("PIN：1234", out)
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
