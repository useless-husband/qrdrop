"""測試用的極簡 QR 解碼器（只吃乾淨的矩陣），用來對編碼器做往返驗證。

刻意不重用編碼器的區塊／ECC 產生邏輯，只借用「哪些位置是功能圖樣」的地圖。
"""

from qrdrop import qrcode as Q

_FMT_TO_ECC = {0b01: "L", 0b00: "M", 0b11: "Q", 0b10: "H"}


def _read_format(m):
    n = len(m)
    copy1 = 0
    pos1 = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(14 - i, 8) for i in range(9, 15)]
    for i, (x, y) in enumerate(pos1):
        copy1 |= int(m[y][x]) << i
    copy2 = 0
    pos2 = [(n - 1 - i, 8) for i in range(8)] + [(8, n - 15 + i) for i in range(8, 15)]
    for i, (x, y) in enumerate(pos2):
        copy2 |= int(m[y][x]) << i
    return copy1, copy2


def _syndromes_zero(codeword_block, ecc_len):
    """整個區塊（資料＋ECC）在 a^0..a^(ecc_len-1) 求值都應為 0。"""
    for i in range(ecc_len):
        acc = 0
        root = Q._GF_EXP[i]
        for c in codeword_block:
            acc = Q.gf_mul(acc, root) ^ c
        if acc:
            return False
    return True


def decode(qr):
    m = qr.modules
    n = len(m)
    version = (n - 17) // 4
    c1, c2 = _read_format(m)
    assert c1 == c2, "兩份格式資訊不一致"
    fmt = c1 ^ 0x5412
    assert Q.format_bits(_FMT_TO_ECC[(fmt >> 13) & 3], (fmt >> 10) & 7) == c1
    ecc = _FMT_TO_ECC[(fmt >> 13) & 3]
    mask = (fmt >> 10) & 7
    func = Q.function_module_map(version)
    fn = Q.MASK_FUNCTIONS[mask]
    bits = []
    right = n - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for vert in range(n):
            y = n - 1 - vert if upward else vert
            for j in range(2):
                x = right - j
                if not func[y][x]:
                    bits.append(int(m[y][x] ^ fn(x, y)))
        right -= 2
    total = Q.num_raw_data_modules(version) // 8
    words = [int("".join(map(str, bits[i * 8:i * 8 + 8])), 2) for i in range(total)]
    e = Q._ECC_INDEX[ecc]
    nb, el = Q._NUM_BLOCKS[e][version], Q._ECC_PER_BLOCK[e][version]
    n_short = nb - total % nb
    short_len = total // nb
    lens = [short_len - el + (0 if i < n_short else 1) for i in range(nb)]
    blocks = [[] for _ in range(nb)]
    k = 0
    for i in range(max(lens)):
        for b in range(nb):
            if i < lens[b]:
                blocks[b].append(words[k])
                k += 1
    eccs = [[] for _ in range(nb)]
    for i in range(el):
        for b in range(nb):
            eccs[b].append(words[k])
            k += 1
    data = []
    for b in range(nb):
        assert _syndromes_zero(blocks[b] + eccs[b], el), "RS 校驗失敗"
        data.extend(blocks[b])
    # 解位元串
    s = "".join(f"{w:08b}" for w in data)
    pos = 0
    out = bytearray()
    text_parts = []
    while pos + 4 <= len(s):
        mode = int(s[pos:pos + 4], 2)
        pos += 4
        if mode == 0:
            break
        name = {1: Q.NUMERIC, 2: Q.ALPHANUMERIC, 4: Q.BYTE}[mode]
        cb = Q._count_bits(name, version)
        count = int(s[pos:pos + cb], 2)
        pos += cb
        if name == Q.NUMERIC:
            res = ""
            while count:
                take = min(3, count)
                nbits = (0, 4, 7, 10)[take]
                res += str(int(s[pos:pos + nbits], 2)).zfill(take)
                pos += nbits
                count -= take
            text_parts.append(res)
        elif name == Q.ALPHANUMERIC:
            res = ""
            while count:
                if count >= 2:
                    v = int(s[pos:pos + 11], 2)
                    pos += 11
                    res += Q.ALPHANUMERIC_CHARSET[v // 45] + Q.ALPHANUMERIC_CHARSET[v % 45]
                    count -= 2
                else:
                    res += Q.ALPHANUMERIC_CHARSET[int(s[pos:pos + 6], 2)]
                    pos += 6
                    count -= 1
            text_parts.append(res)
        else:
            for _ in range(count):
                out.append(int(s[pos:pos + 8], 2))
                pos += 8
            text_parts.append(bytes(out).decode("utf-8"))
            out = bytearray()
    return "".join(text_parts), ecc, mask, version
