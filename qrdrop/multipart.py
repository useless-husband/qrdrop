"""串流式的 multipart/form-data 解析器（不把整個上傳讀進記憶體）。"""

from __future__ import annotations

import re
from typing import Callable, Dict, Optional
from urllib.parse import unquote

MAX_HEADER_BYTES = 16 * 1024


class MultipartError(ValueError):
    pass


def parse_boundary(content_type: Optional[str]) -> bytes:
    if not content_type:
        raise MultipartError("缺少 Content-Type")
    m = re.match(r"\s*multipart/form-data\s*;(.*)$", content_type, re.I | re.S)
    if not m:
        raise MultipartError("Content-Type 不是 multipart/form-data")
    bm = re.search(r'boundary\s*=\s*(?:"([^"]{1,200})"|([^\s;]{1,200}))', m.group(1), re.I)
    if not bm:
        raise MultipartError("缺少 boundary")
    return (bm.group(1) or bm.group(2)).encode("latin-1", "replace")


_PARAM = re.compile(r';\s*([A-Za-z0-9_*.-]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;]*)')


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = re.sub(r'\\(["\\])', r"\1", v[1:-1])  # 只還原 \" 與 \\；其他反斜線（Windows 路徑）保留
    return v


def _params(value: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in _PARAM.finditer(";" + value.split(";", 1)[1] if ";" in value else ""):
        out.setdefault(m.group(1).lower(), _unquote(m.group(2)))
    return out


def parse_part_headers(raw: bytes) -> Dict[str, Optional[str]]:
    """回傳 {'name': 欄位名, 'filename': 檔名或 None, 'content_type': ...}。

    瀏覽器會直接送 UTF-8 的檔名位元組，所以自己解析而不是交給 email 套件。
    """
    text = raw.decode("utf-8", "replace")
    headers: Dict[str, str] = {}
    last = None
    for line in text.split("\r\n"):
        if not line:
            continue
        if line[0] in " \t" and last:
            headers[last] += " " + line.strip()
            continue
        k, sep, v = line.partition(":")
        if not sep:
            raise MultipartError("part 標頭格式錯誤")
        last = k.strip().lower()
        headers[last] = v.strip()
    disp = headers.get("content-disposition")
    if disp is None:
        raise MultipartError("part 缺少 Content-Disposition")
    if disp.split(";", 1)[0].strip().lower() != "form-data":
        raise MultipartError("Content-Disposition 不是 form-data")
    params = _params(disp)
    filename = params.get("filename")
    star = params.get("filename*")  # RFC 5987：charset'lang'percent-encoded
    if star:
        m = re.fullmatch(r"([\w-]+)'[^']*'(.*)", star)
        if m:
            try:
                filename = unquote(m.group(2), encoding=m.group(1))
            except LookupError:
                pass
    return {"name": params.get("name"), "filename": filename, "content_type": headers.get("content-type")}


class MultipartParser:
    """餵位元組進來（任意切法），透過三個回呼取得每個 part。

    on_part(headers)  一個 part 開始（headers 見 parse_part_headers）
    on_data(bytes)    該 part 的內容片段
    on_end()          該 part 結束
    回呼丟出的例外會直接往外傳。
    """

    def __init__(self, boundary: bytes, on_part: Callable, on_data: Callable, on_end: Callable):
        self._delim = b"\r\n--" + boundary
        self._on_part, self._on_data, self._on_end = on_part, on_data, on_end
        self._buf = b"\r\n"  # 第一個 boundary 前面沒有 CRLF，補一個統一處理
        self._state = "preamble"
        self.finished = False

    def feed(self, chunk: bytes) -> None:
        if self.finished:
            return
        self._buf += chunk
        while self._step():
            pass

    def _step(self) -> bool:
        buf = self._buf
        if self._state == "preamble":
            i = buf.find(self._delim)
            if i < 0:
                if len(buf) > MAX_HEADER_BYTES:  # 前言太長且沒有 boundary
                    raise MultipartError("找不到 boundary")
                self._buf = buf[-len(self._delim):]
                return False
            self._buf = buf[i + len(self._delim):]
            self._state = "after_delim"
            return True
        if self._state == "after_delim":
            if len(buf) < 2:
                return False
            if buf[:2] == b"--":
                self.finished = True
                self._buf = b""
                return False
            j = buf.find(b"\r\n")
            if j < 0:
                if len(buf) > 1024:
                    raise MultipartError("boundary 之後的格式錯誤")
                return False
            if buf[:j].strip(b" \t"):
                raise MultipartError("boundary 之後的格式錯誤")
            self._buf = buf[j + 2:]
            self._state = "headers"
            return True
        if self._state == "headers":
            if buf.startswith(b"\r\n"):
                raw = b""
                self._buf = buf[2:]
            else:
                j = buf.find(b"\r\n\r\n")
                if j < 0:
                    if len(buf) > MAX_HEADER_BYTES:
                        raise MultipartError("part 標頭太長")
                    return False
                raw = buf[:j + 2]
                self._buf = buf[j + 4:]
            self._on_part(parse_part_headers(raw))
            self._state = "body"
            return True
        # body
        i = buf.find(self._delim)
        if i >= 0:
            if i:
                self._on_data(buf[:i])
            self._on_end()
            self._buf = buf[i + len(self._delim):]
            self._state = "after_delim"
            return True
        keep = len(self._delim) - 1
        if len(buf) > keep:
            self._on_data(buf[:-keep])
            self._buf = buf[-keep:]
        return False

    def close(self) -> None:
        if not self.finished:
            raise MultipartError("上傳內容不完整")
