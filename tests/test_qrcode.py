import random
import unittest

from qrdrop import qrcode as Q
from tests.qrdecode import decode

ECCS = "LMQH"


class TestTables(unittest.TestCase):
    def test_module_count_known_versions(self):
        # 版本 1 = 21x21，其可用資料模組數 = 26 碼字 * 8
        self.assertEqual(Q.num_raw_data_modules(1), 208)
        self.assertEqual(Q.num_raw_data_modules(2), 359)
        self.assertEqual(Q.num_raw_data_modules(7), 1568)
        self.assertEqual(Q.num_raw_data_modules(40), 29648)

    def test_data_codewords_known(self):
        known = {(1, "L"): 19, (1, "M"): 16, (1, "Q"): 13, (1, "H"): 9,
                 (10, "M"): 216, (40, "L"): 2956, (40, "M"): 2334, (40, "Q"): 1666, (40, "H"): 1276}
        for (v, e), n in known.items():
            self.assertEqual(Q.num_data_codewords(v, e), n, (v, e))

    def test_byte_capacity_known(self):
        self.assertEqual(Q.max_capacity(1, "L"), 17)
        self.assertEqual(Q.max_capacity(1, "M"), 14)
        self.assertEqual(Q.max_capacity(1, "Q"), 11)
        self.assertEqual(Q.max_capacity(1, "H"), 7)
        self.assertEqual(Q.max_capacity(40, "L"), 2953)
        self.assertEqual(Q.max_capacity(40, "H"), 1273)

    def test_numeric_alnum_capacity_known(self):
        self.assertEqual(Q.max_capacity(1, "L", Q.NUMERIC), 41)
        self.assertEqual(Q.max_capacity(1, "M", Q.NUMERIC), 34)
        self.assertEqual(Q.max_capacity(40, "L", Q.NUMERIC), 7089)
        self.assertEqual(Q.max_capacity(1, "L", Q.ALPHANUMERIC), 25)
        self.assertEqual(Q.max_capacity(40, "L", Q.ALPHANUMERIC), 4296)
        self.assertEqual(Q.max_capacity(40, "H", Q.ALPHANUMERIC), 1852)

    def test_block_structure_is_consistent_all_160(self):
        for v in range(1, 41):
            for i, e in enumerate(ECCS):
                total = Q.num_raw_data_modules(v) // 8
                nb, el = Q._NUM_BLOCKS[i][v], Q._ECC_PER_BLOCK[i][v]
                with self.subTest(v=v, e=e):
                    self.assertGreater(total - nb * el, 0)
                    # 短區塊資料長度必須為正，且長區塊只比短區塊多 1
                    self.assertGreater(total // nb - el, 0)
                    self.assertLessEqual(nb, 81)

    def test_capacity_monotonic(self):
        for e in ECCS:
            caps = [Q.num_data_codewords(v, e) for v in range(1, 41)]
            self.assertEqual(caps, sorted(caps))
        for v in range(1, 41):
            caps = [Q.num_data_codewords(v, e) for e in ECCS]
            self.assertEqual(caps, sorted(caps, reverse=True))

    def test_alignment_positions(self):
        self.assertEqual(Q.alignment_positions(1), [])
        self.assertEqual(Q.alignment_positions(2), [6, 18])
        self.assertEqual(Q.alignment_positions(7), [6, 22, 38])
        self.assertEqual(Q.alignment_positions(14), [6, 26, 46, 66])
        self.assertEqual(Q.alignment_positions(32), [6, 34, 60, 86, 112, 138])
        self.assertEqual(Q.alignment_positions(40), [6, 30, 58, 86, 114, 142, 170])

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            Q.num_data_codewords(0, "L")
        with self.assertRaises(ValueError):
            Q.num_data_codewords(41, "L")
        with self.assertRaises(ValueError):
            Q.num_data_codewords(1, "X")


class TestReedSolomon(unittest.TestCase):
    def test_gf_tables(self):
        self.assertEqual(Q._GF_EXP[0], 1)
        self.assertEqual(Q._GF_EXP[1], 2)
        self.assertEqual(Q._GF_EXP[8], 0x1D)
        self.assertEqual(Q._GF_EXP[255], 1)
        self.assertEqual(sorted(Q._GF_EXP[:255]), list(range(1, 256)))

    def test_gf_mul(self):
        self.assertEqual(Q.gf_mul(0, 77), 0)
        self.assertEqual(Q.gf_mul(1, 77), 77)
        self.assertEqual(Q.gf_mul(2, 0x80), 0x1D)
        for _ in range(200):
            a, b, c = (random.randrange(1, 256) for _ in range(3))
            self.assertEqual(Q.gf_mul(a, b), Q.gf_mul(b, a))
            self.assertEqual(Q.gf_mul(a, b ^ c), Q.gf_mul(a, b) ^ Q.gf_mul(a, c))

    def test_generator_degree_7_known(self):
        # 規格附錄 A：7 階生成多項式係數的 alpha 指數 0, 87, 229, 146, 149, 238, 102, 21
        exps = [0, 87, 229, 146, 149, 238, 102, 21]
        expected = [Q._GF_EXP[e] for e in exps][1:]
        self.assertEqual(Q.rs_generator(7), expected)

    def test_hello_world_1m(self):
        data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
        self.assertEqual(Q.rs_remainder(data, 10), [196, 35, 39, 119, 235, 215, 231, 226, 93, 23])

    def test_zero_remainder_property(self):
        for degree in (7, 10, 17, 30):
            data = [random.randrange(256) for _ in range(40)]
            block = data + Q.rs_remainder(data, degree)
            for i in range(degree):
                acc = 0
                for c in block:
                    acc = Q.gf_mul(acc, Q._GF_EXP[i]) ^ c
                self.assertEqual(acc, 0)


class TestIsoVectors(unittest.TestCase):
    """ISO/IEC 18004 附錄的範例 "01234567"，1-M。"""

    def test_data_codewords(self):
        words = Q.build_data_codewords(Q.NUMERIC, "01234567", 1, "M")
        self.assertEqual(words, [0x10, 0x20, 0x0C, 0x56, 0x61, 0x80, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11])

    def test_ecc_codewords(self):
        words = Q.build_data_codewords(Q.NUMERIC, "01234567", 1, "M")
        self.assertEqual(Q.rs_remainder(words, 10), [0xA5, 0x24, 0xD4, 0xC1, 0xED, 0x36, 0xC7, 0x87, 0x2C, 0x55])

    def test_final_sequence(self):
        words = Q.build_data_codewords(Q.NUMERIC, "01234567", 1, "M")
        final = Q.add_ecc_and_interleave(words, 1, "M")
        self.assertEqual(len(final), 26)
        self.assertEqual(final[:16], words)
        self.assertEqual(final[16:], [0xA5, 0x24, 0xD4, 0xC1, 0xED, 0x36, 0xC7, 0x87, 0x2C, 0x55])

    def test_auto_choice_is_1m_numeric(self):
        qr = Q.encode("01234567", "M")
        self.assertEqual((qr.version, qr.ecc, qr.mode), (1, "M", "numeric"))
        self.assertEqual(qr.size, 21)

    def test_alphanumeric_hello_world_codewords(self):
        words = Q.build_data_codewords(Q.ALPHANUMERIC, "HELLO WORLD", 1, "M")
        self.assertEqual(words, [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17])

    def test_interleave_multi_block_order(self):
        # 5-Q：2 個短區塊(15 資料碼字) + 2 個長區塊(16)，各 18 ECC
        v, e = 5, "Q"
        data = list(range(Q.num_data_codewords(v, e)))
        out = Q.add_ecc_and_interleave(data, v, e)
        self.assertEqual(out[:4], [0, 15, 30, 46])  # 各區塊第 0 個
        self.assertEqual(len(out), Q.num_raw_data_modules(v) // 8)
        # 長區塊多出的最後一個資料碼字排在資料段尾端
        self.assertEqual(out[Q.num_data_codewords(v, e) - 2:Q.num_data_codewords(v, e)], [45, 61])


class TestFormatVersionBits(unittest.TestCase):
    def test_format_known_mask0(self):
        self.assertEqual(Q.format_bits("L", 0), 0b111011111000100)
        self.assertEqual(Q.format_bits("M", 0), 0b101010000010010)
        self.assertEqual(Q.format_bits("Q", 0), 0b011010101011111)
        self.assertEqual(Q.format_bits("H", 0), 0b001011010001001)

    def test_format_bch_properties(self):
        seen = set()
        for e in ECCS:
            for m in range(8):
                b = Q.format_bits(e, m)
                self.assertNotIn(b, seen)
                seen.add(b)
                self.assertEqual((b ^ 0x5412) >> 10 & 0x1F, (Q._ECC_FORMAT_BITS[e] << 3) | m)
        # 任兩個碼字的漢明距離至少 7（BCH 15,5 的性質）
        vals = sorted(seen)
        for i, a in enumerate(vals):
            for b in vals[i + 1:]:
                self.assertGreaterEqual(bin(a ^ b).count("1"), 7)

    def test_version_known(self):
        self.assertEqual(Q.version_bits(7), 0b000111110010010100)
        self.assertEqual(Q.version_bits(8), 0b001000010110111100)
        self.assertEqual(Q.version_bits(40), 0b101000110001101001)

    def test_version_hamming(self):
        vals = [Q.version_bits(v) for v in range(7, 41)]
        for i, a in enumerate(vals):
            for b in vals[i + 1:]:
                self.assertGreaterEqual(bin(a ^ b).count("1"), 8)


class TestMasksAndPenalty(unittest.TestCase):
    def test_mask_formulas(self):
        f = Q.MASK_FUNCTIONS
        self.assertTrue(f[0](0, 0)); self.assertFalse(f[0](1, 0))
        self.assertTrue(f[1](5, 2)); self.assertFalse(f[1](5, 3))
        self.assertTrue(f[2](3, 9)); self.assertFalse(f[2](4, 9))
        self.assertTrue(f[3](1, 2)); self.assertFalse(f[3](1, 3))
        self.assertTrue(f[4](0, 0)); self.assertFalse(f[4](3, 0))
        self.assertTrue(f[5](0, 7)); self.assertFalse(f[5](1, 1))
        self.assertTrue(f[6](0, 5)); self.assertTrue(f[6](2, 3))
        self.assertTrue(f[7](0, 0)); self.assertFalse(f[7](1, 0))

    def test_penalty_all_light(self):
        n = 21
        m = [[False] * n for _ in range(n)]
        # N1：每行每列 21 個同色 -> (21-2) * 42；N2：20*20 個 -> 1200；N4：0% 深 -> 偏離 45% -> 9 級 * 10
        self.assertEqual(Q.penalty_score(m), 19 * 42 + 3 * 400 + 90)

    def test_penalty_checkerboard(self):
        n = 21
        m = [[(x + y) % 2 == 0 for x in range(n)] for y in range(n)]
        dark = sum(sum(r) for r in m)
        self.assertEqual(dark, 221)
        # 沒有 >=5 連續、沒有 2x2；N3：棋盤格中 10111010000 不會出現
        self.assertEqual(Q.penalty_score(m), 0)

    def test_penalty_n3_pattern(self):
        n = 21
        m = [[False] * n for _ in range(n)]
        row = "00001011101" + "0" * 10
        base = Q.penalty_score(m)
        m[10] = [c == "1" for c in row]
        after = Q.penalty_score(m)
        self.assertNotEqual(base, after)
        # 抽掉圖樣後單獨比對 N3 貢獻：用交錯圖樣的全棋盤加上一列
        b = [[(x + y) % 2 == 0 for x in range(n)] for y in range(n)]
        b[0] = [c == "1" for c in "10111010000" + "0" * 10]
        self.assertGreaterEqual(Q.penalty_score(b), 40)

    def test_penalty_n1_run(self):
        n = 21
        m = [[(x + y) % 2 == 0 for x in range(n)] for y in range(n)]
        for x in range(6):  # 第 0 列前 6 格同色
            m[0][x] = True
        # 這會改變其他項，只檢查 N1 至少加了 (6-2)=4 分
        self.assertGreaterEqual(Q.penalty_score(m), 4)

    def test_best_mask_is_minimum(self):
        qr = Q.encode("https://example.com/abc", "M")
        words = Q.add_ecc_and_interleave(Q.build_data_codewords(Q.BYTE, b"https://example.com/abc", qr.version, "M"), qr.version, "M")
        scores = [penalty(Q.build_matrix(words, qr.version, "M", mask=k)) for k in range(8)]
        self.assertEqual(scores[qr.mask], min(scores))
        self.assertEqual(qr.mask, scores.index(min(scores)))


def penalty(qr):
    return Q.penalty_score(qr.modules)


class TestStructure(unittest.TestCase):
    def test_finder_patterns_and_timing(self):
        for v in (1, 2, 7, 20, 40):
            qr = Q.encode("A", "L", min_version=v)
            n = qr.size
            self.assertEqual(n, v * 4 + 17)
            for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
                for dy in range(-3, 4):
                    for dx in range(-3, 4):
                        d = max(abs(dx), abs(dy))
                        self.assertEqual(qr.get(cx + dx, cy + dy), d != 2, (v, cx, cy, dx, dy))
            for i in range(8, n - 8):
                self.assertEqual(qr.get(i, 6), i % 2 == 0)
                self.assertEqual(qr.get(6, i), i % 2 == 0)
            self.assertTrue(qr.get(8, n - 8))  # 固定深色模組

    def test_alignment_pattern_present(self):
        qr = Q.encode("A", "L", min_version=2)
        c = 18
        self.assertTrue(qr.get(c, c))
        self.assertFalse(qr.get(c + 1, c))
        self.assertTrue(qr.get(c + 2, c + 2))

    def test_version_info_written(self):
        qr = Q.encode("A", "L", min_version=7)
        bits = Q.version_bits(7)
        n = qr.size
        for i in range(18):
            self.assertEqual(qr.get(n - 11 + i % 3, i // 3), (bits >> i) & 1 == 1)
            self.assertEqual(qr.get(i // 3, n - 11 + i % 3), (bits >> i) & 1 == 1)

    def test_fixed_mask_is_respected(self):
        for m in range(8):
            self.assertEqual(Q.encode("hello", "M", mask=m).mask, m)

    def test_deterministic(self):
        a = Q.encode("同樣的輸入", "Q")
        b = Q.encode("同樣的輸入", "Q")
        self.assertEqual(a.modules, b.modules)


class TestModeAndVersionChoice(unittest.TestCase):
    def test_detect_mode(self):
        self.assertEqual(Q.detect_mode("0123456789"), Q.NUMERIC)
        self.assertEqual(Q.detect_mode("HELLO WORLD $%*+-./:"), Q.ALPHANUMERIC)
        self.assertEqual(Q.detect_mode("hello"), Q.BYTE)
        self.assertEqual(Q.detect_mode("你好"), Q.BYTE)
        self.assertEqual(Q.detect_mode("１２３"), Q.BYTE)  # 全形數字不是 numeric
        self.assertEqual(Q.detect_mode(""), Q.BYTE)

    def test_minimum_version_at_boundaries(self):
        for e in ECCS:
            for v in (1, 2, 5, 9, 10, 20, 27, 40):
                cap = Q.max_capacity(v, e)
                data = "a" * cap
                self.assertEqual(Q.encode(data, e).version, v, (v, e))
                if v > 1:
                    self.assertEqual(Q.encode("a" * (Q.max_capacity(v - 1, e) + 1), e).version, v, (v, e))

    def test_too_long(self):
        with self.assertRaises(Q.DataTooLong):
            Q.encode("a" * 2954, "L")
        with self.assertRaises(Q.DataTooLong):
            Q.encode("1" * 7090, "L")
        with self.assertRaises(Q.DataTooLong):
            Q.encode("a" * 20, "M", max_version=1)

    def test_bad_args(self):
        for kw in ({"ecc": "Z"}, {"mask": 8}, {"min_version": 0}, {"min_version": 5, "max_version": 4}):
            with self.assertRaises(ValueError):
                Q.encode("x", **kw)
        with self.assertRaises(ValueError):
            Q.encode("abc", mode=Q.NUMERIC)
        with self.assertRaises(ValueError):
            Q.encode("abc", mode=Q.ALPHANUMERIC)

    def test_mode_selected(self):
        self.assertEqual(Q.encode("12345").mode, Q.NUMERIC)
        self.assertEqual(Q.encode("ABC123").mode, Q.ALPHANUMERIC)
        self.assertEqual(Q.encode("abc").mode, Q.BYTE)
        self.assertEqual(Q.encode(b"\x00\xff").mode, Q.BYTE)


class TestRoundTrip(unittest.TestCase):
    """用獨立寫的簡易解碼器讀回矩陣，檢查 RS 校驗與內容。"""

    def check(self, text, ecc="M", **kw):
        qr = Q.encode(text, ecc, **kw)
        got, e, mask, ver = decode(qr)
        self.assertEqual(got, text)
        self.assertEqual((e, mask, ver), (qr.ecc, qr.mask, qr.version))
        return qr

    def test_simple_texts(self):
        for t in ("0", "12345678901234567890", "HELLO WORLD", "https://192.168.1.23:8000/aB3_x-Zq/",
                  "hello", "你好，世界", "🙂 emoji", "line1\nline2", "a" * 100):
            for e in ECCS:
                with self.subTest(t=t, e=e):
                    self.check(t, e)

    def test_every_version_and_level_at_capacity(self):
        rng = random.Random(1234)
        for v in range(1, 41):
            for e in ECCS:
                cap = Q.max_capacity(v, e)
                text = "".join(rng.choice("abcXYZ019 中") for _ in range(20))
                data = bytes(rng.randrange(0x20, 0x7F) for _ in range(cap))
                with self.subTest(v=v, e=e):
                    qr = Q.encode(data.decode("ascii"), e, min_version=v)
                    self.assertEqual(qr.version, v)
                    got, *_ = decode(qr)
                    self.assertEqual(got, data.decode("ascii"))

    def test_numeric_and_alnum_at_capacity(self):
        for v in (1, 9, 10, 26, 27, 40):
            for e in ECCS:
                n = Q.max_capacity(v, e, Q.NUMERIC)
                txt = ("0123456789" * (n // 10 + 1))[:n]
                self.assertEqual(Q.encode(txt, e, min_version=v).version, v)
                self.check(txt, e, min_version=v)
                a = Q.max_capacity(v, e, Q.ALPHANUMERIC)
                txt = (Q.ALPHANUMERIC_CHARSET * (a // 45 + 1))[:a]
                self.check(txt, e, min_version=v)

    def test_all_masks(self):
        for m in range(8):
            self.check("mask test 遮罩", "Q", mask=m)

    def test_bytes_input(self):
        qr = Q.encode(b"abc", "L")
        self.assertEqual(decode(qr)[0], "abc")


if __name__ == "__main__":
    unittest.main()
