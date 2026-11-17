"""One run of the collector.

Cron starts a run every few minutes. A run takes the addresses whose check
is due, nodes on the map first, starts one check per pause and stops
starting new ones when its time is up. Because every node gets its next
check an hour (plus or minus a few minutes) after the last one, the checks
spread over the hour by themselves.

A check is the P2P handshake and get_version on the node's RPC port: the one
it announces, or else the usual one. Peers a node shares become addresses to try later.
"""

import random
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import P2P_PORT, RPC_PORT, db as store, p2p, release, rpc
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
    # that worked last time, the one a peer gave, the usual one. A node can
    # offer RPC without announcing it. A port that is not announced and did
    # not work last time is tried once a day, not every hour.
    announced = hs.rpc_port if hs is not None else 0
    out.rpc_port = announced or node["rpc_port"]
    sure = bool(announced) or bool(node["has_rpc"] and node["rpc_port"])
    ports = [out.rpc_port] if sure else list(dict.fromkeys(p for p in (out.rpc_port, RPC_PORT) if p))
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
    return done, answered
