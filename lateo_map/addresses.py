"""Which addresses the collector may contact.

Peers tell the collector about other peers, so every address it holds comes
from a stranger. Only globally routable addresses are accepted: a peer list
must never make the collector connect to its own host, its local network or
a reserved range.
"""

import ipaddress


def parse_public(host):
    """Return the address as an ipaddress object, or None if it is not a
    literal, globally routable IPv4 or IPv6 address."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if not ip.is_global or ip.is_multicast:
        return None
    return ip


def is_public(host):
    return parse_public(host) is not None


def valid_port(port):
    return isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536
