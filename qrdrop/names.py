"""檔名消毒與安全建檔。"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import BinaryIO, Tuple

MAX_NAME_BYTES = 200  # 留空間給「 (1)」之類的後綴，低於多數檔案系統的 255 位元組

_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
_WINDOWS_RESERVED |= {"COM\u00b9", "COM\u00b2", "COM\u00b3", "LPT\u00b9", "LPT\u00b2", "LPT\u00b3"}
_INVISIBLE = set("\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060\u2066\u2067\u2068\u2069\ufeff\u00ad")
_BAD_CHARS = re.compile(r'[<>:"|?*]')


def _truncate_utf8(s: str, limit: int) -> str:
    return s.encode("utf-8")[:limit].decode("utf-8", "ignore")


def sanitize_filename(name: object, default: str = "file") -> str:
    """把使用者給的檔名變成可以放心存進資料夾的「單純檔名」。

    處理：路徑（/ 與 \\，取最後一段）、../、絕對路徑與磁碟代號、NUL 與控制字元、
    雙向文字控制碼、Windows 不允許的字元與保留名、結尾的點與空白、開頭的點、過長。
    """
    if isinstance(name, bytes):
        name = name.decode("utf-8", "replace")
    s = unicodedata.normalize("NFC", str(name or ""))
    s = "".join(c for c in s if c not in _INVISIBLE and unicodedata.category(c) not in ("Cc", "Cs", "Cn", "Zl", "Zp"))
    s = s.replace("\\", "/")
    s = s.split("/")[-1]  # 只留最後一段，這樣 ../../x、/etc/x、C:\a\x 都變成 x
    s = _BAD_CHARS.sub("_", s)
    s = s.strip(" .")  # 開頭的點會變隱藏檔、結尾的點與空白在 Windows 會被吃掉
    if not s:
        return default
    stem, dot, ext = s.partition(".")
    if stem.strip().upper() in _WINDOWS_RESERVED:
        s = "_" + s
    if len(s.encode("utf-8")) > MAX_NAME_BYTES:
        base, ext_dot, ext = s.rpartition(".")
        if not ext_dot or len(ext.encode("utf-8")) > 20 or not base:
            s = _truncate_utf8(s, MAX_NAME_BYTES).rstrip(" .")
        else:
            keep = MAX_NAME_BYTES - len(ext.encode("utf-8")) - 1
            s = _truncate_utf8(base, keep).rstrip(" .") + "." + ext
    return s or default


def unique_path(directory: str, name: str) -> Tuple[str, BinaryIO]:
    """在 directory 以獨佔方式建立檔案；同名就改成「名稱 (1).副檔名」。

    回傳 (實際路徑, 已開啟的二進位寫入檔案)。用 O_EXCL 建立，所以不會覆蓋，也不會被競爭。
    """
    name = sanitize_filename(name)
    stem, ext = os.path.splitext(name)
    if not stem:  # 例如名稱就是 ".x" 之類的極端情況
        stem, ext = name, ""
    root = os.path.realpath(directory)
    n = 0
    while True:
        cand = name if n == 0 else f"{stem} ({n}){ext}"
        path = os.path.join(root, cand)
        if os.path.dirname(os.path.realpath(path)) != root:
            raise ValueError("檔名解析後跑出目標資料夾")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        except FileExistsError:
            n += 1
            if n > 10000:
                raise
            continue
        return path, os.fdopen(fd, "wb")
