"""From what a node shows to the release line it appears to run.

A node with RPC lists every hardfork its software knows; the newest one names
the release line. A node with P2P only shows the hardfork active at its own
height, which gives a lower bound. Neither tells the exact build, and the map
does not try to find it.
"""

# newest hardfork known to the software -> release line
_BY_KNOWN_HARDFORK = {
    5: "v1.2.0",
    6: "v2.0.0-v2.0.1",
    7: "v2.0.2",
    9: "v2.1.0-v2.1.1",
    10: "v2.2.0",
    11: "v2.3.0",
}

# hardfork active at the node's height -> oldest release that has it
_BY_ACTIVE_HARDFORK = {
    3: "v1.0.0", 4: "v1.2.0", 5: "v1.2.0", 6: "v2.0.0", 7: "v2.0.2",
    8: "v2.1.0", 9: "v2.1.0", 10: "v2.2.0", 11: "v2.3.0",
}

NEWEST_KNOWN = max(_BY_KNOWN_HARDFORK)


def from_rpc(max_hardfork, rpc_minor=13):
    """Release line of a node that answered get_version, or None."""
    if max_hardfork == 3:
        return "v1.0.0-v1.0.4" if rpc_minor <= 12 else "v1.1.0"
    if max_hardfork > NEWEST_KNOWN:
        return f"newer than {_BY_KNOWN_HARDFORK[NEWEST_KNOWN]}"
    return _BY_KNOWN_HARDFORK.get(max_hardfork)


def from_p2p(top_version):
    """Lower bound for a node that only answered the handshake, or None."""
    if top_version > NEWEST_KNOWN:
        top_version = NEWEST_KNOWN
    line = _BY_ACTIVE_HARDFORK.get(top_version)
    return f"{line} or later" if line else None
