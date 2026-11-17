# lateo-map

Source of [map.lateo.dev](https://map.lateo.dev), a world map and directory of
the Zephyr nodes that can be found on the public network: the collector that
finds and checks the nodes, and the generator of the static site.

Independent open-source project around Zephyr. Not affiliated with
ZephyrProtocol.

## Status

In preparation. This repository holds no code yet and map.lateo.dev shows no
map yet. What follows describes what is being built.

## What it does

- Finds nodes by asking nodes it already knows for their peers, starting from
  the network's own seeds. It only contacts addresses it learned that way; it
  never scans address ranges.
- Checks each node with the P2P handshake every node answers and, where a node
  offers public RPC, with `get_version`. Known nodes are checked once per hour,
  spread evenly; addresses that never answered once per day at most.
- Shows each node on a map and in a table: its address, the kind of node (P2P,
  RPC or both), city and network operator, whether it answered, its height and
  the release line it appears to run.
- Writes plain static files. The site runs no code per visitor and makes no
  requests to other hosts.

What it does not do:

- It does not call administrative RPC methods on a node, also when a node
  leaves them open, and it never changes anything on a node.
- It does not show whether a node's RPC is restricted, nor an exact build. It
  does not ask for either.
- It does not look up host names for addresses.
- It keeps single check results for 30 days, after that only daily totals.

## Your node on the map

A node that accepts connections from the network is announced by its peers,
and that is how it gets here. The collector identifies itself in its RPC
requests with a user agent that names map.lateo.dev. If you run a node and have
a question about its entry, open an
[issue](https://github.com/loki-notfound/lateo-map/issues) or write to
loki-notfound@proton.me.

## Security and trust model

The collector sees what any peer of a node sees: the address, the ports the
node announces, its height and the list of peers it shares. The site visitor's
browser talks to map.lateo.dev only. For security problems, see
[SECURITY.md](SECURITY.md).

## Contributing

Issues and pull requests are welcome. Every commit on `main` is signed by the
maintainer, so an accepted pull request is not merged with GitHub's button: the
maintainer applies the change as a signed commit that credits you, and closes
the pull request with a reference to it. Contributions are accepted under the
repository's licence.

## Licence

BSD-3-Clause, see [LICENSE](LICENSE). The lateo name and marks are not covered
by that licence; see [TRADEMARKS.md](TRADEMARKS.md).

Location data, once the map shows it, comes from
[DB-IP](https://db-ip.com) under CC BY 4.0.
