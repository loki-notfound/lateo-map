"""Command line of the collector.

    python3 -m lateo_map run --config FILE
        One run, as cron starts it. A second run at the same time exits.

    python3 -m lateo_map site --config FILE
        Write the site into `site_dir` from what the database holds, without
        contacting any node. A run does this by itself at its end.

    python3 -m lateo_map status --config FILE
        Counts of what the database holds. No addresses.

    python3 -m lateo_map geo-fetch --config FILE
        Download this month's DB-IP Lite files into `geo_dir`. Once a month.

    python3 -m lateo_map check HOST [--p2p-port N] [--rpc-port N]
        One node by hand. Prints what the map would know about it; the
        node's peers are counted, not printed.
"""

import argparse
import fcntl
import sys

from . import P2P_PORT, RPC_PORT, collector, config, db, geo, p2p, release, rpc, site


def check(args):
    found = False
    if args.p2p_port:
        try:
            hs = p2p.handshake(args.host, args.p2p_port, args.timeout)
            found = True
            print(f"p2p  height {hs.height}, active hardfork {hs.top_version}, "
                  f"{release.from_p2p(hs.top_version) or 'unknown release'}")
            print(f"p2p  announces p2p port {hs.my_port or 'none'}, "
                  f"rpc port {hs.rpc_port or 'none'}, pruned {'yes' if hs.pruned else 'no'}")
            with_rpc = sum(1 for peer in hs.peers if peer.rpc_port)
            print(f"p2p  shares {len(hs.peers)} public peers, {with_rpc} of them with rpc")
        except (p2p.HandshakeError, OSError) as e:
            print(f"p2p  no answer ({e or type(e).__name__})")
    if args.rpc_port:
        try:
            v = rpc.get_version(args.host, args.rpc_port, args.timeout)
            found = True
            print(f"rpc  height {v.height}, knows hardfork {v.max_hardfork}, "
                  f"{release.from_rpc(v.max_hardfork, v.rpc_minor) or 'unknown release'}")
            nodes = rpc.get_public_nodes(args.host, args.rpc_port, args.timeout)
            print(f"rpc  lists {len(nodes)} public rpc peers")
        except rpc.RpcError as e:
            print(f"rpc  no answer ({e})")
    return 0 if found else 1


def run(args):
    settings = config.load(args.config)
    with open(settings.database + ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another run is busy")
            return 0
        database = db.connect(settings.database)
        try:
            done, answered = collector.run(database, settings)
            print(f"{done} checks, {answered} answered")
            if settings.site_dir:
                print(f"site written, {site.build(database, settings)} nodes")
        finally:
            database.close()
    return 0


def status(args):
    database = db.connect(config.load(args.config).database)
    row = database.execute(
        "SELECT count(*) AS known, coalesce(sum(on_map), 0) AS on_map,"
        " coalesce(sum(on_map AND misses = 0), 0) AS answering,"
        " coalesce(sum(on_map AND has_p2p AND NOT has_rpc), 0) AS p2p_only,"
        " coalesce(sum(on_map AND has_rpc AND NOT has_p2p), 0) AS rpc_only,"
        " coalesce(sum(on_map AND has_p2p AND has_rpc), 0) AS both,"
        " coalesce(sum(on_map AND lat IS NOT NULL), 0) AS located,"
        " coalesce(sum(last_check IS NULL), 0) AS untried FROM nodes").fetchone()
    database.close()
    for key in row.keys():
        print(f"{key.replace('_', ' '):10} {row[key]}")
    return 0


def write_site(args):
    settings = config.load(args.config)
    if not settings.site_dir:
        print("the setting 'site_dir' is missing")
        return 1
    database = db.connect(settings.database)
    try:
        collector.locate(database, settings)
        print(f"site written, {site.build(database, settings)} nodes")
    finally:
        database.close()
    return 0


def geo_fetch(args):
    settings = config.load(args.config)
    if not settings.geo_dir:
        print("the setting 'geo_dir' is missing")
        return 1
    for kind, month in geo.fetch(settings.geo_dir).items():
        print(f"{kind}: {month}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="lateo_map")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="check one node by hand")
    c.add_argument("host")
    c.add_argument("--p2p-port", type=int, default=P2P_PORT, help="0 skips the handshake")
    c.add_argument("--rpc-port", type=int, default=RPC_PORT, help="0 skips the rpc calls")
    c.add_argument("--timeout", type=float, default=8.0)
    c.set_defaults(run=check)
    for name, function, text in (("run", run, "one run of the collector"),
                                 ("status", status, "counts of what is known"),
                                 ("site", write_site, "write the site"),
                                 ("geo-fetch", geo_fetch, "download the location data")):
        c = sub.add_parser(name, help=text)
        c.add_argument("--config", required=True)
        c.set_defaults(run=function)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
