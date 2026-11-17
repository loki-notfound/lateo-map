"""Writes small files in the MaxMind DB format, for the tests only."""

import ipaddress
import struct

MARKER = b"\xab\xcd\xefMaxMind.com"


def _head(kind, size):
    if kind <= 7:
        first, extra = kind << 5, b""
    else:
        first, extra = 0, bytes([kind - 7])
    if size < 29:
        return bytes([first | size]) + extra
    if size < 285:
        return bytes([first | 29]) + extra + bytes([size - 29])
    return bytes([first | 30]) + extra + (size - 285).to_bytes(2, "big")


def encode(value):
    if isinstance(value, bool):
        return _head(14, int(value))
    if isinstance(value, str):
        raw = value.encode()
        return _head(2, len(raw)) + raw
    if isinstance(value, float):
        return _head(3, 8) + struct.pack(">d", value)
    if isinstance(value, int):
        raw = value.to_bytes(4, "big").lstrip(b"\x00")
        return _head(6, len(raw)) + raw
    if isinstance(value, dict):
        return _head(7, len(value)) + b"".join(encode(k) + encode(v) for k, v in value.items())
    if isinstance(value, list):
        return _head(11, len(value)) + b"".join(encode(v) for v in value)
    raise TypeError(type(value))


def build(networks, record_size=24, ip_version=4, database_type="Test-City"):
    """networks: {"8.8.8.0/24": record, ...} -> bytes of a database."""
    data, offsets = b"", {}
    for net, record in networks.items():
        offsets[net] = len(data)
        data += encode(record)
    # a trie of [left, right]; a leaf is ("data", offset), nothing is None
    root = [None, None]
    width = 32 if ip_version == 4 else 128
    for net in networks:
        network = ipaddress.ip_network(net)
        value, length = int(network.network_address), network.prefixlen
        if ip_version == 6 and network.version == 4:
            length += 96
        node = root
        for i in range(length):
            bit = (value >> (width - 1 - i)) & 1
            if i == length - 1:
                node[bit] = ("data", offsets[net])
            else:
                if node[bit] is None:
                    node[bit] = [None, None]
                node = node[bit]
    nodes = []

    def number(node):
        index = len(nodes)
        nodes.append(node)
        for child in node:
            if isinstance(child, list):
                number(child)
        return index

    number(root)
    index_of = {id(n): i for i, n in enumerate(nodes)}
    count = len(nodes)

    def record(child):
        if child is None:
            return count
        if isinstance(child, list):
            return index_of[id(child)]
        return count + 16 + child[1]

    tree = b""
    for node in nodes:
        left, right = record(node[0]), record(node[1])
        if record_size == 24:
            tree += left.to_bytes(3, "big") + right.to_bytes(3, "big")
        elif record_size == 28:
            tree += (left & 0xFFFFFF).to_bytes(3, "big")
            tree += bytes([((left >> 24) << 4) | (right >> 24)])
            tree += (right & 0xFFFFFF).to_bytes(3, "big")
        else:
            tree += left.to_bytes(4, "big") + right.to_bytes(4, "big")
    meta = encode({"node_count": count, "record_size": record_size, "ip_version": ip_version,
                   "database_type": database_type, "build_epoch": 1_700_000_000})
    return tree + bytes(16) + data + MARKER + meta
