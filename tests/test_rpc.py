import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from lateo_map import USER_AGENT, release, rpc


FORKS = [{"hf_version": v, "height": h} for v, h in rpc.MAINNET_FORKS.items()]


class Handler(BaseHTTPRequestHandler):
    answers = {}
    seen = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Handler.seen.append((self.path, self.headers.get("User-Agent"), body))
        status, payload = Handler.answers.get(self.path, (404, b""))
        self.send_response(status)
        if status in (301, 302, 307):
            self.send_header("Location", payload.decode())
            payload = b""
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class RpcTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.answers, Handler.seen = {}, []

    def serve(self, path, payload, status=200):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        Handler.answers[path] = (status, raw)

    def test_get_version(self):
        self.serve("/json_rpc", {"result": {
            "version": 196621, "current_height": 909229,
            "hard_forks": FORKS,
        }})
        v = rpc.get_version("127.0.0.1", self.port)
        self.assertEqual((v.rpc_minor, v.max_hardfork, v.height), (13, 11, 909229))
        path, agent, body = Handler.seen[0]
        self.assertEqual(agent, USER_AGENT)
        self.assertEqual(json.loads(body)["method"], "get_version")

    def test_only_two_calls_exist(self):
        public = [n for n in dir(rpc) if n.startswith("get_")]
        self.assertEqual(sorted(public), ["get_public_nodes", "get_version"])

    def test_inbound_peers_of_an_own_node(self):
        from lateo_map import own
        self.serve("/json_rpc", {"result": {"connections": [
            {"incoming": True, "host": "8.8.8.8", "port": "40000", "height": 900},
            {"incoming": True, "host": "8.8.8.8", "port": "40001", "height": "x"},
            {"incoming": False, "host": "9.9.9.9", "port": "17766"},
            {"incoming": True, "host": "192.168.1.4"},
            {"incoming": True, "host": 5},
            "junk",
        ]}})
        self.assertEqual(own.inbound_peers("127.0.0.1", self.port), {"8.8.8.8": 900})
        self.assertEqual(json.loads(Handler.seen[0][2])["method"], "get_connections")
        self.serve("/json_rpc", {"error": {"code": -32601}})
        with self.assertRaises(rpc.RpcError):
            own.inbound_peers("127.0.0.1", self.port)

    def test_bad_answers(self):
        for payload in (b"<html>", b"[]", {"error": {"code": -32601}}, b"x" * (rpc.MAX_ANSWER + 10)):
            self.serve("/json_rpc", payload)
            with self.assertRaises(rpc.RpcError):
                rpc.get_version("127.0.0.1", self.port)
        self.serve("/json_rpc", b"", status=404)
        with self.assertRaises(rpc.RpcError):
            rpc.get_version("127.0.0.1", self.port)

    def test_odd_fields_do_not_crash(self):
        self.serve("/json_rpc", {"result": {"version": "x", "current_height": None,
                                            "hard_forks": FORKS[:3] + [1, {"hf_version": "9"}, {}]}})
        v = rpc.get_version("127.0.0.1", self.port)
        self.assertEqual((v.rpc_minor, v.max_hardfork, v.height), (0, 3, 0))

    def test_only_zephyr_mainnet_counts(self):
        other_coin = [{"hf_version": 1, "height": 1}, {"hf_version": 2, "height": 1009827},
                      {"hf_version": 16, "height": 2689608}]
        future = FORKS + [{"hf_version": 12, "height": 990000}]
        for forks, good in ((other_coin, False), ([], False), (FORKS[:1], False),
                            (FORKS[:3], True), (future, True)):
            self.serve("/json_rpc", {"result": {"version": 196621, "hard_forks": forks}})
            if good:
                rpc.get_version("127.0.0.1", self.port)
            else:
                with self.assertRaises(rpc.RpcError):
                    rpc.get_version("127.0.0.1", self.port)

    def test_certificate_is_checked_by_name_only(self):
        self.assertIs(rpc._opener_for("node.example"), rpc._by_name)
        self.assertIs(rpc._opener_for("8.8.8.8"), rpc._by_address)
        self.assertIs(rpc._opener_for("127.0.0.1"), rpc._by_name)

    def test_redirect_is_refused(self):
        self.serve("/elsewhere", {"result": {"version": 196621, "hard_forks": FORKS}})
        self.serve("/json_rpc", f"http://127.0.0.1:{self.port}/elsewhere".encode(), status=307)
        with self.assertRaises(rpc.RpcError):
            rpc.get_version("127.0.0.1", self.port)
        self.assertEqual([p for p, _, _ in Handler.seen], ["/json_rpc"])

    def test_public_nodes_keep_public_addresses_only(self):
        self.serve("/get_public_nodes", {"status": "OK", "white": [
            {"host": "8.8.8.8", "rpc_port": 17767, "last_seen": 1},
            {"host": "8.8.8.8", "rpc_port": 17767},
            {"host": "127.0.0.1", "rpc_port": 17767},
            {"host": "192.168.0.4", "rpc_port": 17767},
            {"host": "node.example", "rpc_port": 17767},
            {"host": "1.1.1.1", "rpc_port": 0},
            {"host": 5, "rpc_port": 17767},
            "junk",
        ], "gray": [{"host": "9.9.9.9", "rpc_port": 443}]})
        self.assertEqual(rpc.get_public_nodes("127.0.0.1", self.port),
                         [rpc.PublicNode("8.8.8.8", 17767), rpc.PublicNode("9.9.9.9", 443)])

    def test_https_only_on_443(self):
        self.assertEqual(rpc._url("8.8.8.8", 443, "/x"), "https://8.8.8.8:443/x")
        self.assertEqual(rpc._url("8.8.8.8", 17767, "/x"), "http://8.8.8.8:17767/x")
        self.assertEqual(rpc._url("2606:4700::1", 17767, "/x"), "http://[2606:4700::1]:17767/x")


class ReleaseTest(unittest.TestCase):
    def test_rpc_lines(self):
        self.assertEqual(release.from_rpc(11), "v2.3.0")
        self.assertEqual(release.from_rpc(9), "v2.1.0-v2.1.1")
        self.assertEqual(release.from_rpc(3, 12), "v1.0.0-v1.0.4")
        self.assertEqual(release.from_rpc(3, 13), "v1.1.0")
        self.assertEqual(release.from_rpc(12), "newer than v2.3.0")
        self.assertIsNone(release.from_rpc(0))

    def test_p2p_indication(self):
        self.assertEqual(release.from_p2p(11), "v2.3.0")          # the newest: nothing later exists
        self.assertEqual(release.from_p2p(12), "v2.3.0")
        self.assertEqual(release.from_p2p(10), "v2.2.0 or later")
        self.assertEqual(release.from_p2p(8), "v2.1.0 or later")
        self.assertIsNone(release.from_p2p(0))
        self.assertEqual(release.from_height(909000), "v2.3.0")
        self.assertEqual(release.from_height(520000), "v2.2.0 or later")
        self.assertIsNone(release.from_height(10))

if __name__ == "__main__":
    unittest.main()
