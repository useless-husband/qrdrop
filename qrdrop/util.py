"""小工具：區網 IP、時間長度／大小解析、人類可讀大小。"""

from __future__ import annotations

import re
import socket
from typing import Optional

_UNITS = {"": 1, "b": 1, "k": 1024, "kb": 1024, "m": 1024 ** 2, "mb": 1024 ** 2, "g": 1024 ** 3, "gb": 1024 ** 3, "t": 1024 ** 4, "tb": 1024 ** 4}
_DURATIONS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_size(text: str) -> int:
    """'500M'、'2g'、'1.5GB'、'1048576' -> 位元組（以 1024 為底）。"""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]{0,2})\s*", str(text))
    if not m or m.group(2).lower() not in _UNITS:
        raise ValueError(f"看不懂的大小：{text!r}（例如 500M、2G）")
    n = int(float(m.group(1)) * _UNITS[m.group(2).lower()])
    if n <= 0:
        raise ValueError("大小必須大於 0")
    return n


def parse_duration(text: str) -> Optional[float]:
    """'10m'、'90s'、'1h'、'600' -> 秒；'0'、'off'、'none' -> None（不逾時）。"""
    t = str(text).strip().lower()
    if t in ("0", "off", "none", "no", ""):
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", t)
    if not m:
        raise ValueError(f"看不懂的時間長度：{text!r}（例如 90s、10m、1h）")
    secs = float(m.group(1)) * _DURATIONS[m.group(2)]
    return secs if secs > 0 else None


def human_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"  # pragma: no cover


def human_duration(secs: float) -> str:
    secs = int(secs)
    if secs % 3600 == 0 and secs >= 3600:
        return f"{secs // 3600} 小時"
    if secs % 60 == 0 and secs >= 60:
        return f"{secs // 60} 分鐘"
    return f"{secs} 秒"


def lan_ip() -> Optional[str]:
    """找出對外網卡的 IPv4（不會真的送出封包）。找不到回傳 None。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1，UDP connect 只是查路由表
        ip = s.getsockname()[0]
        if ip and not ip.startswith("127.") and ip != "0.0.0.0":
            return ip
    except OSError:
        pass
    finally:
        s.close()
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return None
