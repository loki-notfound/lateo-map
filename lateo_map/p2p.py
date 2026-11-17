"""The P2P handshake: the one exchange every Zephyr node answers.

The collector connects, sends a handshake that announces no listening port
(so the node does not add the collector to its own peer list), reads the
answer and disconnects. The answer says that this is a Zephyr mainnet node,
how far it is synchronised, which ports it announces, and which peers it
shares.
"""

import os
import socket
import struct
import time
from dataclasses import dataclass, field

from . import storage
from .addresses import parse_public, valid_port

LEVIN_SIGNATURE = 0x0101010101012101
LEVIN_HEADER = struct.Struct("<QQBIiII")
LEVIN_VERSION = 1
FLAG_REQUEST, FLAG_RESPONSE = 1, 2
CMD_HANDSHAKE = 1001
CMD_SUPPORT_FLAGS = 1007

# A full answer with 250 peers is a few tens of kilobytes.
MAX_BODY = 1 << 20
MAX_PEERS = 250
MAX_PACKETS = 6

MAINNET_ID = bytes.fromhex("1220f173630244611731008216b1b210")

ADDR_IPV4, ADDR_IPV6 = 1, 2


class HandshakeError(Exception):
    pass


@dataclass(frozen=True)
class Peer:
    host: str
    port: int
    rpc_port: int = 0


@dataclass
class Handshake:
    height: int
    top_version: int
    my_port: int
    rpc_port: int
    pruned: bool
    peers: list = field(default_factory=list)


def request_body():
    return storage.dumps([
        ("node_data", storage.obj([
            ("network_id", storage.blob(MAINNET_ID)),
            ("peer_id", storage.u64(int.from_bytes(os.urandom(8), "little"))),
            ("my_port", storage.u32(0)),
            ("support_flags", storage.u32(1)),
        ])),
        ("payload_data", storage.obj([
            ("current_height", storage.u64(0)),
            ("cumulative_difficulty", storage.u64(0)),
            ("top_id", storage.blob(bytes(32))),
        ])),
    ])


def pack_packet(command, body, flags, expect_response):
    return LEVIN_HEADER.pack(
        LEVIN_SIGNATURE, len(body), 1 if expect_response else 0,
        command, 0, flags, LEVIN_VERSION,
    ) + body


def _recv(sock, n, deadline):
    buf = bytearray()
    while len(buf) < n:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HandshakeError("timeout")
        sock.settimeout(remaining)
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise HandshakeError("connection closed")
        buf += chunk
    return bytes(buf)


def _read_packet(sock, deadline):
    signature, size, _expect, command, _code, flags, _version = LEVIN_HEADER.unpack(
        _recv(sock, LEVIN_HEADER.size, deadline))
    if signature != LEVIN_SIGNATURE:
        raise HandshakeError("not a levin peer")
    if size > MAX_BODY:
        raise HandshakeError("oversized packet")
    return command, flags, _recv(sock, size, deadline)


def _int(section, name, default=0):
    v = section.get(name, default)
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def parse_peers(entries):
    """Public IPv4 and IPv6 peers from the shared peer list. Everything else
    (other networks, private or reserved addresses, bad ports) is dropped."""
    peers, seen = [], set()
    if not isinstance(entries, list):
        return peers
    for entry in entries[:MAX_PEERS]:
        if not isinstance(entry, dict):
            continue
        adr = entry.get("adr")
        addr = adr.get("addr") if isinstance(adr, dict) else None
        if not isinstance(addr, dict):
            continue
        kind = _int(adr, "type")
        if kind == ADDR_IPV4:
            raw = struct.pack("<I", _int(addr, "m_ip") & 0xFFFFFFFF)
            host = socket.inet_ntop(socket.AF_INET, raw)
        elif kind == ADDR_IPV6 and isinstance(addr.get("addr"), bytes) and len(addr["addr"]) == 16:
            host = socket.inet_ntop(socket.AF_INET6, addr["addr"])
        else:
            continue
        ip = parse_public(host)
        port = _int(addr, "m_port")
        if ip is None or not valid_port(port):
            continue
        key = (str(ip), port)
        if key in seen:
            continue
        seen.add(key)
        rpc_port = _int(entry, "rpc_port")
        peers.append(Peer(str(ip), port, rpc_port if valid_port(rpc_port) else 0))
    return peers


def parse_response(body):
    try:
        root = storage.loads(body)
    except storage.StorageError as e:
        raise HandshakeError(f"malformed answer: {e}") from None
    node = root.get("node_data")
    payload = root.get("payload_data")
    if not isinstance(node, dict) or not isinstance(payload, dict):
        raise HandshakeError("incomplete answer")
    if node.get("network_id") != MAINNET_ID:
        raise HandshakeError("another network")
    my_port = _int(node, "my_port")
    rpc_port = _int(node, "rpc_port")
    return Handshake(
        height=_int(payload, "current_height"),
        top_version=_int(payload, "top_version"),
        my_port=my_port if valid_port(my_port) else 0,
        rpc_port=rpc_port if valid_port(rpc_port) else 0,
        pruned=_int(payload, "pruning_seed") != 0,
        peers=parse_peers(root.get("local_peerlist_new")),
    )


def handshake(host, port, timeout=8.0):
    """Shake hands with one node. The timeout covers the whole exchange.
    Raises HandshakeError or OSError when the node gives no usable answer."""
    deadline = time.monotonic() + timeout
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(pack_packet(CMD_HANDSHAKE, request_body(), FLAG_REQUEST, True))
        for _ in range(MAX_PACKETS):
            command, flags, body = _read_packet(sock, deadline)
            if command == CMD_HANDSHAKE and flags & FLAG_RESPONSE:
                return parse_response(body)
            if command == CMD_SUPPORT_FLAGS and flags & FLAG_REQUEST:
                answer = storage.dumps([("support_flags", storage.u32(1))])
                sock.sendall(pack_packet(CMD_SUPPORT_FLAGS, answer, FLAG_RESPONSE, False))
    raise HandshakeError("no handshake answer")
