"""The binary key-value format of the P2P protocol ("portable storage").

Only what the handshake needs: writing a few scalar fields and nested
sections, and reading whatever a node sends back. The reader treats its input
as hostile: every length is checked against the bytes that are left, nesting
is limited, and a malformed message raises StorageError.
"""

import struct

HEADER = struct.pack("<IIB", 0x01011101, 0x01020101, 1)

T_INT64, T_INT32, T_INT16, T_INT8 = 1, 2, 3, 4
T_UINT64, T_UINT32, T_UINT16, T_UINT8 = 5, 6, 7, 8
T_DOUBLE, T_STRING, T_BOOL, T_OBJECT, T_ARRAY = 9, 10, 11, 12, 13
FLAG_ARRAY = 0x80

MAX_DEPTH = 16

_SCALARS = {
    T_INT64: "<q", T_INT32: "<i", T_INT16: "<h", T_INT8: "<b",
    T_UINT64: "<Q", T_UINT32: "<I", T_UINT16: "<H", T_UINT8: "<B",
    T_DOUBLE: "<d",
}


class StorageError(ValueError):
    pass


# --- writing ---------------------------------------------------------------

def varint(n):
    if n <= 63:
        return struct.pack("<B", n << 2)
    if n <= 16383:
        return struct.pack("<H", (n << 2) | 1)
    if n <= 1073741823:
        return struct.pack("<I", (n << 2) | 2)
    return struct.pack("<Q", (n << 2) | 3)


def u8(v):
    return bytes([T_UINT8]) + struct.pack("<B", v)


def u16(v):
    return bytes([T_UINT16]) + struct.pack("<H", v)


def u32(v):
    return bytes([T_UINT32]) + struct.pack("<I", v)


def u64(v):
    return bytes([T_UINT64]) + struct.pack("<Q", v)


def i64(v):
    return bytes([T_INT64]) + struct.pack("<q", v)


def blob(b):
    return bytes([T_STRING]) + varint(len(b)) + b


def _section(items):
    out = varint(len(items))
    for name, value in items:
        raw = name.encode()
        out += bytes([len(raw)]) + raw + value
    return out


def obj(items):
    return bytes([T_OBJECT]) + _section(items)


def obj_array(sections):
    """An array of objects; each element is a list of (name, value) pairs."""
    out = bytes([T_OBJECT | FLAG_ARRAY]) + varint(len(sections))
    for items in sections:
        out += _section(items)
    return out


def dumps(items):
    return HEADER + _section(items)


# --- reading ---------------------------------------------------------------

class _Reader:
    def __init__(self, data):
        self.d = data
        self.i = 0

    def take(self, n):
        if n < 0 or self.i + n > len(self.d):
            raise StorageError("truncated")
        b = self.d[self.i:self.i + n]
        self.i += n
        return b

    def left(self):
        return len(self.d) - self.i

    def varint(self):
        if self.left() < 1:
            raise StorageError("truncated")
        size = 1 << (self.d[self.i] & 3)
        return int.from_bytes(self.take(size), "little") >> 2

    def scalar(self, base, depth):
        fmt = _SCALARS.get(base)
        if fmt:
            return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]
        if base == T_STRING:
            return self.take(self.varint())
        if base == T_BOOL:
            return bool(self.take(1)[0])
        if base == T_OBJECT:
            return self.section(depth + 1)
        if base == T_ARRAY:
            return self.value(depth + 1)
        raise StorageError(f"unknown type {base}")

    def value(self, depth):
        if depth > MAX_DEPTH:
            raise StorageError("nested too deep")
        t = self.take(1)[0]
        base = t & 0x7F
        if t & FLAG_ARRAY:
            n = self.varint()
            # every element takes at least one byte
            if n > self.left():
                raise StorageError("array longer than the message")
            return [self.scalar(base, depth) for _ in range(n)]
        return self.scalar(base, depth)

    def section(self, depth):
        if depth > MAX_DEPTH:
            raise StorageError("nested too deep")
        n = self.varint()
        if n > self.left():
            raise StorageError("section longer than the message")
        out = {}
        for _ in range(n):
            name = self.take(self.take(1)[0]).decode("utf-8", "replace")
            out[name] = self.value(depth)
        return out


def loads(data):
    if data[:len(HEADER)] != HEADER:
        raise StorageError("not a portable storage message")
    r = _Reader(data)
    r.take(len(HEADER))
    try:
        return r.section(0)
    except (struct.error, IndexError, RecursionError) as e:
        raise StorageError(str(e)) from None
