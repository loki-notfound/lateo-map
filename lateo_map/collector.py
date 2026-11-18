"""One run of the collector.

Cron starts a run every few minutes. A run takes the addresses whose check
is due, nodes on the map first, starts one check per pause and stops
starting new ones when its time is up. Because every node gets its next
check an hour (plus or minus a few minutes) after the last one, the checks
spread over the hour by themselves.

A check is the P2P handshake and get_version on the node's RPC port: the one
it announces, or else the usual ones. Peers a node shares become addresses to try later.
"""

import hmac
import random
import secrets
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import P2P_PORT, db as store, geo, mmdb, own, p2p, release, rpc
from .addresses import is_public, valid_port
from .config import split_endpoint


@dataclass
class Outcome:
    has_p2p: bool = False
    has_rpc: bool = False
    p2p_port: int = 0
    rpc_port: int = 0
    height: int = None
    release: str = None
    pruned: bool = False
    heard: list = field(default_factory=list)   # (ip, p2p_port, rpc_port)
    asked_peers: bool = False
    tried_rpc: bool = False

    @property
    def answered(self):
        return self.has_p2p or self.has_rpc


class Probes:
    """The calls that reach the network; replaced in tests."""
    handshake = staticmethod(p2p.handshake)
    get_version = staticmethod(rpc.get_version)
    get_public_nodes = staticmethod(rpc.get_public_nodes)
    resolve = staticmethod(lambda name: [info[4][0] for info in socket.getaddrinfo(
        name, None, type=socket.SOCK_STREAM)])
    inbound_peers = staticmethod(own.inbound_peers)   # the operator's own nodes only


def check(node, config, probes, now):
    """Ask one node. Never raises for anything the node does."""
    out = Outcome(p2p_port=node["p2p_port"], rpc_port=node["rpc_port"])
    ip = node["ip"]
    hs = None
    # Without a P2P port from a peer the usual port is tried, but not hour
    # after hour on a node that is on the map with RPC only.
    if node["p2p_port"] or not (node["on_map"] and node["has_rpc"]):
        try:
            hs = probes.handshake(ip, node["p2p_port"] or P2P_PORT, config.p2p_timeout)
        except (p2p.HandshakeError, OSError):
            pass
    if hs is not None:
        out.has_p2p = True
        out.p2p_port = node["p2p_port"] or P2P_PORT
        out.height = hs.height
        out.release = release.from_p2p(hs.top_version)
        out.pruned = hs.pruned
        out.heard += [(peer.host, peer.port, peer.rpc_port) for peer in hs.peers]
    # Which RPC port, in order: the one the node announces itself, the one
    # that worked last time, the one a peer gave, the usual one (the
    # daemon's own; `usual_rpc_ports` can add 443 and 80 for nodes behind a
    # web server). A node can offer RPC without announcing it. Ports that
    # are not announced and did not work last time are tried once a day,
    # not every hour.
    announced = hs.rpc_port if hs is not None else 0
    out.rpc_port = announced or node["rpc_port"]
    sure = bool(announced) or bool(node["has_rpc"] and node["rpc_port"])
    ports = [out.rpc_port] if sure else list(dict.fromkeys(
        p for p in (out.rpc_port, *config.usual_rpc_ports) if p))
    if sure or now - node["tried_rpc"] >= config.try_usual_rpc_every:
        out.tried_rpc = not sure
        version = port = None
        for port in ports:
            try:
                version = probes.get_version(ip, port, config.rpc_timeout)
                break
            except rpc.RpcError:
                pass
        if version is not None:
            out.has_rpc = True
            out.rpc_port = port
            out.height = version.height or out.height
            out.release = release.from_rpc(version.max_hardfork, version.rpc_minor) or out.release
            if now - node["asked_peers"] >= config.ask_peers_every:
                out.asked_peers = True
                try:
                    nodes = probes.get_public_nodes(ip, out.rpc_port, config.rpc_timeout)
                except rpc.RpcError:
                    nodes = []
                out.heard += [(n.host, 0, n.rpc_port) for n in nodes]
    return out


def _hear_all(db, config, heard, now, rng):
    room = config.max_addresses - store.count(db)
    for ip, p2p_port, rpc_port in heard:
        if not is_public(ip):
            continue
        first = now + int(rng.uniform(0, config.new_within))
        if store.hear(db, ip, p2p_port, rpc_port, now, first, room > 0):
            room -= 1


def settle(db, config, node, out, now, rng):
    """Store the outcome of a check and decide when the next one is."""
    ip = node["ip"]
    if out.answered:
        nxt = now + config.check_every + int(rng.uniform(-config.check_jitter, config.check_jitter))
        store.record_answer(db, ip, now, nxt, has_p2p=out.has_p2p, has_rpc=out.has_rpc,
                            p2p_port=out.p2p_port, rpc_port=out.rpc_port, height=out.height,
                            release=out.release, pruned=out.pruned)
    else:
        last_ok = node["last_ok"]
        if node["on_map"] and last_ok is not None and now - last_ok < config.drop_after:
            # still on the map, shown as offline after a few misses
            nxt = now + config.check_every + int(rng.uniform(-config.check_jitter, config.check_jitter))
            store.record_miss(db, ip, now, nxt, True, node["misses"] + 1)
        else:
            # off the map: tried again after a day, then less and less often
            tries = 0 if node["on_map"] else node["misses"]
            if node["first_ok"] is None and tries >= len(config.retry_after):
                # it never answered: most likely an old entry of a peer list,
                # perhaps somebody else's address by now. Left alone for long.
                nxt = now + config.retry_dead_after
            else:
                nxt = now + config.retry_after[min(tries, len(config.retry_after) - 1)]
            store.record_miss(db, ip, now, nxt, False, tries + 1)
    if out.tried_rpc:
        store.mark_tried_rpc(db, ip, now)
    if out.asked_peers:
        store.mark_asked_peers(db, ip, now)
    _hear_all(db, config, out.heard, now, rng)


def sow(db, config, probes, now, rng):
    """Put the seeds in, and ask the RPC seeds for their public peers. The
    RPC seeds are sources only: they are host names and get no place on the
    map through this."""
    last = float(store.get_meta(db, "sown", 0))
    if now - last < config.ask_peers_every:
        return
    store.set_meta(db, "sown", now)
    first, later = [], []
    for seed in config.p2p_seeds:
        host, port = split_endpoint(seed, P2P_PORT)
        first.append((host, port, 0))
    for name in config.dns_seeds:
        try:
            first += [(ip, P2P_PORT, 0) for ip in probes.resolve(name)]
        except OSError:
            pass
    for seed in config.rpc_seeds:
        host, port = split_endpoint(seed, 443)
        try:
            later += [(n.host, 0, n.rpc_port) for n in probes.get_public_nodes(host, port, config.rpc_timeout)]
        except rpc.RpcError:
            pass
    room = config.max_addresses - store.count(db)
    for ip, p2p_port, rpc_port in first:
        # the seeds themselves go first in this run
        if is_public(ip) and valid_port(p2p_port):
            if store.hear(db, ip, p2p_port, rpc_port, now, now - 1, room > 0):
                room -= 1
    # what the RPC seeds list is hearsay like any peer list
    _hear_all(db, config, later, now, rng)


def run(db, config, probes=Probes, clock=time.time, sleep=time.sleep, rng=random, pool=None):
    """Returns (checks done, of which answered).

    A few checks are under way side by side, because an address that does
    not answer takes seconds to say so. The pace stays the same: a new
    check starts at most once per `gap_seconds`, and no node is asked more
    often. Only the calls to the network run in threads; the database is
    touched from here alone.
    """
    started = clock()
    sow(db, config, probes, int(started), rng)
    own_pool = pool is None
    if own_pool:
        pool = ThreadPoolExecutor(max_workers=max(1, config.side_by_side))
    under_way = {}
    done = answered = 0
    last_start = None
    try:
        while True:
            for future in [f for f in under_way if f.done()]:
                node = under_way.pop(future)
                try:
                    out = future.result()
                except Exception as e:   # a fault of ours, not of the node
                    print(f"check failed: {type(e).__name__}", file=sys.stderr)
                    out = Outcome(p2p_port=node["p2p_port"], rpc_port=node["rpc_port"])
                settle(db, config, node, out, int(clock()), rng)
                answered += out.answered
            now = clock()
            wait = 0.0 if last_start is None else config.gap_seconds - (now - last_start)
            in_time = now - started < config.run_seconds
            if in_time and wait <= 0 and len(under_way) < max(1, config.side_by_side):
                node = store.next_due(db, int(now))
                if node is not None:
                    store.claim(db, node["ip"], int(now) + config.check_every)
                    under_way[pool.submit(check, node, config, probes, int(now))] = node
                    last_start = now
                    done += 1
                    continue
                if not under_way:
                    break
            elif not in_time and not under_way:
                break
            if under_way:
                sleep(min(wait, 0.1) if wait > 0 else 0.1)
            else:
                sleep(wait)
    finally:
        if own_pool:
            pool.shutdown(wait=True)
    store.prune(db, int(clock()), config.keep_checks, config.forget_after)
    locate(db, config)
    private = count_unreachable(db, config, probes, int(clock()))
    note_totals(db, int(clock()), private)
    return done, answered


def _private_key(db, now):
    """The key of the day for the fingerprints of private nodes. A new day
    brings a new key, and the fingerprints made with the old one are dropped."""
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    if store.get_meta(db, "private_key_day") != day:
        store.set_meta(db, "private_key", secrets.token_hex(32))
        store.set_meta(db, "private_key_day", day)
        db.execute("DELETE FROM private_nodes")
    return bytes.fromhex(store.get_meta(db, "private_key"))


def _answers(ip, rpc_port, config, probes):
    """Does this address answer from outside, on the usual P2P port or on its
    RPC port (the one it announces, else the usual ones)?"""
    try:
        probes.handshake(ip, P2P_PORT, config.p2p_timeout)
        return True
    except (p2p.HandshakeError, OSError):
        pass
    for port in dict.fromkeys(p for p in (rpc_port, *config.usual_rpc_ports) if p):
        try:
            probes.get_version(ip, port, config.rpc_timeout)
            return True
        except rpc.RpcError:
            pass
    return False


def count_unreachable(db, config, probes, now, data=None):
    """Find the private nodes: peers that stay connected to the operator's
    own nodes, follow the chain, and are not on the map. Per node its place,
    network operator and height are kept, and a keyed fingerprint to count it
    once; never its address. Returns their number, or None when no own node
    answered."""
    if not config.own_nodes:
        return None
    seen, answered = {}, 0
    for entry in config.own_nodes:
        host, port = split_endpoint(entry, 17767)
        try:
            peers = probes.inbound_peers(host, port, config.rpc_timeout,
                                         config.private_min_seconds, config.private_max_behind)
            for ip, (height, rpc_port) in peers.items():
                known = seen.get(ip, (0, 0))
                seen[ip] = (max(known[0], height), rpc_port or known[1])
            answered += 1
        except rpc.RpcError:
            pass
    if not answered:
        return None
    key = _private_key(db, now)
    public = {row["ip"] for row in db.execute("SELECT ip FROM nodes WHERE on_map = 1")}
    # an address the collector knows from a peer list but has not tried yet
    # may well be public: it is left out until a check found it silent
    untried = {row["ip"] for row in db.execute("SELECT ip FROM nodes WHERE last_check IS NULL")}
    own_data = data is None
    try:
        data = data or geo.open_dir(config.geo_dir)
    except (OSError, mmdb.FormatError):
        data = None
    try:
        for ip in sorted(seen):
            tag = hmac.new(key, ip.encode(), "sha256").hexdigest()[:32]
            if ip in public:
                store.forget_private(db, tag)
                continue
            if ip in untried:
                continue
            height, rpc_port = seen[ip]
            probed = None
            if now - store.private_probed(db, tag) >= config.private_probe_every:
                # Private is tested, not assumed: does it answer from outside?
                if _answers(ip, rpc_port, config, probes):
                    # a public node after all: it joins the addresses that are
                    # checked, due at once, and is no longer counted here
                    store.forget_private(db, tag)
                    store.hear(db, ip, P2P_PORT, rpc_port, now, now, True)
                    db.execute("UPDATE nodes SET next_check = ? WHERE ip = ?", (now, ip))
                    continue
                probed = now
            try:
                place = data.place(ip) if data is not None else geo.Place()
            except (ValueError, mmdb.FormatError):
                place = geo.Place()
            store.see_private(db, tag, now, height or None, place, probed)
    finally:
        if own_data and data is not None:
            data.close()
    db.execute("DELETE FROM private_nodes WHERE last_seen < ?", (now - config.private_hold,))
    store.set_meta(db, "unreachable_observers", answered)
    store.set_meta(db, "unreachable_at", now)
    return db.execute("SELECT count(*) FROM private_nodes").fetchone()[0]


def note_totals(db, now, private):
    """Keep today's numbers, for a history later: per kind, country and
    version the most public nodes seen at one moment of the day, and the most
    private nodes."""
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    rows = db.execute("SELECT has_p2p, has_rpc, country, release FROM nodes WHERE on_map = 1").fetchall()
    totals = {}
    for row in rows:
        kind = "both" if row["has_p2p"] and row["has_rpc"] else "rpc" if row["has_rpc"] else "p2p"
        for metric, name in (("kind", kind), ("country", row["country"]), ("release", row["release"])):
            totals[(metric, name or "")] = totals.get((metric, name or ""), 0) + 1
    totals[("kind", "public")] = len(rows)
    if private is not None:
        totals[("kind", "private")] = private
    for (metric, name), value in totals.items():
        store.note_daily(db, day, metric, name, value)


def locate(db, config, data=None):
    """Look up location and network of the nodes on the map that have none
    from the current data. Returns how many were looked up."""
    own = data is None
    try:
        data = data or geo.open_dir(config.geo_dir)
    except (OSError, mmdb.FormatError) as e:
        print(f"location data unusable: {e}", file=sys.stderr)
        return 0
    if data is None:
        return 0
    try:
        ips = store.unlocated(db, data.version)
        for ip in ips:
            try:
                place = data.place(ip)
            except (ValueError, mmdb.FormatError):
                place = geo.Place()
            store.set_place(db, ip, place, data.version)
        return len(ips)
    finally:
        if own:
            data.close()
