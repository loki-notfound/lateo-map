"""Counting nodes that run but cannot be reached from outside.

Such a node is in nobody's peer list, so the collector cannot find it. But
it connects out, and shows up as an inbound peer of the nodes it connects
to. The operator of the map can therefore count them on nodes of their own,
with `get_connections`.

That is an administrative call. It is made only to the addresses listed as
`own_nodes` in the settings, which must be the operator's own nodes, and
never to a node found on the network. The addresses of the inbound peers are
used to tell them from the nodes on the map and to look up a place, and are
then dropped: they are not stored, written to a file or shown. What is kept
per such node is its place at city level, its network operator and its height.
"""

from . import rpc
from .addresses import parse_public

MAX_CONNECTIONS = 5000


def inbound_peers(host, port, timeout=6.0):
    """The peers connected to this node from outside: {public address: height}."""
    answer = rpc._post(host, port, "/json_rpc",
                       {"jsonrpc": "2.0", "id": "0", "method": "get_connections"}, timeout)
    result = answer.get("result")
    if not isinstance(result, dict):
        raise rpc.RpcError("no result")
    connections = result.get("connections")
    peers = {}
    for connection in (connections if isinstance(connections, list) else [])[:MAX_CONNECTIONS]:
        if not isinstance(connection, dict) or not connection.get("incoming"):
            continue
        ip = parse_public(connection.get("host")) if isinstance(connection.get("host"), str) else None
        if ip is not None:
            height = connection.get("height")
            good = isinstance(height, int) and not isinstance(height, bool) and height > 0
            peers[str(ip)] = max(peers.get(str(ip), 0), height if good else 0)
    return peers
