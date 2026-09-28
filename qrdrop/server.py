"""qrdrop 的 HTTP 伺服器：send（下載）與 receive（上傳）兩種模式。"""

from __future__ import annotations

import hashlib
import hmac
import http.cookies
import json
import mimetypes
import os
import re
import secrets
import shutil
import socketserver
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, List, Optional, Sequence
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import pages
from .multipart import MultipartError, MultipartParser, parse_boundary
from .names import unique_path
from .util import human_size
from .zipstream import stream_zip

DEFAULT_MAX_SIZE = 2 * 1024 ** 3
MIN_FREE_RESERVE = 64 * 1024 * 1024  # 上傳完後至少還要剩這麼多空間
MAX_FILES_PER_REQUEST = 1000
PIN_MAX_FAILURES = 5
PIN_LOCK_SECONDS = 60
READ_CHUNK = 128 * 1024
COOKIE_NAME = "qrdrop_auth"


@dataclass
class SharedFile:
    arcname: str  # 對外顯示與 zip 內的路徑（用 / 分隔）
    path: str
    size: int


def collect_files(paths: Sequence[str]) -> List[SharedFile]:
    """把使用者給的檔案／資料夾展開成檔案清單。資料夾會保留最上層資料夾名稱。"""
    out: List[SharedFile] = []
    seen = set()

    def add(arc: str, path: str) -> None:
        base, n = arc, 1
        while arc in seen:  # 兩個來源同名時避免 zip 內撞名
            stem, ext = os.path.splitext(base)
            arc = f"{stem} ({n}){ext}"
            n += 1
        seen.add(arc)
        out.append(SharedFile(arc, path, os.stat(path).st_size))

    for p in paths:
        if not os.path.exists(p):
            raise FileNotFoundError(f"找不到：{p}")
        p = os.path.abspath(p)
        if os.path.isdir(p):
            top = os.path.basename(p.rstrip(os.sep)) or "folder"
            for root, dirs, names in os.walk(p):
                dirs.sort()
                for name in sorted(names):
                    full = os.path.join(root, name)
                    if os.path.isfile(full):
                        rel = os.path.relpath(full, p).replace(os.sep, "/")
                        add(f"{top}/{rel}", full)
        elif os.path.isfile(p):
            add(os.path.basename(p), p)
        else:
            raise ValueError(f"不是一般檔案或資料夾：{p}")
    if not out:
        raise ValueError("沒有可以分享的檔案（資料夾是空的？）")
    return out


class UploadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Session:
    """一次分享的設定與狀態。"""

    def __init__(
        self,
        mode: str,
        files: Optional[List[SharedFile]] = None,
        dest: Optional[str] = None,
        max_size: int = DEFAULT_MAX_SIZE,
        pin: Optional[str] = None,
        once: bool = False,
        token: Optional[str] = None,
        log: Optional[Callable[[str, str, str], None]] = None,
        zip_name: str = "qrdrop.zip",
    ):
        if mode not in ("send", "receive"):
            raise ValueError("mode 必須是 send 或 receive")
        self.mode = mode
        self.files = files or []
        self.dest = dest
        self.max_size = max_size
        self.pin = pin
        self.once = once
        self.token = token or secrets.token_urlsafe(12)
        self.log_cb = log
        self.zip_name = zip_name
        self._secret = secrets.token_bytes(16)
        self._lock = threading.Lock()
        self._fails = {}  # ip -> (次數, 鎖定到何時)
        self.downloads = 0
        self.uploads: List[str] = []

    # --- PIN
    def auth_value(self) -> str:
        return hmac.new(self._secret, (self.pin or "").encode(), hashlib.sha256).hexdigest()

    def pin_ok(self, candidate: Optional[str]) -> bool:
        return bool(self.pin) and candidate is not None and hmac.compare_digest(candidate.encode(), self.pin.encode())

    def locked(self, ip: str) -> bool:
        with self._lock:
            c, until = self._fails.get(ip, (0, 0.0))
            return c >= PIN_MAX_FAILURES and time.time() < until

    def record_fail(self, ip: str) -> None:
        with self._lock:
            c, until = self._fails.get(ip, (0, 0.0))
            if c >= PIN_MAX_FAILURES and time.time() >= until:
                c = 0
            c += 1
            self._fails[ip] = (c, time.time() + PIN_LOCK_SECONDS)

    def clear_fail(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)

    def log(self, ip: str, action: str, detail: str = "") -> None:
        if self.log_cb:
            self.log_cb(ip, action, detail)


def _clean_for_log(s: str, limit: int = 80) -> str:
    s = "".join(c if c.isprintable() else "?" for c in s)
    return s if len(s) <= limit else s[:limit] + "…"


class QrdropServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = False

    def __init__(self, addr, session: Session):
        self.session = session
        self.stop_reason: Optional[str] = None
        self._stop_lock = threading.Lock()
        super().__init__(addr, Handler)

    def server_bind(self) -> None:
        # HTTPServer.server_bind 會呼叫 socket.getfqdn() 反查主機名稱，在部分 macOS 環境
        # （例如 GitHub 的 macOS runner）會卡住數十秒；我們用不到 server_name，直接記下位址。
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

    def request_stop(self, reason: str) -> None:
        with self._stop_lock:
            if self.stop_reason is not None:
                return
            self.stop_reason = reason
        threading.Thread(target=self.shutdown, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server: QrdropServer
    protocol_version = "HTTP/1.0"  # 每個請求一條連線，結束就關，簡單可靠
    server_version = "qrdrop"
    sys_version = ""
    timeout = 60

    def log_message(self, format, *args):  # noqa: A002 - 由我們自己記錄
        pass

    # --- 基本回應
    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")

    def _send_bytes(self, status: int, body: bytes, ctype: str, extra: Sequence[tuple] = (), head: bool = False, csp: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        if csp:
            self.send_header("Content-Security-Policy", csp)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _send_html(self, status: int, make: Callable[[str], str], extra: Sequence[tuple] = (), head: bool = False) -> None:
        nonce = secrets.token_urlsafe(9)
        csp = (f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
               "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self._send_bytes(status, make(nonce).encode("utf-8"), "text/html; charset=utf-8", extra, head, csp)

    def _send_json(self, status: int, obj: dict) -> None:
        self._send_bytes(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _plain(self, status: int, text: str, head: bool = False) -> None:
        self._send_bytes(status, text.encode("utf-8"), "text/plain; charset=utf-8", head=head)

    def _not_found(self, head: bool = False) -> None:
        self.server.session.log(self.client_address[0], "拒絕", f"404 {self.command} {_clean_for_log(self.path)}")
        self._plain(404, "Not Found", head)

    # --- 入口
    def do_GET(self):
        self._dispatch(False)

    def do_HEAD(self):
        self._dispatch(True)

    def do_POST(self):
        self._dispatch(False)

    def _dispatch(self, head: bool) -> None:
        sess = self.server.session
        ip = self.client_address[0]
        try:
            parts = urlsplit(self.path)
            segs = parts.path.split("/")
            if len(segs) < 2 or segs[0] != "" or not hmac.compare_digest(unquote(segs[1]).encode(), sess.token.encode()):
                return self._not_found(head)
            rest = [unquote(s) for s in segs[2:]]
            if not rest:  # /token -> /token/
                self.send_response(302)
                self.send_header("Location", f"/{quote(sess.token)}/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if sess.pin and not self._authorized(parts, rest, head):
                return
            method = self.command
            if sess.mode == "send":
                if method in ("GET", "HEAD") and rest == [""]:
                    return self._send_index(head)
                if method in ("GET", "HEAD") and rest == ["all.zip"] and len(sess.files) > 1:
                    return self._send_zip(head)
                if method in ("GET", "HEAD") and len(rest) == 3 and rest[0] == "f" and rest[1].isdigit():
                    return self._send_file(int(rest[1]), rest[2], head)
            else:
                if method in ("GET", "HEAD") and rest == [""]:
                    sess.log(ip, "開啟上傳頁")
                    return self._send_html(200, lambda n: pages.upload_page(sess.max_size, n), head=head)
                if method == "POST" and rest == ["upload"]:
                    return self._receive_upload()
            if rest == ["pin"]:
                return self._plain(405, "Method Not Allowed")
            return self._not_found(head)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            sess.log(ip, "連線中斷")
        except Exception as exc:  # 不讓例外洩漏細節給對方
            sess.log(ip, "錯誤", f"{type(exc).__name__}: {_clean_for_log(str(exc))}")
            try:
                self._plain(500, "Internal Server Error")
            except OSError:
                pass

    # --- PIN
    def _authorized(self, parts, rest, head: bool) -> bool:
        sess = self.server.session
        ip = self.client_address[0]
        if sess.locked(ip):
            self.send_response(429)
            self.send_header("Retry-After", str(PIN_LOCK_SECONDS))
            self.send_header("Content-Length", "0")
            self.end_headers()
            sess.log(ip, "拒絕", "PIN 錯誤次數過多，暫時鎖定")
            return False
        if rest == ["pin"] and self.command == "POST":
            n = min(int(self.headers.get("Content-Length") or 0), 1024)
            form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
            if sess.pin_ok((form.get("pin") or [None])[0]):
                sess.clear_fail(ip)
                sess.log(ip, "PIN 正確")
                cookie = f"{COOKIE_NAME}={sess.auth_value()}; Path=/{quote(sess.token)}/; HttpOnly; SameSite=Strict"
                self.send_response(303)
                self.send_header("Set-Cookie", cookie)
                self.send_header("Location", f"/{quote(sess.token)}/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                sess.record_fail(ip)
                sess.log(ip, "拒絕", "PIN 錯誤")
                self._send_html(403, lambda nonce: pages.pin_page(nonce, error=True))
            return False
        # 已有有效 cookie？
        raw = self.headers.get("Cookie")
        if raw:
            try:
                jar = http.cookies.SimpleCookie(raw)
                if COOKIE_NAME in jar and hmac.compare_digest(jar[COOKIE_NAME].value.encode(), sess.auth_value().encode()):
                    return True
            except http.cookies.CookieError:
                pass
        # curl 等工具：?pin=1234 或 X-Qrdrop-Pin 標頭
        cand = (parse_qs(parts.query).get("pin") or [None])[0] or self.headers.get("X-Qrdrop-Pin")
        if cand is not None:
            if sess.pin_ok(cand):
                return True
            sess.record_fail(ip)
            sess.log(ip, "拒絕", "PIN 錯誤")
            self._plain(403, "Forbidden", head)
            return False
        if self.command in ("GET", "HEAD") and rest == [""]:
            sess.log(ip, "要求輸入 PIN")
            self._send_html(401, lambda nonce: pages.pin_page(nonce), head=head)
        else:
            self._plain(401, "PIN required", head)
        return False

    # --- send
    def _send_index(self, head: bool) -> None:
        sess = self.server.session
        sess.log(self.client_address[0], "開啟下載頁")
        items = [(f.arcname, f.size, pages.href_for(i, f.arcname)) for i, f in enumerate(sess.files)]
        self._send_html(200, lambda n: pages.download_page(items, sess.zip_name, n), head=head)

    @staticmethod
    def _disposition(name: str) -> str:
        ascii_name = re.sub(r'[^\x20-\x7e]|["\\%;]', "_", name) or "download"
        return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"

    def _send_file(self, index: int, name: str, head: bool) -> None:
        sess = self.server.session
        ip = self.client_address[0]
        if index >= len(sess.files) or name != sess.files[index].arcname:
            return self._not_found(head)
        f = sess.files[index]
        try:
            fh = open(f.path, "rb")
        except OSError:
            sess.log(ip, "錯誤", f"讀不到 {f.arcname}")
            return self._plain(404, "Not Found", head)
        with fh:
            size = os.fstat(fh.fileno()).st_size
            start, end, status = 0, size - 1, 200
            rng = self.headers.get("Range")
            if rng:
                m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
                if m and (m.group(1) or m.group(2)):
                    if m.group(1):
                        start = int(m.group(1))
                        end = int(m.group(2)) if m.group(2) else size - 1
                    else:
                        start = max(size - int(m.group(2)), 0)
                    end = min(end, size - 1)
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    status = 206
            length = end - start + 1 if size else 0
            ctype = mimetypes.guess_type(f.arcname)[0] or "application/octet-stream"
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Disposition", self._disposition(os.path.basename(f.arcname)))
            self._security_headers()
            self.end_headers()
            if head:
                return
            sess.log(ip, "下載", f"{f.arcname} ({human_size(length)})")
            fh.seek(start)
            left = length
            try:
                while left > 0:
                    chunk = fh.read(min(READ_CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                sess.log(ip, "下載中斷", f.arcname)
                return
        if left == 0 and status == 200:
            self._download_done(f.arcname)

    def _send_zip(self, head: bool) -> None:
        sess = self.server.session
        ip = self.client_address[0]
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", self._disposition(sess.zip_name))
        self._security_headers()
        self.end_headers()
        if head:
            return
        sess.log(ip, "下載", f"{sess.zip_name}（{len(sess.files)} 個檔案，打包中）")
        try:
            for chunk in stream_zip([(f.arcname, f.path) for f in sess.files]):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            sess.log(ip, "下載中斷", sess.zip_name)
            return
        self._download_done(sess.zip_name)

    def _download_done(self, what: str) -> None:
        sess = self.server.session
        with sess._lock:
            sess.downloads += 1
        sess.log(self.client_address[0], "完成", what)
        if sess.once:
            self.server.request_stop("once")

    # --- receive
    def _receive_upload(self) -> None:
        sess = self.server.session
        ip = self.client_address[0]
        state = {"cur": None, "saved": [], "files": 0, "left": 0}
        try:
            clen_raw = self.headers.get("Content-Length")
            if clen_raw is None:
                raise UploadError(411, "缺少 Content-Length")
            if not clen_raw.isdigit():
                raise UploadError(400, "Content-Length 不正確")
            clen = int(clen_raw)
            state["left"] = clen
            try:
                boundary = parse_boundary(self.headers.get("Content-Type"))
            except MultipartError as e:
                raise UploadError(400, str(e))
            free = shutil.disk_usage(sess.dest).free
            if clen + MIN_FREE_RESERVE > free:
                raise UploadError(507, f"電腦的剩餘空間不夠（剩 {human_size(free)}，要傳 {human_size(clen)}）")
            if (self.headers.get("Expect") or "").lower() == "100-continue":
                self.send_response_only(100)  # curl 送大檔時會等這個，不回會白等 1 秒
                self.end_headers()

            def on_part(h):
                if not h.get("filename"):
                    state["cur"] = None
                    return
                state["files"] += 1
                if state["files"] > MAX_FILES_PER_REQUEST:
                    raise UploadError(413, f"一次最多 {MAX_FILES_PER_REQUEST} 個檔案")
                path, fh = unique_path(sess.dest, h["filename"])
                state["cur"] = {"path": path, "fh": fh, "size": 0}

            def on_data(b):
                cur = state["cur"]
                if cur is None:
                    return
                cur["size"] += len(b)
                if cur["size"] > sess.max_size:
                    raise UploadError(413, f"檔案超過上限 {human_size(sess.max_size)}")
                cur["fh"].write(b)

            def on_end():
                cur = state["cur"]
                if cur is None:
                    return
                cur["fh"].close()
                state["cur"] = None
                name = os.path.basename(cur["path"])
                state["saved"].append({"name": name, "size": cur["size"]})
                sess.log(ip, "收到", f"{name} ({human_size(cur['size'])})")

            parser = MultipartParser(boundary, on_part, on_data, on_end)
            state["left"] = clen
            try:
                while state["left"] > 0:
                    chunk = self.rfile.read(min(READ_CHUNK, state["left"]))
                    if not chunk:
                        break
                    state["left"] -= len(chunk)
                    parser.feed(chunk)
                parser.close()
            except MultipartError as e:
                raise UploadError(400, f"上傳內容格式錯誤：{e}")
            except OSError as e:
                if isinstance(e, (BrokenPipeError, ConnectionResetError, TimeoutError)):
                    raise
                raise UploadError(507 if getattr(e, "errno", None) == 28 else 500, f"寫入失敗：{e.strerror or e}")
            if not state["saved"]:
                raise UploadError(400, "沒有收到檔案")
        except UploadError as e:
            cur = state["cur"]
            if cur:
                cur["fh"].close()
                try:
                    os.unlink(cur["path"])
                except OSError:
                    pass
            self._drain(state["left"])
            sess.log(ip, "拒絕", f"{e.status} {e.message}")
            return self._reply_upload(e.status, False, e.message, [s["name"] for s in state["saved"]])
        except BaseException:
            cur = state["cur"]
            if cur:
                cur["fh"].close()
                try:
                    os.unlink(cur["path"])
                except OSError:
                    pass
            raise
        with sess._lock:
            sess.uploads.extend(s["name"] for s in state["saved"])
        names = [s["name"] for s in state["saved"]]
        self._reply_upload(200, True, f"已儲存 {len(names)} 個檔案", names, state["saved"])
        if sess.once:
            self.server.request_stop("once")

    def _drain(self, left: int, limit: int = 8 * 1024 * 1024) -> None:
        """出錯時把還沒讀的一小段請求內容讀掉，避免對方因連線被重置而看不到錯誤訊息。"""
        try:
            left = min(left, limit)
            self.connection.settimeout(2)
            while left > 0:
                chunk = self.rfile.read(min(READ_CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
        except (OSError, ValueError):
            pass

    def _reply_upload(self, status: int, ok: bool, text: str, names: List[str], saved: Optional[list] = None) -> None:
        accept = self.headers.get("Accept") or ""
        if "text/html" in accept and "application/json" not in accept:
            self._send_html(status, lambda n: pages.result_page(ok, text, names, n))
        elif ok:
            self._send_json(status, {"ok": True, "saved": saved or []})
        else:
            self._send_json(status, {"ok": False, "error": text})


def make_server(session: Session, host: str, port: int = 0) -> QrdropServer:
    return QrdropServer((host, port), session)
