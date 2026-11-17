"""Reader for the MaxMind DB file format, in which DB-IP publishes its free
location and network data.

Only looking up an address. The file is a binary search tree over the bits
of an address, followed by a data section the tree points into.
"""

import ipaddress
import mmap
import struct

MARKER = b"\xab\xcd\xefMaxMind.com"
MAX_DEPTH = 32

(T_POINTER, T_STRING, T_DOUBLE, T_BYTES, T_UINT16, T_UINT32, T_MAP, T_INT32,
 T_UINT64, T_UINT128, T_ARRAY, T_CACHE, T_END, T_BOOL, T_FLOAT) = range(1, 16)


class FormatError(ValueError):
    pass


class Reader:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.buf = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            start = self.buf.rfind(MARKER)
            if start < 0:
                raise FormatError("no metadata")
            self.data_start = 0
            meta, _ = self._decode(start + len(MARKER), 0)
            self.node_count = meta["node_count"]
            self.record_size = meta["record_size"]
            self.ip_version = meta["ip_version"]
            self.build_epoch = meta.get("build_epoch", 0)
            self.database_type = meta.get("database_type", "")
            if self.record_size not in (24, 28, 32) or self.ip_version not in (4, 6):
                raise FormatError("unsupported tree")
            self.node_bytes = self.record_size // 4
            tree_size = self.node_count * self.node_bytes
            self.data_start = tree_size + 16
            if self.data_start > start:
                raise FormatError("tree larger than the file")
            # where the IPv4 addresses start inside an IPv6 tree
            self.ipv4_start = 0
            if self.ip_version == 6:
                node = 0
                for _ in range(96):
                    if node >= self.node_count:
                        break
                    node = self._record(node, 0)
                self.ipv4_start = node
        except (KeyError, TypeError, IndexError, struct.error) as e:
            self.close()
            raise FormatError(f"damaged file: {e}") from None
        except FormatError:
            self.close()
            raise

    def close(self):
        self.buf.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- the tree ----------------------------------------------------------

    def _record(self, node, side):
        base = node * self.node_bytes
        b = self.buf
        if self.record_size == 24:
            o = base + 3 * side
            return int.from_bytes(b[o:o + 3], "big")
        if self.record_size == 28:
            middle = b[base + 3]
            if side == 0:
                return ((middle & 0xF0) << 20) | int.from_bytes(b[base:base + 3], "big")
            return ((middle & 0x0F) << 24) | int.from_bytes(b[base + 4:base + 7], "big")
        o = base + 4 * side
        return int.from_bytes(b[o:o + 4], "big")

    def lookup(self, address):
        """The record for an address (a dict, usually), or None."""
        ip = ipaddress.ip_address(address)
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if ip.version == 4:
            node, bits, value = self.ipv4_start, 32, int(ip)
        elif self.ip_version == 4:
            return None
        else:
            node, bits, value = 0, 128, int(ip)
        try:
            for i in range(bits - 1, -1, -1):
                if node >= self.node_count:
                    break
                node = self._record(node, (value >> i) & 1)
            if node <= self.node_count:
                return None   # no record for this address
            position = node - self.node_count - 16 + self.data_start
            return self._decode(position, 0)[0]
        except (IndexError, struct.error, RecursionError) as e:
            raise FormatError(f"damaged file: {e}") from None

    # --- the data section ----------------------------------------------------

    def _decode(self, pos, depth):
        """Returns (value, position after it)."""
        if depth > MAX_DEPTH:
            raise FormatError("nested too deep")
        b = self.buf
        control = b[pos]
        pos += 1
        kind = control >> 5
        if kind == T_POINTER:
            size = (control >> 3) & 3
            raw = int.from_bytes(b[pos:pos + size + 1], "big")
            pos += size + 1
            if size == 0:
                pointer = ((control & 7) << 8) | raw
            elif size == 1:
                pointer = 2048 + (((control & 7) << 16) | raw)
            elif size == 2:
                pointer = 526336 + (((control & 7) << 24) | raw)
            else:
                pointer = raw
            value, _ = self._decode(self.data_start + pointer, depth + 1)
            return value, pos
        if kind == 0:
            kind = 7 + b[pos]
            pos += 1
        size = control & 0x1F
        if size == 29:
            size = 29 + b[pos]
            pos += 1
        elif size == 30:
            size = 285 + int.from_bytes(b[pos:pos + 2], "big")
            pos += 2
        elif size == 31:
            size = 65821 + int.from_bytes(b[pos:pos + 3], "big")
            pos += 3
        if kind == T_MAP:
            out = {}
            for _ in range(size):
                key, pos = self._decode(pos, depth + 1)
                out[key], pos = self._decode(pos, depth + 1)
            return out, pos
        if kind == T_ARRAY:
            out = []
            for _ in range(size):
                item, pos = self._decode(pos, depth + 1)
                out.append(item)
            return out, pos
        if kind == T_BOOL:
            return bool(size), pos
        if pos + size > len(b):
            raise FormatError("value runs past the end")
        raw = b[pos:pos + size]
        pos += size
        if kind == T_STRING:
            return raw.decode("utf-8", "replace"), pos
        if kind == T_BYTES:
            return bytes(raw), pos
        if kind in (T_UINT16, T_UINT32, T_UINT64, T_UINT128):
            return int.from_bytes(raw, "big"), pos
        if kind == T_INT32:
            return int.from_bytes(raw, "big", signed=True), pos
        if kind == T_DOUBLE and size == 8:
            return struct.unpack(">d", raw)[0], pos
        if kind == T_FLOAT and size == 4:
            return struct.unpack(">f", raw)[0], pos
        raise FormatError(f"unknown type {kind}")
