"""從零實作的 QR Code 編碼器（ISO/IEC 18004，模型 2）。

支援：
  * numeric / alphanumeric / byte（UTF-8）模式，自動挑選
  * 版本 1–40（自動選最小）、錯誤修正等級 L / M / Q / H
  * GF(256) Reed–Solomon、區塊交錯
  * 8 種遮罩，依規格的四項罰分挑最佳
  * 格式資訊（BCH 15,5）與版本資訊（BCH 18,6）

只使用標準函式庫。主要入口是 :func:`encode`。
"""

from __future__ import annotations

import re
from itertools import chain, groupby
from typing import List, Optional, Sequence, Union

__all__ = ["QRCode", "DataTooLong", "encode", "ECC_LEVELS"]

ECC_LEVELS = ("L", "M", "Q", "H")
_ECC_INDEX = {"L": 0, "M": 1, "Q": 2, "H": 3}
# 格式資訊中的 2 位元等級代碼（注意順序不是 L,M,Q,H）
_ECC_FORMAT_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}

# 每個區塊的錯誤修正碼字數；索引 0 是佔位
_ECC_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
)
# 區塊數
_NUM_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
)

NUMERIC, ALPHANUMERIC, BYTE = "numeric", "alphanumeric", "byte"
_MODE_INDICATOR = {NUMERIC: 0b0001, ALPHANUMERIC: 0b0010, BYTE: 0b0100}
ALPHANUMERIC_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
_ALNUM_INDEX = {c: i for i, c in enumerate(ALPHANUMERIC_CHARSET)}
PAD_BYTES = (0xEC, 0x11)


class DataTooLong(ValueError):
    """資料放不進指定範圍的版本。"""


# ---------------------------------------------------------------- GF(256)

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256


def _init_gf() -> None:
    x = 1
    for i in range(255):
        _GF_EXP[i] = x
        _GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D  # x^8 + x^4 + x^3 + x^2 + 1
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]


_init_gf()


def gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


_generator_cache: dict = {}


def rs_generator(degree: int) -> List[int]:
    """(x - a^0)(x - a^1)...(x - a^(degree-1))，回傳最高次項以外的係數（高次到低次）。"""
    if degree in _generator_cache:
        return _generator_cache[degree]
    poly = [1]  # 最高次項係數為 1，高次在前
    for i in range(degree):
        root = _GF_EXP[i]
        nxt = poly + [0]
        for j, c in enumerate(poly):
            nxt[j + 1] ^= gf_mul(c, root)
        poly = nxt
    result = poly[1:]
    _generator_cache[degree] = result
    return result


def rs_remainder(data: Sequence[int], degree: int) -> List[int]:
    """資料多項式乘 x^degree 後除以生成多項式的餘式，即 ECC 碼字。"""
    gen = rs_generator(degree)
    rem = [0] * degree
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        if factor:
            for i, g in enumerate(gen):
                rem[i] ^= gf_mul(g, factor)
    return rem


# ---------------------------------------------------------------- 容量

def _check_version(version: int) -> None:
    if not 1 <= version <= 40:
        raise ValueError("version 必須在 1–40")


def _check_ecc(ecc: str) -> None:
    if ecc not in _ECC_INDEX:
        raise ValueError("ecc 必須是 L / M / Q / H")


def alignment_positions(version: int) -> List[int]:
    _check_version(version)
    if version == 1:
        return []
    count = version // 7 + 2
    size = version * 4 + 17
    step = 26 if version == 32 else (version * 4 + count * 2 + 1) // (count * 2 - 2) * 2
    return [6] + [size - 7 - i * step for i in range(count - 2, -1, -1)]


def num_raw_data_modules(version: int) -> int:
    """扣掉功能圖樣、格式與版本資訊後可放資料＋ECC 的模組數。"""
    _check_version(version)
    result = (16 * version + 128) * version + 64
    if version >= 2:
        n = version // 7 + 2
        result -= (25 * n - 10) * n - 55
        if version >= 7:
            result -= 36
    return result


def num_data_codewords(version: int, ecc: str) -> int:
    _check_version(version)
    _check_ecc(ecc)
    e = _ECC_INDEX[ecc]
    return num_raw_data_modules(version) // 8 - _ECC_PER_BLOCK[e][version] * _NUM_BLOCKS[e][version]


def _count_bits(mode: str, version: int) -> int:
    tier = 0 if version <= 9 else 1 if version <= 26 else 2
    return {NUMERIC: (10, 12, 14), ALPHANUMERIC: (9, 11, 13), BYTE: (8, 16, 16)}[mode][tier]


def detect_mode(text: str) -> str:
    if text.isascii() and text.isdigit():
        return NUMERIC
    if text and all(c in _ALNUM_INDEX for c in text):
        return ALPHANUMERIC
    return BYTE


def _data_bit_length(mode: str, count: int) -> int:
    if mode == NUMERIC:
        return 10 * (count // 3) + (0, 4, 7)[count % 3]
    if mode == ALPHANUMERIC:
        return 11 * (count // 2) + 6 * (count % 2)
    return 8 * count


def max_capacity(version: int, ecc: str, mode: str = BYTE) -> int:
    """在該版本與等級下，指定模式最多可放幾個字元（byte 模式為位元組數）。"""
    bits = num_data_codewords(version, ecc) * 8 - 4 - _count_bits(mode, version)
    if bits < 0:
        return 0
    limit = (1 << _count_bits(mode, version)) - 1
    if mode == NUMERIC:
        n = bits // 10 * 3
        rest = bits % 10
        n += 2 if rest >= 7 else 1 if rest >= 4 else 0
    elif mode == ALPHANUMERIC:
        n = bits // 11 * 2 + (1 if bits % 11 >= 6 else 0)
    else:
        n = bits // 8
    return min(n, limit)


# ---------------------------------------------------------------- 位元串

def _append(bits: List[int], value: int, length: int) -> None:
    for i in range(length - 1, -1, -1):
        bits.append((value >> i) & 1)


def _encode_payload(mode: str, payload: Union[str, bytes]) -> List[int]:
    bits: List[int] = []
    if mode == NUMERIC:
        s = payload  # type: ignore[assignment]
        for i in range(0, len(s), 3):
            chunk = s[i:i + 3]
            _append(bits, int(chunk), (0, 4, 7, 10)[len(chunk)])
    elif mode == ALPHANUMERIC:
        s = payload  # type: ignore[assignment]
        for i in range(0, len(s) - 1, 2):
            _append(bits, _ALNUM_INDEX[s[i]] * 45 + _ALNUM_INDEX[s[i + 1]], 11)
        if len(s) % 2:
            _append(bits, _ALNUM_INDEX[s[-1]], 6)
    else:
        for b in payload:  # type: ignore[union-attr]
            _append(bits, b, 8)
    return bits


def build_data_codewords(mode: str, payload: Union[str, bytes], version: int, ecc: str) -> List[int]:
    """模式指示＋字元數＋資料＋結束符＋補位，回傳剛好填滿容量的資料碼字。"""
    count = len(payload)
    bits: List[int] = []
    _append(bits, _MODE_INDICATOR[mode], 4)
    _append(bits, count, _count_bits(mode, version))
    bits.extend(_encode_payload(mode, payload))
    capacity_bits = num_data_codewords(version, ecc) * 8
    if len(bits) > capacity_bits:
        raise DataTooLong("資料超過容量")
    bits.extend([0] * min(4, capacity_bits - len(bits)))  # 結束符
    bits.extend([0] * (-len(bits) % 8))
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0
    while len(words) < capacity_bits // 8:
        words.append(PAD_BYTES[pad])
        pad ^= 1
    return words


def add_ecc_and_interleave(data: Sequence[int], version: int, ecc: str) -> List[int]:
    """切成區塊、各自算 ECC，再依規格交錯輸出最終碼字序列。"""
    e = _ECC_INDEX[ecc]
    n_blocks = _NUM_BLOCKS[e][version]
    ecc_len = _ECC_PER_BLOCK[e][version]
    total = num_raw_data_modules(version) // 8
    n_short = n_blocks - total % n_blocks
    short_len = total // n_blocks  # 短區塊（資料＋ECC）長度
    if len(data) != total - ecc_len * n_blocks:
        raise ValueError("資料碼字數與版本不符")
    data_blocks, ecc_blocks = [], []
    k = 0
    for i in range(n_blocks):
        dlen = short_len - ecc_len + (0 if i < n_short else 1)
        block = list(data[k:k + dlen])
        k += dlen
        data_blocks.append(block)
        ecc_blocks.append(rs_remainder(block, ecc_len))
    out: List[int] = []
    for i in range(max(len(b) for b in data_blocks)):
        for block in data_blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(ecc_len):
        for block in ecc_blocks:
            out.append(block[i])
    return out


# ---------------------------------------------------------------- BCH

def format_bits(ecc: str, mask: int) -> int:
    """15 位元格式資訊（已 XOR 0x5412 遮罩）。"""
    data = (_ECC_FORMAT_BITS[ecc] << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def version_bits(version: int) -> int:
    """18 位元版本資訊（版本 7 以上才使用）。"""
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (version << 12) | rem


# ---------------------------------------------------------------- 遮罩與罰分

MASK_FUNCTIONS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)

_FINDER_LIKE = re.compile(r"(?=(10111010000|00001011101))")


def penalty_score(modules: Sequence[Sequence[bool]]) -> int:
    """依 ISO 18004 §7.8.3 計算四項罰分總和。"""
    n = len(modules)
    cols = [list(c) for c in zip(*modules)]
    score = 0
    # N1：同色連續 >= 5 個，得 3 + (長度 - 5) 分
    for line in chain(modules, cols):
        for _, grp in groupby(line):
            run = sum(1 for _ in grp)
            if run >= 5:
                score += run - 2
    # N2：2x2 同色區塊，每個 3 分
    for y in range(n - 1):
        r0, r1 = modules[y], modules[y + 1]
        for x in range(n - 1):
            v = r0[x]
            if v == r0[x + 1] == r1[x] == r1[x + 1]:
                score += 3
    # N3：1:1:3:1:1 圖樣前或後有 4 個淺色，每個 40 分
    for line in chain(modules, cols):
        s = "".join("1" if b else "0" for b in line)
        score += 40 * len(_FINDER_LIKE.findall(s))
    # N4：深色比例偏離 50%，每 5% 罰 10 分
    dark = sum(sum(1 for b in row if b) for row in modules)
    total = n * n
    k = min(abs(dark * 20 // total * 5 - 50), abs(dark * 20 // total * 5 + 5 - 50)) // 5
    score += 10 * k
    return score


# ---------------------------------------------------------------- 矩陣

class QRCode:
    """已編碼的 QR Code。``modules[y][x]`` 為 True 代表深色。"""

    def __init__(self, version: int, ecc: str, mask: int, modules: List[List[bool]], mode: str):
        self.version = version
        self.ecc = ecc
        self.mask = mask
        self.modules = modules
        self.mode = mode
        self.size = len(modules)

    def get(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size and self.modules[y][x]

    def __repr__(self) -> str:
        return f"QRCode(version={self.version}, ecc={self.ecc!r}, mask={self.mask}, mode={self.mode!r})"


class _Canvas:
    def __init__(self, version: int):
        self.version = version
        self.size = version * 4 + 17
        self.m = [[False] * self.size for _ in range(self.size)]
        self.func = [[False] * self.size for _ in range(self.size)]
        self._draw_function_patterns()

    def _set(self, x: int, y: int, dark: bool) -> None:
        self.m[y][x] = bool(dark)
        self.func[y][x] = True

    def _draw_function_patterns(self) -> None:
        n = self.size
        for i in range(n):  # 定位線
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):  # 三個定位圖樣＋分隔線
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        dist = max(abs(dx), abs(dy))
                        self._set(x, y, dist not in (2, 4))
        pos = alignment_positions(self.version)
        for i, cy in enumerate(pos):
            for j, cx in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0, "M")  # 先佔位，稍後每個遮罩重畫
        self._draw_version()

    def draw_format(self, mask: int, ecc: str) -> None:
        bits = format_bits(ecc, mask)
        n = self.size

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1

        for i in range(0, 6):
            self._set(8, i, bit(i))
        self._set(8, 7, bit(6))
        self._set(8, 8, bit(7))
        self._set(7, 8, bit(8))
        for i in range(9, 15):
            self._set(14 - i, 8, bit(i))
        for i in range(0, 8):
            self._set(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set(8, n - 15 + i, bit(i))
        self._set(8, n - 8, True)  # 固定的深色模組

    def _draw_version(self) -> None:
        if self.version < 7:
            return
        bits = version_bits(self.version)
        n = self.size
        for i in range(18):
            b = (bits >> i) & 1 == 1
            a, c = n - 11 + i % 3, i // 3
            self._set(a, c, b)
            self._set(c, a, b)

    def place_codewords(self, words: Sequence[int]) -> None:
        n = self.size
        total_bits = len(words) * 8
        i = 0
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            for vert in range(n):
                y = n - 1 - vert if upward else vert
                for j in range(2):
                    x = right - j
                    if not self.func[y][x] and i < total_bits:
                        self.m[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2
        if i != total_bits:
            raise AssertionError("碼字沒有全部放進矩陣")

    def masked(self, mask: int) -> List[List[bool]]:
        fn = MASK_FUNCTIONS[mask]
        out = []
        for y in range(self.size):
            row = self.m[y]
            frow = self.func[y]
            out.append([row[x] ^ (not frow[x] and fn(x, y)) for x in range(self.size)])
        return out


def function_module_map(version: int) -> List[List[bool]]:
    """哪些位置是功能圖樣（True）；供測試用解碼器使用。"""
    return _Canvas(version).func


def build_matrix(codewords: Sequence[int], version: int, ecc: str, mask: Optional[int] = None) -> QRCode:
    canvas = _Canvas(version)
    canvas.place_codewords(codewords)
    best_mask, best_mods, best_score = -1, None, 0
    for mk in ([mask] if mask is not None else range(8)):
        # 為了讓格式資訊也進入罰分，套遮罩後要先畫格式
        mods = canvas.masked(mk)
        saved = canvas.m
        canvas.m = mods
        canvas.draw_format(mk, ecc)
        canvas.m = saved
        score = penalty_score(mods) if mask is None else 0
        if best_mods is None or score < best_score:
            best_mask, best_mods, best_score = mk, mods, score
    assert best_mods is not None
    return QRCode(version, ecc, best_mask, best_mods, BYTE)


def encode(
    data: Union[str, bytes],
    ecc: str = "M",
    min_version: int = 1,
    max_version: int = 40,
    mask: Optional[int] = None,
    mode: Optional[str] = None,
) -> QRCode:
    """把文字或位元組編成 QR Code。

    ``str`` 會自動在 numeric / alphanumeric / byte（UTF-8）中挑最省的模式；
    ``bytes`` 一律用 byte 模式。版本從 ``min_version`` 起找第一個放得下的。
    """
    ecc = ecc.upper()
    _check_ecc(ecc)
    _check_version(min_version)
    _check_version(max_version)
    if min_version > max_version:
        raise ValueError("min_version 不能大於 max_version")
    if mask is not None and not 0 <= mask <= 7:
        raise ValueError("mask 必須在 0–7")
    if isinstance(data, str):
        chosen = mode or detect_mode(data)
        payload: Union[str, bytes] = data.encode("utf-8") if chosen == BYTE else data
    else:
        chosen, payload = BYTE, bytes(data)
        if mode not in (None, BYTE):
            raise ValueError("bytes 只能用 byte 模式")
    if chosen == NUMERIC and not (isinstance(payload, str) and payload.isascii() and payload.isdigit()):
        raise ValueError("內容不是純數字")
    if chosen == ALPHANUMERIC and not (isinstance(payload, str) and all(c in _ALNUM_INDEX for c in payload)):
        raise ValueError("內容含有 alphanumeric 以外的字元")
    count = len(payload)
    for version in range(min_version, max_version + 1):
        need = 4 + _count_bits(chosen, version) + _data_bit_length(chosen, count)
        if count < (1 << _count_bits(chosen, version)) and need <= num_data_codewords(version, ecc) * 8:
            break
    else:
        raise DataTooLong(f"資料太長（{count} 個{'位元組' if chosen == BYTE else '字元'}），放不進版本 {max_version}-{ecc}")
    words = build_data_codewords(chosen, payload, version, ecc)
    final = add_ecc_and_interleave(words, version, ecc)
    qr = build_matrix(final, version, ecc, mask)
    qr.mode = chosen
    return qr
