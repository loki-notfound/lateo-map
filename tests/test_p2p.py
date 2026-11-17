import socket
import struct
import threading
import unittest

from lateo_map import p2p, storage as st


def ipv4_entry(ip, port, rpc_port=0):
    m_ip = struct.unpack("<I", socket.inet_aton(ip))[0]
    return [
        ("adr", st.obj([
            ("type", st.u8(p2p.ADDR_IPV4)),
            ("addr", st.obj([("m_ip", st.u32(m_ip)), ("m_port", st.u16(port))])),
        ])),
        ("id", st.u64(1)),
        ("rpc_port", st.u16(rpc_port)),
    ]


def ipv6_entry(ip, port):
    return [("adr", st.obj([
        ("type", st.u8(p2p.ADDR_IPV6)),
        ("addr", st.obj([("addr", st.blob(socket.inet_pton(socket.AF_INET6, ip))),
                         ("m_port", st.u16(port))])),
    ]))]


def answer(network_id=p2p.MAINNET_ID, peers=()):
    return st.dumps([
        ("node_data", st.obj([
            ("network_id", st.blob(network_id)),
            ("my_port", st.u32(17766)),
            ("rpc_port", st.u16(17767)),
            ("peer_id", st.u64(5)),
        ])),
        ("payload_data", st.obj([
            ("current_height", st.u64(909000)),
            ("top_version", st.u8(11)),
            ("pruning_seed", st.u32(0)),
        ])),
        ("local_peerlist_new", st.obj_array(list(peers))),
    ])


class FakeNode:
    """Answers one connection on localhost with the given packets."""

    def __init__(self, packets, read_request=True):
        self.packets, self.read_request = packets, read_request
        self.received = b""
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        conn, _ = self.sock.accept()
        with conn:
            conn.settimeout(2)
            try:
                if self.read_request:
                    self.received = conn.recv(65536)
                for packet in self.packets:
                    conn.sendall(packet)
                conn.recv(65536)
            except OSError:
                pass

    def close(self):
        self.thread.join(3)
        self.sock.close()


class ParseTest(unittest.TestCase):
    def test_fields(self):
        hs = p2p.parse_response(answer())
        self.assertEqual((hs.height, hs.top_version, hs.my_port, hs.rpc_port, hs.pruned),
                         (909000, 11, 17766, 17767, False))

    def test_other_network_is_refused(self):
        with self.assertRaises(p2p.HandshakeError):
            p2p.parse_response(answer(network_id=bytes(16)))

    def test_garbage_is_refused(self):
        for body in (b"", b"\x00" * 40, st.dumps([("node_data", st.u8(1))]), answer()[:-3]):
            with self.assertRaises(p2p.HandshakeError):
                p2p.parse_response(body)

    def test_only_public_peers_are_kept(self):
        hs = p2p.parse_response(answer(peers=[
            ipv4_entry("8.8.8.8", 17766, 17767),
            ipv4_entry("8.8.8.8", 17766),          # duplicate
            ipv4_entry("127.0.0.1", 17766),        # loopback
            ipv4_entry("10.1.2.3", 17766),         # private
            ipv4_entry("192.168.1.9", 17766),      # private
            ipv4_entry("169.254.1.1", 17766),      # link local
            ipv4_entry("100.64.0.1", 17766),       # shared address space
            ipv4_entry("224.0.0.1", 17766),        # multicast
            ipv4_entry("0.0.0.0", 17766),
            ipv4_entry("1.1.1.1", 0),              # no port
            ipv6_entry("2606:4700:4700::1111", 17766),
            ipv6_entry("::1", 17766),
            ipv6_entry("fe80::1", 17766),
            ipv6_entry("::ffff:127.0.0.1", 17766),  # loopback, mapped
            [("adr", st.obj([("type", st.u8(4)), ("addr", st.obj([("host", st.blob(b"x.onion"))]))]))],
            [("adr", st.u8(1))],
        ]))
        self.assertEqual(hs.peers, [
            p2p.Peer("8.8.8.8", 17766, 17767),
            p2p.Peer("2606:4700:4700::1111", 17766, 0),
        ])

    def test_peer_list_is_capped(self):
        entries = [ipv4_entry(f"8.8.{i // 250}.{i % 250 + 1}", 17766) for i in range(400)]
        self.assertEqual(len(p2p.parse_response(answer(peers=entries)).peers), p2p.MAX_PEERS)


class HandshakeTest(unittest.TestCase):
    def run_against(self, packets, **kw):
        node = FakeNode(packets, **kw)
        try:
            return p2p.handshake("127.0.0.1", node.port, timeout=2), node
        finally:
            node.close()

    def test_handshake_with_support_flags_question(self):
        question = p2p.pack_packet(p2p.CMD_SUPPORT_FLAGS, st.dumps([]), p2p.FLAG_REQUEST, True)
        reply = p2p.pack_packet(p2p.CMD_HANDSHAKE, answer(), p2p.FLAG_RESPONSE, False)
        hs, node = self.run_against([question, reply])
        self.assertEqual(hs.height, 909000)
        request = st.loads(node.received[p2p.LEVIN_HEADER.size:])
        self.assertEqual(request["node_data"]["my_port"], 0)
        self.assertEqual(request["node_data"]["network_id"], p2p.MAINNET_ID)

    def test_wrong_signature(self):
        with self.assertRaises(p2p.HandshakeError):
            self.run_against([b"HTTP/1.1 400 Bad Request\r\n\r\n" + bytes(40)])

    def test_oversized_packet_is_not_read(self):
        header = p2p.LEVIN_HEADER.pack(p2p.LEVIN_SIGNATURE, 2**40, 0, p2p.CMD_HANDSHAKE, 0,
                                       p2p.FLAG_RESPONSE, 1)
        with self.assertRaises(p2p.HandshakeError):
            self.run_against([header])

    def test_silent_node_times_out(self):
        node = FakeNode([])
        try:
            with self.assertRaises((p2p.HandshakeError, OSError)):
                p2p.handshake("127.0.0.1", node.port, timeout=0.5)
        finally:
            node.close()

    def test_endless_other_packets_stop(self):
        noise = p2p.pack_packet(2002, st.dumps([]), p2p.FLAG_REQUEST, False)
        with self.assertRaises(p2p.HandshakeError):
            self.run_against([noise] * 20)


if __name__ == "__main__":
    unittest.main()
