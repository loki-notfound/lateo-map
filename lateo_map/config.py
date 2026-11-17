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
    # where discovery starts
    p2p_seeds: list = field(default_factory=list)   # "address:port"
    dns_seeds: list = field(default_factory=list)   # names that resolve to P2P nodes
    rpc_seeds: list = field(default_factory=list)   # "host:port", asked for public peers only
    # pace
    run_seconds: int = 240          # a run starts no new check after this
    gap_seconds: float = 1.0        # at least this long between two nodes
    p2p_timeout: float = 6.0
    rpc_timeout: float = 6.0
    # how often
    check_every: int = HOUR         # a node that is on the map
    check_jitter: int = 300
    retry_after: list = field(default_factory=lambda: [DAY, 2 * DAY, 4 * DAY, 7 * DAY])
    new_within: int = 6 * HOUR      # first check of a new address, spread over this
    ask_peers_every: int = 6 * HOUR  # get_public_nodes per node, and the seeds
    try_usual_rpc_every: int = DAY  # the usual RPC port on a node that announces none
    # how long
    offline_after_misses: int = 2
    drop_after: int = 2 * DAY       # unreachable this long: off the map
    forget_after: int = 30 * DAY    # never answers and no peer mentions it: deleted
    keep_checks: int = 30 * DAY
    max_addresses: int = 20000      # no new addresses beyond this


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
