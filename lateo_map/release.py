"""From what a node shows to the release line it appears to run.

A node with RPC lists every hardfork its software knows; the newest one names
the release line. A node with P2P only shows the hardfork active at its own
height: the newest release line when that is the newest hardfork, a lower
bound otherwise. It is an indication; neither tells the exact build, and the
map does not try to find it.
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
    """Release line for a node that only answered the handshake, or None.

    The handshake shows the hardfork active at the node's height. On the
    newest hardfork that names the newest release line: there is nothing
    later it could be. On an older one the node may run that release or be
    a newer one still synchronising, so it is a lower bound."""
    if top_version >= NEWEST_KNOWN:
        return _BY_KNOWN_HARDFORK[NEWEST_KNOWN]
    line = _BY_ACTIVE_HARDFORK.get(top_version)
    return f"{line} or later" if line else None


# height at which each hardfork became active on mainnet
_FORK_HEIGHTS = ((536000, 11), (511000, 10), (508000, 9), (481500, 8), (433000, 7),
                 (360000, 6), (295000, 5), (294000, 4), (89300, 3))


def from_height(height):
    """Release line for a node of which only the height is known: it follows
    the chain up to there, so it runs the hardfork active at that height."""
    for start, fork in _FORK_HEIGHTS:
        if height >= start:
            return from_p2p(fork)
    return None
