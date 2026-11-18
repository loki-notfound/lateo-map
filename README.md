# lateo-map

Source of [map.lateo.dev](https://map.lateo.dev), a world map and directory of
Zephyr nodes: the public nodes found on the network, and private nodes seen
connecting to it. It holds the collector that finds and checks the nodes, and
the generator of the static site.

Independent open-source project around Zephyr. Not affiliated with
ZephyrProtocol.

## Status

First version, running on map.lateo.dev. Target: Zephyr `v2.3.0`.

## What it does

- Finds nodes by asking nodes it already knows for their peers, starting from
  the network's own seeds. It only contacts addresses it learned that way, or
  that are connected to the operator's own nodes; it never scans address
  ranges.
- Checks each node with the P2P handshake every node answers and with
  `get_version` on its RPC port: the one it announces, or else 17767. Known
  nodes are checked once per hour, spread evenly; an address that
  does not answer is tried again after a day, then less and less often; one
  that never answered is left alone for a month after four tries.
- Shows each node on a map and in a table: its address and ports, the kind of
  node (P2P, RPC or both), city and network operator, whether it answered, its
  height and the release line it appears to run.
- Writes plain static files. The site runs no code per visitor on the server
  and makes no requests to other hosts. A small script lets a visitor filter
  by kind and version, sort, and zoom the map.

What it does not do:

- It does not call administrative RPC methods on other people's nodes, also
  when a node leaves them open, and it never changes anything on a node. The
  one administrative call it knows, `get_connections`, goes only to nodes of
  the operator listed as `own_nodes`: it finds the private nodes among the
  peers that stay connected there, the ones that run but cannot be reached
  from outside. Of those the map shows the place, the network and the
  height. Their addresses are not shown or kept; a keyed fingerprint with a
  key of the day counts each node once ([lateo_map/own.py](lateo_map/own.py)).
  Once a day such a peer is tried from outside; one that answers is a public
  node and goes onto the map like any other.
- It does not show whether a node's RPC is restricted, nor an exact build. It
  does not ask for either.
- It does not look up host names for addresses.
- It keeps single check results for 30 days, and beside them the highest
  numbers of each day (per kind, country and version).

## Install and use

Python 3.11 or later; no packages beyond the standard library.

    git clone https://github.com/loki-notfound/lateo-map.git
    cd lateo-map
    cp config.example.toml /path/outside/the/webroot/map.toml

Fill in the copy: the database path, the directory for the location data,
the webroot and the seeds. Then:

    python3 -m lateo_map geo-fetch --config /path/outside/the/webroot/map.toml
    python3 -m lateo_map run --config /path/outside/the/webroot/map.toml

`geo-fetch` downloads this month's DB-IP Lite files; repeat it once a month.
`run` is one round: it checks the addresses that are due, for at most four
minutes, and writes the site. Start it every five minutes, for example from
cron; a second run at the same time exits at once:

    */5 * * * * cd /path/of/lateo-map && python3 -m lateo_map run --config /path/outside/the/webroot/map.toml >> /path/outside/the/webroot/run.log 2>&1
    30 3 * * * tail -n 2000 /path/outside/the/webroot/run.log > /path/outside/the/webroot/run.log.new && mv /path/outside/the/webroot/run.log.new /path/outside/the/webroot/run.log

The second line keeps the log short.

Other commands: `status` prints counts of what the database holds, `site`
writes the site without contacting a node, `check HOST` asks one node by hand.

If you run your own map with these files, replace the mark files and the
site's name with your own ([TRADEMARKS.md](TRADEMARKS.md)), and the contact
in the page text.

## Configuration

Settings are read from the TOML file; [config.example.toml](config.example.toml)
shows the ones without a default.

| Setting | Default | Meaning |
|---|---|---|
| `database` | none | Path of the SQLite file. |
| `geo_dir` | empty | Directory of the DB-IP files; empty means no locations. |
| `site_dir` | empty | Webroot the site is written into; empty means no site. |
| `p2p_seeds`, `dns_seeds`, `rpc_seeds` | empty | Where discovery starts. |
| `own_nodes` | empty | Your own nodes with unrestricted RPC on a private address, for the private nodes. |
| `gap_seconds` | `1.0` | Shortest time between the starts of two checks. |
| `side_by_side` | `4` | Checks under way at the same time. |
| `run_seconds` | `240` | A run starts no new check after this. |
| `check_every` | `3600` | Seconds between checks of a node on the map. |
| `usual_rpc_ports` | `[17767]` | Tried on a node that announces no RPC port; 443 and 80 can be added. |
| `drop_after` | `172800` | A node silent this long leaves the map. |
| `keep_checks` | `2592000` | Single check results are kept this long. |

The rest is in [lateo_map/config.py](lateo_map/config.py).

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

## Development

    python3 -m unittest discover -s tests -t .

The tests use a pretend network, a pretend clock and small data files they
build themselves; they contact nothing but localhost. `tests/test_browser.py`
loads the page in Chromium or Chrome when one is installed (it is skipped
otherwise) and plays a visitor: switches, version, pick, zoom, hover, the
refresh of an open page. The same tests run for every pull request.

The layout:

- `lateo_map/p2p.py`, `storage.py`: the P2P handshake and its binary format.
- `lateo_map/rpc.py`: the two RPC calls. `own.py`: the private nodes, seen on
  your own nodes.
- `lateo_map/collector.py`, `db.py`: one run, and what is remembered.
- `lateo_map/geo.py`, `mmdb.py`: location and network of an address.
- `lateo_map/site.py`, `static/`: the site.

## Contributing

Issues and pull requests are welcome. Every commit on `main` is signed by the
maintainer, so an accepted pull request is not merged with GitHub's button: the
maintainer applies the change as a signed commit that credits you, and closes
the pull request with a reference to it. Contributions are accepted under the
repository's licence.

## Licence

BSD-3-Clause, see [LICENSE](LICENSE). The lateo name and marks are not covered
by that licence; see [TRADEMARKS.md](TRADEMARKS.md).

Location data comes from [DB-IP](https://db-ip.com) under CC BY 4.0; the world
outlines are Natural Earth, public domain.
