"""Command line of the collector. So far: one manual check of one node.

    python3 -m lateo_map check HOST [--p2p-port N] [--rpc-port N]

It prints what the map would know about the node. Addresses of the node's
peers are counted, not printed.
"""

import argparse
import sys

from . import P2P_PORT, RPC_PORT, p2p, release, rpc


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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="lateo_map")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="check one node by hand")
    c.add_argument("host")
    c.add_argument("--p2p-port", type=int, default=P2P_PORT, help="0 skips the handshake")
    c.add_argument("--rpc-port", type=int, default=RPC_PORT, help="0 skips the rpc calls")
    c.add_argument("--timeout", type=float, default=8.0)
    c.set_defaults(run=check)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
