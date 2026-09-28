import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
import zipfile

from qrdrop import server as S


def multipart(files, boundary="----qrdropBoundary42", fields=()):
    body = b""
    for name, value in fields:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    for filename, data in files:
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
                 "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8") + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class ServerCase(unittest.TestCase):
    def start(self, session):
        self.logs = []
        session.log_cb = lambda ip, action, detail="": self.logs.append((ip, action, detail))
        self.session = session
        self.srv = S.make_server(session, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(5)

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, dict((k.lower(), v) for k, v in r.getheaders()), r.read()
        finally:
            c.close()

    @property
    def base(self):
        return f"/{self.session.token}/"


class TestSend(ServerCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = self.tmp.name
        self.a = os.path.join(d, "a.txt")
        self.b = os.path.join(d, "照片 b.bin")
        with open(self.a, "wb") as fh:
            fh.write(b"alpha" * 1000)
        with open(self.b, "wb") as fh:
            fh.write(bytes(range(256)) * 50)
        os.makedirs(os.path.join(d, "dir", "sub"))
        with open(os.path.join(d, "dir", "x.txt"), "wb") as fh:
            fh.write(b"x")
        with open(os.path.join(d, "dir", "sub", "y.txt"), "wb") as fh:
            fh.write(b"y")

    def test_single_file_page_and_download(self):
        self.start(S.Session("send", S.collect_files([self.a])))
        st, h, body = self.req("GET", self.base)
        self.assertEqual(st, 200)
        self.assertIn("text/html", h["content-type"])
        text = body.decode()
        self.assertIn("a.txt", text)
        self.assertIn("f/0/a.txt", text)
        self.assertIn("4.9 KB", text)
        self.assertNotIn("all.zip", text)
        st, h, body = self.req("GET", self.base + "f/0/a.txt")
        self.assertEqual(st, 200)
        self.assertEqual(body, b"alpha" * 1000)
        self.assertEqual(h["content-length"], "5000")
        self.assertIn("attachment", h["content-disposition"])
        self.assertIn("filename=\"a.txt\"", h["content-disposition"])

    def test_security_headers(self):
        self.start(S.Session("send", S.collect_files([self.a])))
        st, h, _ = self.req("GET", self.base)
        self.assertEqual(h["cache-control"], "no-store")
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertIn("default-src 'none'", h["content-security-policy"])
        self.assertNotIn("Python", h.get("server", ""))

    def test_unicode_filename_download(self):
        self.start(S.Session("send", S.collect_files([self.a, self.b])))
        st, h, body = self.req("GET", self.base + "f/1/" + "%E7%85%A7%E7%89%87%20b.bin")
        self.assertEqual(st, 200)
        self.assertEqual(len(body), 256 * 50)
        self.assertIn("filename*=UTF-8''%E7%85%A7%E7%89%87%20b.bin", h["content-disposition"])

    def test_multi_file_zip(self):
        self.start(S.Session("send", S.collect_files([self.a, self.b, os.path.join(self.tmp.name, "dir")])))
        st, _, page = self.req("GET", self.base)
        self.assertIn("all.zip", page.decode())
        st, h, body = self.req("GET", self.base + "all.zip")
        self.assertEqual(st, 200)
        self.assertEqual(h["content-type"], "application/zip")
        self.assertNotIn("content-length", h)  # 串流：事先不知道大小
        zf = zipfile.ZipFile(io.BytesIO(body))
        self.assertIsNone(zf.testzip())
        self.assertEqual(sorted(zf.namelist()), sorted(["a.txt", "照片 b.bin", "dir/x.txt", "dir/sub/y.txt"]))
        self.assertEqual(zf.read("dir/sub/y.txt"), b"y")

    def test_zip_not_available_for_single_file(self):
        self.start(S.Session("send", S.collect_files([self.a])))
        self.assertEqual(self.req("GET", self.base + "all.zip")[0], 404)

    def test_wrong_token_is_404_everywhere(self):
        self.start(S.Session("send", S.collect_files([self.a])))
        for path in ("/", "/wrong/", "/wrong/f/0/a.txt", "/" + self.session.token[:-1] + "/", "/" + self.session.token + "x/",
                     "/f/0/a.txt", "/all.zip", "/favicon.ico", "/?token=" + self.session.token):
            st, _, body = self.req("GET", path)
            self.assertEqual(st, 404, path)
            self.assertEqual(body, b"Not Found")
        self.assertEqual(self.req("POST", "/wrong/upload", b"x")[0], 404)
        self.assertEqual(self.session.downloads, 0)

    def test_traversal_attempts_blocked(self):
        self.start(S.Session("send", S.collect_files([self.a])))
        for path in ("f/0/../../etc/passwd", "f/0/%2e%2e%2f%2e%2e%2fetc%2fpasswd", "f/../../../etc/passwd", "f/-1/a.txt", "f/9/a.txt",
                     "f/0/b.txt", "f/0/", "f/x/a.txt", "..%2f..%2f", "f/0/a.txt/extra"):
            st, _, _ = self.req("GET", self.base + path)
            self.assertEqual(st, 404, path)
        st, _, _ = self.req("GET", "/" + self.session.token + "/../" + self.session.token + "/f/0/../../x")
        self.assertEqual(st, 404)

    def test_method_restrictions(self):
        self.start(S.Session("send", S.collect_files([self.a])))
        self.assertEqual(self.req("POST", self.base + "upload", b"x")[0], 404)
        self.assertEqual(self.req("DELETE", self.base)[0], 501)

    def test_token_redirect_without_slash(self):
        self.start(S.Session("send", S.collect_files([self.a])))
        st, h, _ = self.req("GET", "/" + self.session.token)
        self.assertEqual(st, 302)
        self.assertEqual(h["location"], self.base)

    def test_range_request(self):
        self.start(S.Session("send", S.collect_files([self.a])))
        st, h, body = self.req("GET", self.base + "f/0/a.txt", headers={"Range": "bytes=5-9"})
        self.assertEqual(st, 206)
        self.assertEqual(body, b"alpha")
        self.assertEqual(h["content-range"], "bytes 5-9/5000")
        st, _, body = self.req("GET", self.base + "f/0/a.txt", headers={"Range": "bytes=-5"})
        self.assertEqual(body, b"alpha")
        st, h, _ = self.req("GET", self.base + "f/0/a.txt", headers={"Range": "bytes=99999-"})
        self.assertEqual(st, 416)

    def test_head_request(self):
        self.start(S.Session("send", S.collect_files([self.a])))
        st, h, body = self.req("HEAD", self.base + "f/0/a.txt")
        self.assertEqual((st, body), (200, b""))
        self.assertEqual(h["content-length"], "5000")

    def test_once_shuts_down_after_complete_download(self):
        self.start(S.Session("send", S.collect_files([self.a]), once=True))
        self.assertEqual(self.req("GET", self.base)[0], 200)  # 只開頁面不算
        time.sleep(0.2)
        self.assertIsNone(self.srv.stop_reason)
        self.assertEqual(self.req("GET", self.base + "f/0/a.txt")[0], 200)
        for _ in range(100):
            if self.srv.stop_reason:
                break
            time.sleep(0.05)
        self.assertEqual(self.srv.stop_reason, "once")
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.session.downloads, 1)

    def test_once_with_zip(self):
        self.start(S.Session("send", S.collect_files([self.a, self.b]), once=True))
        self.assertEqual(self.req("GET", self.base + "all.zip")[0], 200)
        for _ in range(100):
            if self.srv.stop_reason:
                break
            time.sleep(0.05)
        self.assertEqual(self.srv.stop_reason, "once")

    def test_aborted_download_does_not_count(self):
        big = os.path.join(self.tmp.name, "big.bin")
        with open(big, "wb") as fh:
            fh.write(b"z" * 20_000_000)
        self.start(S.Session("send", S.collect_files([big]), once=True))
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", self.base + "f/0/big.bin")
        r = c.getresponse()
        r.read(1000)
        c.close()  # 中途斷線
        time.sleep(0.5)
        self.assertEqual(self.session.downloads, 0)
        self.assertIsNone(self.srv.stop_reason)

    def test_requests_are_logged(self):
        self.start(S.Session("send", S.collect_files([self.a])))
        self.req("GET", self.base)
        self.req("GET", self.base + "f/0/a.txt")
        self.req("GET", "/nope")
        actions = [a for _, a, _ in self.logs]
        self.assertIn("開啟下載頁", actions)
        self.assertIn("下載", actions)
        self.assertIn("完成", actions)
        self.assertIn("拒絕", actions)
        self.assertTrue(all(ip == "127.0.0.1" for ip, _, _ in self.logs))

    def test_log_sanitizes_hostile_path(self):
        self.start(S.Session("send", S.collect_files([self.a])))
        self.req("GET", "/%1b%5b2J" + "A" * 500)
        detail = [d for _, a, d in self.logs if a == "拒絕"][0]
        self.assertNotIn("\x1b", detail)
        self.assertLess(len(detail), 200)

    def test_html_escapes_filenames(self):
        evil = os.path.join(self.tmp.name, "<img src=x onerror=alert(1)>.txt")
        with open(evil, "w") as fh:
            fh.write("x")
        self.start(S.Session("send", S.collect_files([evil])))
        text = self.req("GET", self.base)[2].decode()
        self.assertNotIn("<img src=x", text)
        self.assertIn("&lt;img src=x", text)


class TestCollect(unittest.TestCase):
    def test_folder_is_walked_and_named(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "top", "a"))
            open(os.path.join(d, "top", "a", "1.txt"), "w").close()
            open(os.path.join(d, "top", "2.txt"), "w").close()
            files = S.collect_files([os.path.join(d, "top")])
            self.assertEqual(sorted(f.arcname for f in files), ["top/2.txt", "top/a/1.txt"])

    def test_duplicate_names_disambiguated(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "x"))
            os.makedirs(os.path.join(d, "y"))
            for sub in "xy":
                open(os.path.join(d, sub, "same.txt"), "w").close()
            files = S.collect_files([os.path.join(d, "x", "same.txt"), os.path.join(d, "y", "same.txt")])
            self.assertEqual(len({f.arcname for f in files}), 2)

    def test_errors(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                S.collect_files([os.path.join(d, "nope")])
            os.makedirs(os.path.join(d, "empty"))
            with self.assertRaises(ValueError):
                S.collect_files([os.path.join(d, "empty")])


class TestReceive(ServerCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = os.path.realpath(self.tmp.name)

    def start_recv(self, **kw):
        self.start(S.Session("receive", dest=self.dest, **kw))

    def upload(self, files, path=None, **kw):
        body, ctype = multipart(files, **kw)
        return self.req("POST", path or self.base + "upload", body, {"Content-Type": ctype, "Content-Length": str(len(body))})

    def listing(self):
        return sorted(os.listdir(self.dest))

    def test_upload_page(self):
        self.start_recv(max_size=5 * 1024 ** 2)
        st, h, body = self.req("GET", self.base)
        self.assertEqual(st, 200)
        text = body.decode()
        self.assertIn('type="file"', text)
        self.assertIn("5.0 MB", text)
        self.assertIn("multiple", text)

    def test_upload_multiple_files(self):
        self.start_recv()
        st, h, body = self.upload([("a.txt", b"AAA"), ("b.jpg", b"\xff\xd8" * 1000), ("中文.txt", "你好".encode())])
        self.assertEqual(st, 200)
        r = json.loads(body)
        self.assertTrue(r["ok"])
        self.assertEqual([s["name"] for s in r["saved"]], ["a.txt", "b.jpg", "中文.txt"])
        self.assertEqual(self.listing(), ["a.txt", "b.jpg", "中文.txt"])
        with open(os.path.join(self.dest, "b.jpg"), "rb") as fh:
            self.assertEqual(fh.read(), b"\xff\xd8" * 1000)
        self.assertEqual(self.session.uploads, ["a.txt", "b.jpg", "中文.txt"])

    def test_same_name_renamed(self):
        self.start_recv()
        for _ in range(3):
            self.assertEqual(self.upload([("photo.jpg", b"x")])[0], 200)
        self.assertEqual(self.listing(), ["photo (1).jpg", "photo (2).jpg", "photo.jpg"])

    def test_existing_file_not_overwritten(self):
        with open(os.path.join(self.dest, "keep.txt"), "wb") as fh:
            fh.write(b"mine")
        self.start_recv()
        self.upload([("keep.txt", b"theirs")])
        with open(os.path.join(self.dest, "keep.txt"), "rb") as fh:
            self.assertEqual(fh.read(), b"mine")
        self.assertEqual(self.listing(), ["keep (1).txt", "keep.txt"])

    def test_evil_filenames_stay_inside_dest(self):
        self.start_recv()
        evil = ["../../evil1.txt", "..\\..\\evil2.txt", "/tmp/qrdrop-abs-evil.txt", "C:\\evil3.txt", "a/../../evil4", "CON", "nul.txt",
                "x\x00.txt", "....//evil5", ".hidden", "trailing.dot.", "a:b"]
        for name in evil:
            st, _, body = self.upload([(name, b"pwn")])
            self.assertEqual(st, 200, name)
        parent = os.path.dirname(self.dest)
        for bad in ("evil1.txt", "evil2.txt", "evil4", "evil5"):
            self.assertFalse(os.path.exists(os.path.join(parent, bad)), bad)
        self.assertFalse(os.path.exists("/tmp/qrdrop-abs-evil.txt"))
        for f in os.listdir(self.dest):
            self.assertNotIn("/", f)
            self.assertFalse(f.startswith("."))
        self.assertEqual(len(self.listing()), len(evil))
        self.assertIn("_CON", self.listing())

    def test_bad_token_rejected_and_nothing_written(self):
        self.start_recv()
        st, _, _ = self.upload([("a.txt", b"x")], path="/wrongtoken/upload")
        self.assertEqual(st, 404)
        st, _, _ = self.upload([("a.txt", b"x")], path="/upload")
        self.assertEqual(st, 404)
        self.assertEqual(self.listing(), [])

    def test_max_size_enforced_and_partial_removed(self):
        self.start_recv(max_size=1000)
        st, _, body = self.upload([("ok.bin", b"a" * 1000), ("big.bin", b"b" * 1001)])
        self.assertEqual(st, 413)
        self.assertFalse(json.loads(body)["ok"])
        self.assertEqual(self.listing(), ["ok.bin"])  # 超過的那個被刪掉

    def test_exactly_max_size_ok(self):
        self.start_recv(max_size=1000)
        self.assertEqual(self.upload([("ok.bin", b"a" * 1000)])[0], 200)

    def test_missing_content_length(self):
        self.start_recv()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest("POST", self.base + "upload")
        c.putheader("Content-Type", "multipart/form-data; boundary=x")
        c.endheaders()
        r = c.getresponse()
        self.assertEqual(r.status, 411)
        c.close()

    def test_wrong_content_type(self):
        self.start_recv()
        st, _, _ = self.req("POST", self.base + "upload", b"hello", {"Content-Type": "text/plain"})
        self.assertEqual(st, 400)

    def test_no_files_in_request(self):
        self.start_recv()
        st, _, _ = self.upload([], fields=[("x", "y")])
        self.assertEqual(st, 400)
        self.assertEqual(self.listing(), [])

    def test_truncated_upload_cleans_up(self):
        self.start_recv()
        body, ctype = multipart([("half.bin", b"q" * 5000)])
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest("POST", self.base + "upload")
        c.putheader("Content-Type", ctype)
        c.putheader("Content-Length", str(len(body)))
        c.endheaders()
        c.send(body[:2000])
        c.close()
        time.sleep(0.5)
        self.assertEqual(self.listing(), [])

    def test_insufficient_disk_space(self):
        self.start_recv()
        real = S.shutil.disk_usage
        S.shutil.disk_usage = lambda p: type("U", (), {"free": 1000})()
        try:
            st, _, body = self.upload([("a.txt", b"x" * 10)])
        finally:
            S.shutil.disk_usage = real
        self.assertEqual(st, 507)
        self.assertIn("剩餘空間", json.loads(body)["error"])
        self.assertEqual(self.listing(), [])

    def test_html_result_for_plain_form_post(self):
        self.start_recv()
        body, ctype = multipart([("a.txt", b"x")])
        st, h, out = self.req("POST", self.base + "upload", body, {"Content-Type": ctype, "Accept": "text/html,application/xhtml+xml"})
        self.assertEqual(st, 200)
        self.assertIn("text/html", h["content-type"])
        self.assertIn("上傳完成", out.decode())

    def test_once_after_upload(self):
        self.start_recv(once=True)
        self.assertEqual(self.upload([("a.txt", b"x")])[0], 200)
        for _ in range(100):
            if self.srv.stop_reason:
                break
            time.sleep(0.05)
        self.assertEqual(self.srv.stop_reason, "once")
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def test_failed_upload_does_not_trigger_once(self):
        self.start_recv(once=True, max_size=10)
        self.assertEqual(self.upload([("a.txt", b"x" * 100)])[0], 413)
        time.sleep(0.3)
        self.assertIsNone(self.srv.stop_reason)

    def test_get_upload_endpoint_not_allowed(self):
        self.start_recv()
        self.assertEqual(self.req("GET", self.base + "upload")[0], 404)

    def test_uploads_logged(self):
        self.start_recv()
        self.upload([("a.txt", b"x")])
        self.assertIn(("127.0.0.1", "收到", "a.txt (1 B)"), self.logs)


class TestPin(ServerCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = os.path.join(self.tmp.name, "s.txt")
        with open(self.f, "w") as fh:
            fh.write("secret data")
        self.start(S.Session("send", S.collect_files([self.f]), pin="4821"))

    def test_pin_page_shown(self):
        st, _, body = self.req("GET", self.base)
        self.assertEqual(st, 401)
        self.assertIn("輸入 PIN", body.decode())

    def test_download_needs_pin(self):
        st, _, body = self.req("GET", self.base + "f/0/s.txt")
        self.assertEqual(st, 401)
        self.assertNotIn(b"secret", body)

    def test_query_pin(self):
        self.assertEqual(self.req("GET", self.base + "f/0/s.txt?pin=4821")[2], b"secret data")
        self.assertEqual(self.req("GET", self.base + "f/0/s.txt?pin=0000")[0], 403)

    def test_header_pin(self):
        self.assertEqual(self.req("GET", self.base + "f/0/s.txt", headers={"X-Qrdrop-Pin": "4821"})[0], 200)

    def test_form_login_sets_cookie_and_grants_access(self):
        st, h, _ = self.req("POST", self.base + "pin", b"pin=4821", {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(st, 303)
        cookie = h["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        c = cookie.split(";")[0]
        st, _, body = self.req("GET", self.base, headers={"Cookie": c})
        self.assertEqual(st, 200)
        self.assertEqual(self.req("GET", self.base + "f/0/s.txt", headers={"Cookie": c})[2], b"secret data")
        self.assertEqual(self.req("GET", self.base, headers={"Cookie": "qrdrop_auth=forged"})[0], 401)

    def test_wrong_pin_form(self):
        st, h, body = self.req("POST", self.base + "pin", b"pin=1111", {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(st, 403)
        self.assertNotIn("set-cookie", h)
        self.assertIn("PIN 不對", body.decode())

    def test_lockout_after_repeated_failures(self):
        for _ in range(S.PIN_MAX_FAILURES):
            self.assertEqual(self.req("GET", self.base + "?pin=0000")[0], 403)
        st, h, _ = self.req("GET", self.base + "?pin=4821")  # 即使對了也先鎖住
        self.assertEqual(st, 429)
        self.assertIn("retry-after", h)

    def test_token_still_checked_first(self):
        self.assertEqual(self.req("GET", "/bad/?pin=4821")[0], 404)

    def test_pin_protects_upload(self):
        d = os.path.join(self.tmp.name, "up")
        os.makedirs(d)
        self.srv.session.mode = "receive"
        self.srv.session.dest = d
        body, ctype = multipart([("a.txt", b"x")])
        st, _, _ = self.req("POST", self.base + "upload", body, {"Content-Type": ctype})
        self.assertEqual(st, 401)
        self.assertEqual(os.listdir(d), [])
        st, _, _ = self.req("POST", self.base + "upload?pin=4821", body, {"Content-Type": ctype})
        self.assertEqual(st, 200)


class TestTimeoutViaRequestStop(ServerCase):
    def test_request_stop_is_idempotent_and_stops_loop(self):
        self.start(S.Session("receive", dest=tempfile.gettempdir()))
        self.srv.request_stop("timeout")
        self.srv.request_stop("once")
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.srv.stop_reason, "timeout")


if __name__ == "__main__":
    unittest.main()
