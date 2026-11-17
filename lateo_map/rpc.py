"""The two RPC calls the collector makes to a node with public RPC.

`get_version` tells which hardforks the node's software knows and how far it
is synchronised. `get_public_nodes` lists the peers that announce public RPC
themselves. Both are meant for the public and answer on every node. Nothing
else is ever asked: no `get_info`, no administrative method.

Requests go straight to the node: no proxy, and a redirect is refused, so a
node cannot point the collector at another host. HTTPS is used on port 443,
with the certificate checked.
"""

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import USER_AGENT
from .addresses import is_public, valid_port

MAX_ANSWER = 1 << 20
MAX_PUBLIC_NODES = 2000


class RpcError(Exception):
    pass


@dataclass
class Version:
    rpc_minor: int
    max_hardfork: int
    height: int


@dataclass(frozen=True)
class PublicNode:
    host: str
    rpc_port: int


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirect,
    urllib.request.HTTPSHandler(context=ssl.create_default_context()),
)


def _url(host, port, path):
    scheme = "https" if port == 443 else "http"
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}{path}"


def _post(host, port, path, payload, timeout):
    request = urllib.request.Request(
        _url(host, port, path),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with _opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_ANSWER + 1)
    except urllib.error.HTTPError as e:
        raise RpcError(f"HTTP {e.code}") from None
    except (OSError, ValueError) as e:
        raise RpcError(type(e).__name__) from None
    if len(raw) > MAX_ANSWER:
        raise RpcError("oversized answer")
    try:
        answer = json.loads(raw)
    except ValueError:
        raise RpcError("not JSON") from None
    if not isinstance(answer, dict):
        raise RpcError("unexpected answer")
    return answer


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def get_version(host, port, timeout=6.0):
    answer = _post(host, port, "/json_rpc",
                   {"jsonrpc": "2.0", "id": "0", "method": "get_version"}, timeout)
    result = answer.get("result")
    if not isinstance(result, dict):
        raise RpcError("no result")
    forks = result.get("hard_forks")
    forks = forks if isinstance(forks, list) else []
    known = [_int(f.get("hf_version")) for f in forks if isinstance(f, dict)]
    return Version(
        rpc_minor=_int(result.get("version")) & 0xFFFF,
        max_hardfork=max(known, default=0),
        height=_int(result.get("current_height")),
    )


def get_public_nodes(host, port, timeout=6.0):
    """Peers of this node that announce public RPC, as public addresses."""
    answer = _post(host, port, "/get_public_nodes", {"gray": True, "white": True}, timeout)
    nodes, seen = [], set()
    for kind in ("white", "gray"):
        entries = answer.get(kind)
        if not isinstance(entries, list):
            continue
        for entry in entries[:MAX_PUBLIC_NODES]:
            if not isinstance(entry, dict):
                continue
            peer_host, rpc_port = entry.get("host"), entry.get("rpc_port")
            if not isinstance(peer_host, str) or not is_public(peer_host):
                continue
            if not valid_port(rpc_port) or (peer_host, rpc_port) in seen:
                continue
            seen.add((peer_host, rpc_port))
            nodes.append(PublicNode(peer_host, rpc_port))
    return nodes
