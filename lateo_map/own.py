"""Counting nodes that run but cannot be reached from outside.

Such a node is in nobody's peer list, so the collector cannot find it. But
it connects out, and shows up as an inbound peer of the nodes it connects
to. The operator of the map can therefore count them on nodes of their own,
with `get_connections`.

That is an administrative call. It is made only to the addresses listed as
`own_nodes` in the settings, which must be the operator's own nodes, and
never to a node found on the network.

The address of such a peer is used to tell it from the nodes on the map and
to look up a place, and is then dropped: it is not stored, written to a file
or shown. To count one node once from run to run, a keyed fingerprint of the
address is kept for as long as the node counts (an hour by default); the key
is replaced every day, and with it the old fingerprints lose their meaning.
Whoever holds the database and the key of the day could try every address
against a fingerprint; the operator sees these addresses on their own nodes
anyway. What is kept per node beside that is its place at city level, its
network operator and its height.
"""

from . import rpc
from .addresses import parse_public

MAX_CONNECTIONS = 5000


def inbound_peers(host, port, timeout=6.0, min_seconds=180, max_behind=20):
    """The peers connected to this node from outside that behave like a
    node: {public address: height}.

    A crawler (the collector of a map is one) connects, shakes hands and
    leaves, and reports no height. A node stays and follows the chain. So a
    peer counts only when it has been connected for `min_seconds` and its
    height is within `max_behind` blocks of the highest one this node sees."""
    answer = rpc._post(host, port, "/json_rpc",
                       {"jsonrpc": "2.0", "id": "0", "method": "get_connections"}, timeout)
    result = answer.get("result")
    if not isinstance(result, dict):
        raise rpc.RpcError("no result")
    connections = result.get("connections")
    connections = [c for c in (connections if isinstance(connections, list) else [])[:MAX_CONNECTIONS]
                   if isinstance(c, dict)]

    def number(connection, key):
        value = connection.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    tip = max((number(c, "height") for c in connections), default=0)
    peers = {}
    for connection in connections:
        if not connection.get("incoming"):
            continue
        if number(connection, "live_time") < min_seconds:
            continue
        height = number(connection, "height")
        if not height or tip - height > max_behind:
            continue
        ip = parse_public(connection.get("host")) if isinstance(connection.get("host"), str) else None
        if ip is not None:
            peers[str(ip)] = max(peers.get(str(ip), 0), height)
    return peers
