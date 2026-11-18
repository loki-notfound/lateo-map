"""Settings of the collector, read from a TOML file kept outside the
repository. Every value has a default except the database path and the seeds.
"""

import tomllib
from dataclasses import dataclass, field, fields

HOUR = 3600
DAY = 24 * HOUR


@dataclass
class Config:
    database: str = ""
    geo_dir: str = ""               # where the DB-IP files are kept; empty: no locations
    site_dir: str = ""              # the webroot the site is written into; empty: no site
    site_url: str = ""              # its public address, for link previews; empty: none
    # where discovery starts
    p2p_seeds: list = field(default_factory=list)   # "address:port"
    dns_seeds: list = field(default_factory=list)   # names that resolve to P2P nodes
    rpc_seeds: list = field(default_factory=list)   # "host:port", asked for public peers only
    # Nodes reached by a host name instead of an address, such as one behind
    # a proxy with several hosts: {name, port, note, link}. They are asked for
    # get_version like any RPC node and stand in the table; having no single
    # place, they get no marker on the map.
    named_nodes: list = field(default_factory=list)
    # the operator's own nodes with unrestricted RPC on a private address: see own.py
    own_nodes: list = field(default_factory=list)   # "host:port"
    # pace
    run_seconds: int = 240          # a run starts no new check after this
    gap_seconds: float = 1.0        # at least this long between the starts of two checks
    side_by_side: int = 4           # checks under way at the same time, at most
    p2p_timeout: float = 5.0
    rpc_timeout: float = 5.0
    # how often
    check_every: int = HOUR         # a node that is on the map
    check_jitter: int = 300
    retry_after: list = field(default_factory=lambda: [DAY, 2 * DAY, 4 * DAY, 7 * DAY])
    retry_dead_after: int = 30 * DAY  # an address that never answered, once those tries are used up
    new_within: int = 6 * HOUR      # first check of a new address, spread over this
    ask_peers_every: int = 6 * HOUR  # get_public_nodes per node, and the seeds
    try_usual_rpc_every: int = DAY  # the usual RPC port on a node that announces none
    usual_rpc_ports: list = field(default_factory=lambda: [17767])
    # how long
    offline_after_misses: int = 2
    drop_after: int = 2 * DAY       # unreachable this long: off the map
    forget_after: int = 30 * DAY    # never answers and no peer mentions it: deleted
    keep_checks: int = 30 * DAY
    max_addresses: int = 20000      # no new addresses beyond this
    # private nodes (see own.py)
    private_min_seconds: int = 180  # connected at least this long to an own node
    private_max_behind: int = 20    # and at most this many blocks behind
    private_hold: int = HOUR        # counted this long after it was last seen
    private_probe_every: int = DAY  # how often such a peer is tried from outside


def load(path):
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
    config = Config(**raw)
    if not config.database:
        raise ValueError("the setting 'database' is missing")
    for entry in config.named_nodes:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]:
            raise ValueError("named_nodes: every entry needs a name")
        unknown = set(entry) - {"name", "port", "note", "link"}
        if unknown:
            raise ValueError(f"named_nodes: unknown keys: {', '.join(sorted(unknown))}")
        if not isinstance(entry.get("port", 443), int):
            raise ValueError("named_nodes: the port is a number")
        link = entry.get("link", "")
        if link and not (isinstance(link, str) and link.startswith("https://")):
            raise ValueError("named_nodes: a link starts with https://")
    return config


def split_endpoint(text, default_port):
    """'host', 'host:port' or '[v6]:port' -> (host, port)."""
    text = text.strip()
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        return host, int(rest.lstrip(":") or default_port)
    if text.count(":") == 1:
        host, port = text.split(":")
        return host, int(port)
    return text, default_port
