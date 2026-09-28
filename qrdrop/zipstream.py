"""串流產生 zip：邊讀檔邊輸出，不寫暫存檔，也不需要事先知道壓縮後大小。

每個項目都使用 data descriptor（旗標位元 3），所以可以一次讀完不回頭。
需要時（單檔 >= 4 GiB 或偏移量超過 4 GiB）自動改用 zip64。
"""

from __future__ import annotations

import os
import struct
import time
import zlib
from typing import Iterator, List, Sequence, Tuple

MARK = 0xFFFFFFFF  # 「請看 zip64 extra」的標記值
ZIP64_THRESHOLD = 0xFFFFFFFF  # 單檔大小/偏移量達到這個值就改用 zip64（測試會調小）
CHUNK = 256 * 1024
_STORED_EXTS = {
    ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar", ".zst", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif",
    ".avif", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi",
    ".pdf", ".docx", ".xlsx", ".pptx", ".jar", ".apk", ".ipa", ".dmg",
}


def _dos_datetime(ts: float) -> Tuple[int, int]:
    t = time.localtime(ts)
    year = max(t.tm_year, 1980)
    return ((t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2)), (((year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday)


class _Entry:
    def __init__(self, arcname: str, path: str):
        self.arcname = arcname
        self.path = path
        st = os.stat(path)
        self.size_hint = st.st_size
        self.mtime = st.st_mtime
        self.name_bytes = arcname.encode("utf-8")
        self.stored = os.path.splitext(arcname)[1].lower() in _STORED_EXTS
        self.zip64 = st.st_size >= ZIP64_THRESHOLD
        self.crc = 0
        self.csize = 0
        self.usize = 0
        self.offset = 0


def stream_zip(files: Sequence[Tuple[str, str]]) -> Iterator[bytes]:
    """files: [(壓縮檔內的路徑, 本機路徑), ...]，逐段 yield zip 位元組。"""
    entries: List[_Entry] = []
    pos = 0
    for arcname, path in files:
        e = _Entry(arcname.replace("\\", "/").lstrip("/"), path)
        e.offset = pos
        if e.offset >= ZIP64_THRESHOLD:
            e.zip64 = True
        dtime, ddate = _dos_datetime(e.mtime)
        extra = struct.pack("<HHQQ", 1, 16, 0, 0) if e.zip64 else b""
        method = 0 if e.stored else 8
        header = struct.pack(
            "<IHHHHHIIIHH", 0x04034B50, 45 if e.zip64 else 20, 0x0808, method, dtime, ddate, 0,
            MARK if e.zip64 else 0, MARK if e.zip64 else 0, len(e.name_bytes), len(extra),
        ) + e.name_bytes + extra
        yield header
        pos += len(header)
        comp = None if e.stored else zlib.compressobj(6, zlib.DEFLATED, -15)
        crc = 0
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                crc = zlib.crc32(chunk, crc)
                e.usize += len(chunk)
                out = chunk if comp is None else comp.compress(chunk)
                if out:
                    e.csize += len(out)
                    pos += len(out)
                    yield out
        if comp is not None:
            out = comp.flush()
            if out:
                e.csize += len(out)
                pos += len(out)
                yield out
        e.crc = crc & 0xFFFFFFFF
        if e.zip64:
            desc = struct.pack("<IIQQ", 0x08074B50, e.crc, e.csize, e.usize)
        else:
            desc = struct.pack("<IIII", 0x08074B50, e.crc, e.csize, e.usize)
        yield desc
        pos += len(desc)
        entries.append(e)
    cd_start = pos
    cd_size = 0
    for e in entries:
        dtime, ddate = _dos_datetime(e.mtime)
        fields = []
        csize, usize, off = e.csize, e.usize, e.offset
        if e.zip64:  # 本地標頭已用 zip64：兩個大小欄位都要放進 extra
            fields += [e.usize, e.csize]
            usize = csize = MARK
        if e.offset >= MARK:
            fields.append(e.offset)
            off = MARK
        extra = struct.pack("<HH", 1, 8 * len(fields)) + struct.pack("<" + "Q" * len(fields), *fields) if fields else b""
        rec = struct.pack(
            "<IHHHHHHIIIHHHHHII", 0x02014B50, 45 if extra else 20, 45 if extra else 20, 0x0808,
            0 if e.stored else 8, dtime, ddate, e.crc, csize, usize, len(e.name_bytes), len(extra), 0, 0, 0,
            (0o100644 << 16), off,
        ) + e.name_bytes + extra
        yield rec
        cd_size += len(rec)
    n = len(entries)
    need64 = n >= 0xFFFF or cd_start >= MARK or cd_size >= MARK
    if need64:
        yield struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, n, n, cd_size, cd_start)
        yield struct.pack("<IIQI", 0x07064B50, 0, cd_start + cd_size, 1)
    yield struct.pack(
        "<IHHHHIIH", 0x06054B50, 0, 0, min(n, 0xFFFF), min(n, 0xFFFF),
        min(cd_size, MARK), min(cd_start, MARK), 0,
    )
