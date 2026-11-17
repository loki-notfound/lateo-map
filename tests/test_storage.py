import unittest

from lateo_map import storage as st


class StorageTest(unittest.TestCase):
    def test_round_trip(self):
        data = st.dumps([
            ("a", st.u64(2**40)),
            ("b", st.blob(b"\x00\x01")),
            ("c", st.obj([("d", st.u32(7)), ("e", st.u16(9)), ("f", st.u8(1))])),
            ("g", st.obj_array([[("h", st.i64(-3))], [("h", st.i64(4))]])),
        ])
        self.assertEqual(st.loads(data), {
            "a": 2**40, "b": b"\x00\x01", "c": {"d": 7, "e": 9, "f": 1},
            "g": [{"h": -3}, {"h": 4}],
        })

    def test_varint_sizes(self):
        for n in (0, 63, 64, 16383, 16384, 1073741823, 1073741824):
            payload = st.dumps([("s", st.blob(b""))])
            self.assertEqual(st.loads(payload), {"s": b""})
            self.assertEqual(st._Reader(st.varint(n)).varint(), n)

    def test_rejects_wrong_header(self):
        with self.assertRaises(st.StorageError):
            st.loads(b"\x00" * 20)

    def test_rejects_every_truncation(self):
        data = st.dumps([("c", st.obj([("d", st.u32(7)), ("s", st.blob(b"abcdef"))]))])
        for cut in range(len(st.HEADER), len(data)):
            with self.assertRaises(st.StorageError, msg=cut):
                st.loads(data[:cut])

    def test_rejects_huge_counts(self):
        huge = st.varint(2**40)
        with self.assertRaises(st.StorageError):
            st.loads(st.HEADER + huge)
        with self.assertRaises(st.StorageError):
            st.loads(st.HEADER + st.varint(1) + b"\x01a" + bytes([st.T_UINT8 | st.FLAG_ARRAY]) + huge)
        with self.assertRaises(st.StorageError):
            st.loads(st.HEADER + st.varint(1) + b"\x01a" + bytes([st.T_STRING]) + huge)

    def test_rejects_deep_nesting(self):
        value = st.u8(1)
        for _ in range(st.MAX_DEPTH + 5):
            value = st.obj([("x", value)])
        with self.assertRaises(st.StorageError):
            st.loads(st.dumps([("x", value)]))

    def test_rejects_unknown_type(self):
        with self.assertRaises(st.StorageError):
            st.loads(st.HEADER + st.varint(1) + b"\x01a" + bytes([0x7F]))


if __name__ == "__main__":
    unittest.main()
