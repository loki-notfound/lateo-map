import random
import threading
import unittest
from concurrent.futures import Future

from lateo_map import collector, config, db as store, p2p, rpc

HOUR, DAY = 3600, 86400
A, B, C = "8.8.8.8", "9.9.9.9", "1.1.1.1"


class Net:
    """A pretend network: which addresses answer, and with what."""

    def __init__(self):
        self.p2p = {}       # ip -> Handshake
        self.rpc = {}       # (ip, port) -> Version
        self.public = {}    # (host, port) -> [PublicNode]
        self.names = {}
        self.inbound = {}   # (host, port) of an own node -> addresses of its inbound peers
        self.calls = []

    def handshake(self, ip, port, timeout):
        self.calls.append(("p2p", ip, port))
        if ip not in self.p2p:
            raise OSError("refused")
        return self.p2p[ip]

    def get_version(self, ip, port, timeout):
        self.calls.append(("version", ip, port))
        if (ip, port) not in self.rpc:
            raise rpc.RpcError("refused")
        return self.rpc[(ip, port)]

    def get_public_nodes(self, host, port, timeout):
        self.calls.append(("public", host, port))
        if (host, port) not in self.public:
            raise rpc.RpcError("refused")
        return self.public[(host, port)]

    def inbound_peers(self, host, port, timeout):
        self.calls.append(("inbound", host, port))
        if (host, port) not in self.inbound:
            raise rpc.RpcError("refused")
        return dict(self.inbound[(host, port)])

    def resolve(self, name):
        if name not in self.names:
            raise OSError("no such name")
        return self.names[name]


class Inline:
    """Runs a check at once, so that a test has one order of events."""

    def submit(self, fn, *args):
        future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as e:
            future.set_exception(e)
        return future


def hs(rpc_port=0, peers=(), height=900, top=11):
    return p2p.Handshake(height=height, top_version=top, my_port=17766, rpc_port=rpc_port,
                         pruned=False, peers=list(peers))


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self.db = store.connect(":memory:")
        self.net = Net()
        self.cfg = config.Config(database=":memory:", p2p_seeds=[f"{A}:17766"], check_jitter=0)
        self.now = 1_000_000.0
        self.slept = []
        self.rng = random.Random(1)

    def tearDown(self):
        self.db.close()

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def run_once(self):
        return collector.run(self.db, self.cfg, self.net, self.clock, self.sleep, self.rng, Inline())

    def node(self, ip):
        return self.db.execute("SELECT * FROM nodes WHERE ip = ?", (ip,)).fetchone()

    def test_seed_is_checked_and_its_peers_are_stored_for_later(self):
        self.net.p2p[A] = hs(peers=[p2p.Peer(B, 17766, 17767), p2p.Peer(C, 18000)])
        self.assertEqual(self.run_once(), (1, 1))
        a = self.node(A)
        self.assertEqual((a["on_map"], a["has_p2p"], a["has_rpc"], a["release"]),
                         (1, 1, 0, "v2.3.0"))
        self.assertEqual(a["next_check"], 1_000_000 + HOUR)
        b, c = self.node(B), self.node(C)
        self.assertEqual((b["on_map"], b["p2p_port"], b["rpc_port"]), (0, 17766, 17767))
        self.assertEqual(c["p2p_port"], 18000)
        for new in (b, c):
            self.assertTrue(1_000_000 <= new["next_check"] <= 1_000_000 + self.cfg.new_within)
        # the new addresses were not contacted in this run
        self.assertEqual({c[1] for c in self.net.calls}, {A})

    def test_node_with_both_uses_the_port_it_announces(self):
        self.net.p2p[A] = hs(rpc_port=17767)
        self.net.rpc[(A, 17767)] = rpc.Version(13, 11, 905)
        self.net.public[(A, 17767)] = [rpc.PublicNode(B, 443)]
        self.run_once()
        a = self.node(A)
        self.assertEqual((a["has_p2p"], a["has_rpc"], a["rpc_port"], a["height"], a["release"]),
                         (1, 1, 17767, 905, "v2.3.0"))
        self.assertEqual(self.node(B)["rpc_port"], 443)
        self.assertEqual([c[0] for c in self.net.calls], ["p2p", "version", "public"])

    def test_public_peers_are_asked_for_only_every_six_hours(self):
        self.net.p2p[A] = hs(rpc_port=17767)
        self.net.rpc[(A, 17767)] = rpc.Version(13, 11, 905)
        self.net.public[(A, 17767)] = []
        for _ in range(7):
            self.run_once()
            self.now += HOUR
        self.assertEqual([c[0] for c in self.net.calls].count("public"), 2)
        self.assertEqual([c[0] for c in self.net.calls].count("version"), 7)

    def test_rpc_only_node(self):
        self.cfg.p2p_seeds = []
        self.cfg.rpc_seeds = ["node.example:443"]
        self.net.public[("node.example", 443)] = [rpc.PublicNode(B, 17767)]
        self.net.rpc[(B, 17767)] = rpc.Version(13, 10, 800)
        self.run_once()
        self.now = self.node(B)["next_check"]
        self.run_once()
        b = self.node(B)
        self.assertEqual((b["on_map"], b["has_p2p"], b["has_rpc"], b["release"]), (1, 0, 1, "v2.2.0"))
        # the seed itself is a source, not a node
        self.assertIsNone(self.node("node.example"))
        # once it is known as RPC only, the usual P2P port is not tried every hour
        self.net.calls.clear()
        self.now += HOUR
        self.run_once()
        self.assertEqual(self.net.calls, [("version", B, 17767)])

    def test_nodes_on_the_map_go_before_a_backlog_of_silent_addresses(self):
        self.cfg.p2p_seeds = []
        for i in range(1, 30):
            store.hear(self.db, f"8.8.4.{i}", 17766, 0, 0, 5)       # long overdue, silent
        store.hear(self.db, A, 17766, 0, 0, 999_999)
        self.db.execute("UPDATE nodes SET on_map = 1, has_p2p = 1, last_ok = 999_000 WHERE ip = ?", (A,))
        self.net.p2p[A] = hs()
        self.cfg.run_seconds = 3
        self.run_once()
        self.assertEqual(self.net.calls[0], ("p2p", A, 17766))

    def test_seeds_go_first_and_seed_lists_are_spread(self):
        self.cfg.rpc_seeds = ["node.example:443"]
        self.net.public[("node.example", 443)] = [rpc.PublicNode(f"8.8.4.{i}", 17767) for i in range(1, 30)]
        self.net.p2p[A] = hs()
        self.cfg.run_seconds = 1
        self.run_once()
        self.assertEqual(self.net.calls[:2], [("public", "node.example", 443), ("p2p", A, 17766)])

    def test_unreachable_nodes_are_counted_on_own_nodes_only(self):
        self.net.p2p[A] = hs()
        self.cfg.own_nodes = ["10.0.0.5:17769", "10.0.0.6:17769"]
        self.net.inbound[("10.0.0.5", 17769)] = {A: 900, B: 900, C: 880}    # A is on the map
        self.net.inbound[("10.0.0.6", 17769)] = {C: 890, "8.8.4.4": 0}      # C is seen by both
        self.run_once()
        self.assertEqual(store.get_meta(self.db, "unreachable"), "3")
        self.assertEqual(store.get_meta(self.db, "unreachable_observers"), "2")
        import json
        kept = json.loads(store.get_meta(self.db, "unreachable_nodes"))
        self.assertEqual(sorted(str(k["height"]) for k in kept), ["890", "900", "None"])
        self.assertEqual(set(kept[0]), {"country", "country_name", "city", "lat", "lon", "asn", "network", "height"})
        # the admin call went to the own nodes and to nobody else
        self.assertEqual({c[1] for c in self.net.calls if c[0] == "inbound"}, {"10.0.0.5", "10.0.0.6"})
        # and no address of an unreachable node was kept
        self.assertEqual([r["ip"] for r in self.db.execute("SELECT ip FROM nodes")], [A])
        for row in self.db.execute("SELECT value FROM meta"):
            for address in (B, C, "8.8.4.4"):
                self.assertNotIn(address, row["value"])

    def test_an_address_not_tried_yet_is_not_called_private(self):
        self.net.p2p[A] = hs(peers=[p2p.Peer(B, 17766)])      # B is known from a peer list, untried
        self.cfg.own_nodes = ["10.0.0.5:17769"]
        self.net.inbound[("10.0.0.5", 17769)] = {B: 900, C: 900}
        self.run_once()
        self.assertEqual(store.get_meta(self.db, "unreachable"), "1")
        # once B was tried and stayed silent it counts
        self.now = self.node(B)["next_check"]
        self.run_once()
        self.assertEqual(store.get_meta(self.db, "unreachable"), "2")

    def test_no_own_node_or_no_answer_means_no_count(self):
        self.run_once()
        self.assertIsNone(store.get_meta(self.db, "unreachable"))
        self.assertEqual([c for c in self.net.calls if c[0] == "inbound"], [])
        self.cfg.own_nodes = ["10.0.0.5:17769"]
        self.now += HOUR
        self.run_once()
        self.assertIsNone(store.get_meta(self.db, "unreachable"))

    def test_never_calls_anything_else(self):
        self.assertEqual(sorted(n for n in vars(collector.Probes) if not n.startswith("_")),
                         ["get_public_nodes", "get_version", "handshake", "inbound_peers", "resolve"])

    def test_offline_then_off_the_map_then_rare_retries_then_back(self):
        self.net.p2p[A] = hs()
        self.run_once()
        del self.net.p2p[A]
        for hour in range(1, 48):
            self.now = 1_000_000 + hour * HOUR
            self.run_once()
            a = self.node(A)
            self.assertEqual((a["on_map"], a["misses"]), (1, hour))
        self.now = 1_000_000 + 48 * HOUR
        self.run_once()
        a = self.node(A)
        self.assertEqual((a["on_map"], a["misses"]), (0, 1))
        waits = []
        for _ in range(5):
            before = self.now
            self.now = self.node(A)["next_check"]
            waits.append(round((self.now - before) / DAY))
            self.run_once()
        self.assertEqual(waits, [1, 2, 4, 7, 7])
        self.net.p2p[A] = hs()
        self.now = self.node(A)["next_check"]
        self.run_once()
        a = self.node(A)
        self.assertEqual((a["on_map"], a["misses"]), (1, 0))

    def test_address_that_never_answers_backs_off_and_is_forgotten(self):
        self.run_once()
        self.assertEqual((self.node(A)["on_map"], self.node(A)["misses"]), (0, 1))
        self.assertEqual(self.node(A)["next_check"], 1_000_000 + DAY)
        self.cfg.p2p_seeds = []
        self.now += 31 * DAY
        self.db.execute("UPDATE nodes SET next_check = ?", (self.now + DAY,))
        self.run_once()
        self.assertIsNone(self.node(A))

    def test_pause_between_nodes_and_time_limit(self):
        self.cfg.p2p_seeds = [f"8.8.4.{i}:17766" for i in range(1, 41)]
        self.cfg.run_seconds = 10
        self.cfg.gap_seconds = 1.0
        done, answered = self.run_once()
        self.assertEqual((done, answered), (10, 0))
        self.assertEqual(self.slept, [1.0] * 10)

    def test_checks_side_by_side_keep_the_pace_and_ask_each_node_once(self):
        ips = [f"8.8.4.{i}" for i in range(1, 13)]
        self.cfg.p2p_seeds = [f"{ip}:17766" for ip in ips]
        self.cfg.gap_seconds = 0.05
        self.cfg.side_by_side = 4
        self.cfg.try_usual_rpc_every = 10**9
        lock, state, starts = threading.Lock(), {"now": 0, "most": 0}, []
        import time as real

        def slow_handshake(ip, port, timeout):
            with lock:
                starts.append((real.monotonic(), ip))
                state["now"] += 1
                state["most"] = max(state["most"], state["now"])
            real.sleep(0.4)
            with lock:
                state["now"] -= 1
            raise OSError("silent")

        self.net.handshake = slow_handshake
        self.net.get_version = lambda *a: (_ for _ in ()).throw(rpc.RpcError("silent"))
        for ip in ips:
            store.hear(self.db, ip, 17766, 0, int(real.time()), 0)
            self.db.execute("UPDATE nodes SET tried_rpc = ? WHERE ip = ?", (int(real.time()), ip))
        self.cfg.p2p_seeds = []
        began = real.monotonic()
        done, answered = collector.run(self.db, self.cfg, self.net)
        took = real.monotonic() - began
        self.assertEqual((done, answered), (12, 0))
        self.assertEqual(sorted(ip for _, ip in starts), sorted(ips))       # each once
        self.assertEqual(state["most"], 4)                                  # never more than four
        self.assertLess(took, 12 * 0.4 * 0.6)                               # faster than one by one
        gaps = [b[0] - a[0] for a, b in zip(starts, starts[1:])]
        self.assertGreaterEqual(min(gaps), 0.04)                            # the pause holds
        self.assertEqual(self.db.execute("SELECT count(*) FROM nodes WHERE misses = 1").fetchone()[0], 12)

    def test_a_fault_in_a_check_does_not_stop_the_run(self):
        self.net.p2p[A] = hs()
        self.net.handshake = lambda *a: (_ for _ in ()).throw(RuntimeError("bug"))
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.run_once(), (1, 0))
        self.assertIn("RuntimeError", err.getvalue())
        self.assertEqual(self.node(A)["misses"], 1)

    def test_private_addresses_from_seeds_and_peers_are_ignored(self):
        self.cfg.p2p_seeds = ["127.0.0.1:17766", "10.0.0.5:17766", f"{A}:17766"]
        self.cfg.dns_seeds = ["seeds.example"]
        self.net.names["seeds.example"] = ["192.168.1.1", B]
        self.net.p2p[A] = hs()
        self.net.p2p[B] = hs()
        self.run_once()
        self.assertEqual(sorted(r["ip"] for r in self.db.execute("SELECT ip FROM nodes")), [A, B])
        self.assertEqual({c[1] for c in self.net.calls}, {A, B})

    def test_no_new_addresses_beyond_the_limit(self):
        self.cfg.max_addresses = 3
        self.net.p2p[A] = hs(peers=[p2p.Peer(f"8.8.4.{i}", 17766) for i in range(1, 30)])
        self.run_once()
        self.assertEqual(store.count(self.db), 3)

    def test_a_working_node_keeps_its_p2p_port_when_peers_say_otherwise(self):
        self.net.p2p[A] = hs(peers=[p2p.Peer(A, 9999)])
        self.run_once()
        self.assertEqual(self.node(A)["p2p_port"], 17766)

    def test_usual_rpc_port_is_found_without_announcement(self):
        self.net.p2p[A] = hs()                       # announces no RPC port
        self.net.rpc[(A, 17767)] = rpc.Version(13, 11, 905)
        self.net.public[(A, 17767)] = []
        self.run_once()
        a = self.node(A)
        self.assertEqual((a["has_p2p"], a["has_rpc"], a["rpc_port"], a["release"]),
                         (1, 1, 17767, "v2.3.0"))
        # a port that worked is asked again every hour
        self.net.calls.clear()
        self.now += HOUR
        self.run_once()
        self.assertIn(("version", A, 17767), self.net.calls)

    def test_usual_rpc_port_without_answer_is_tried_once_a_day(self):
        self.net.p2p[A] = hs()
        for _ in range(30):
            self.run_once()
            self.now += HOUR
        tries = [c for c in self.net.calls if c[0] == "version"]
        self.assertEqual(tries, [("version", A, 17767)] * 2)
        self.assertEqual(self.node(A)["has_rpc"], 0)

    def test_port_from_hearsay_then_the_usual_one(self):
        self.net.p2p[A] = hs()
        store.hear(self.db, A, 17766, 8888, 1_000_000, 1_000_000)
        self.run_once()
        self.assertEqual([c for c in self.net.calls if c[0] == "version"],
                         [("version", A, 8888), ("version", A, 17767)])

    def test_rpc_behind_a_web_server_is_found_on_443_when_that_port_is_added(self):
        self.cfg.usual_rpc_ports = [17767, 443, 80]
        self.net.p2p[A] = hs()
        self.net.rpc[(A, 443)] = rpc.Version(13, 11, 905)
        self.net.public[(A, 443)] = []
        self.run_once()
        self.assertEqual((self.node(A)["has_rpc"], self.node(A)["rpc_port"]), (1, 443))
        self.net.calls.clear()
        self.now += HOUR
        self.run_once()
        self.assertEqual([c for c in self.net.calls if c[0] == "version"], [("version", A, 443)])

    def test_announced_port_goes_first_and_is_the_only_one_tried(self):
        self.net.p2p[A] = hs(rpc_port=18089)
        self.run_once()
        self.assertEqual([c for c in self.net.calls if c[0] == "version"], [("version", A, 18089)])

    def test_checks_are_kept_thirty_days(self):
        self.net.p2p[A] = hs()
        self.run_once()
        self.now += 29 * DAY
        self.run_once()
        self.assertEqual(self.db.execute("SELECT count(*) FROM checks").fetchone()[0], 2)
        self.now += 2 * DAY
        self.run_once()
        self.assertEqual(self.db.execute("SELECT count(*) FROM checks").fetchone()[0], 2)


class ConfigTest(unittest.TestCase):
    def test_endpoints(self):
        self.assertEqual(config.split_endpoint("8.8.8.8:1", 5), ("8.8.8.8", 1))
        self.assertEqual(config.split_endpoint("8.8.8.8", 5), ("8.8.8.8", 5))
        self.assertEqual(config.split_endpoint("[2606:4700::1]:7", 5), ("2606:4700::1", 7))
        self.assertEqual(config.split_endpoint("2606:4700::1", 5), ("2606:4700::1", 5))

    def test_example_loads_and_unknown_settings_are_refused(self):
        import os, tempfile
        self.assertEqual(config.load("config.example.toml").rpc_seeds, ["node.example:443"])
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('database = "x"\nspeed = 9\n')
        try:
            with self.assertRaises(ValueError):
                config.load(f.name)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
