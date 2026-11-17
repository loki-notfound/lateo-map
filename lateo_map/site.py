"""Writes the static site of the map from what the collector knows.

One page with the map, the table of nodes and the explanation, a JSON file
with the same nodes, and the stylesheets, icons and script copied from
`static/`. Everything a node or the location data supplied is escaped; the
page loads nothing from other hosts and carries no inline style or script.
"""

import html
import json
import os
import re
import shutil
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

WIDTH, HEIGHT = 1000, 500       # the world map, equirectangular
VIEW = "0 14 1000 400"         # the part shown: without the polar regions
CLUSTER_RADIUS = 13             # map units; places closer than this are drawn as one
BEHIND_BLOCKS = 10
BLOCK_SECONDS = 120

KINDS = {  # key: (label, description)
    "p2p": ("P2P", "takes part in the network, no public RPC found"),
    "rpc": ("RPC", "answers wallets over RPC, no P2P found"),
    "both": ("P2P and RPC", "takes part in the network and answers wallets"),
}

HEADERS = (
    "default-src 'none'; style-src 'self'; img-src 'self'; script-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def e(value):
    return html.escape(str(value), quote=True)


def kind_of(row):
    if row["has_p2p"] and row["has_rpc"]:
        return "both"
    return "rpc" if row["has_rpc"] else "p2p"


def load_nodes(db, config):
    """The nodes on the map as plain dicts, with their status worked out."""
    rows = db.execute("SELECT * FROM nodes WHERE on_map = 1").fetchall()
    # Nodes are checked at different moments of the hour, so a height is
    # compared with where the chain was when that node answered.
    live = [r for r in rows if r["height"] and r["last_ok"] and r["misses"] < config.offline_after_misses]
    newest = max(live, key=lambda r: (r["last_ok"], r["height"]), default=None)
    nodes = []
    for r in rows:
        status = "online"
        if r["misses"] >= config.offline_after_misses:
            status = "offline"
        elif newest is not None and r["height"] and r["last_ok"]:
            expected = newest["height"] - (newest["last_ok"] - r["last_ok"]) / BLOCK_SECONDS
            if expected - r["height"] > BEHIND_BLOCKS:
                status = "behind"
        nodes.append({
            "ip": r["ip"],
            "kind": kind_of(r),
            "p2p_port": r["p2p_port"] if r["has_p2p"] else None,
            "rpc_port": r["rpc_port"] if r["has_rpc"] else None,
            "status": status,
            "height": r["height"],
            "release": r["release"],
            "pruned": bool(r["pruned"]),
            "country": r["country"],
            "country_name": r["country_name"],
            "region": r["region"],
            "city": r["city"],
            "lat": r["lat"],
            "lon": r["lon"],
            "asn": r["asn"],
            "network": r["network"],
            "first_seen": r["first_ok"],
            "last_seen": r["last_ok"],
        })
    nodes.sort(key=lambda n: (n["country_name"] or "~", n["city"] or "~", n["ip"]))
    return nodes


def project(lat, lon):
    return (lon + 180.0) / 360.0 * WIDTH, (90.0 - lat) / 180.0 * HEIGHT


def radius(count):
    """How large a group of `count` nodes is drawn, in map units."""
    return 8.0 if count == 1 else 8 + min(count, 9) * 0.5


def cluster(nodes):
    """Group the nodes that would overlap on the map. Returns a list of
    {"x", "y", "nodes"}: nodes at one place first, then groups are merged
    until no two drawn markers touch."""
    groups = []
    for node in nodes:
        if node["lat"] is None or node["lon"] is None:
            continue
        x, y = project(node["lat"], node["lon"])
        for group in groups:
            if (group["x"] - x) ** 2 + (group["y"] - y) ** 2 <= CLUSTER_RADIUS ** 2:
                group["nodes"].append(node)
                break
        else:
            groups.append({"x": x, "y": y, "nodes": [node]})
    merged = True
    while merged:
        merged = False
        for i, a in enumerate(groups):
            for b in groups[i + 1:]:
                reach = radius(len(a["nodes"])) + radius(len(b["nodes"])) - 1
                if (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2 < reach ** 2:
                    na, nb = len(a["nodes"]), len(b["nodes"])
                    a["x"] = (a["x"] * na + b["x"] * nb) / (na + nb)
                    a["y"] = (a["y"] * na + b["y"] * nb) / (na + nb)
                    a["nodes"] += b["nodes"]
                    groups.remove(b)
                    merged = True
                    break
            if merged:
                break
    return groups


def row_id(ip):
    return "n-" + re.sub(r"[^0-9a-f]", "-", ip.lower())


def place_name(node):
    parts = [node["city"], node["country_name"] or node["country"]]
    return ", ".join(p for p in parts if p) or "Unknown place"


def stamp(seconds):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(seconds)) + " UTC" if seconds else ""


def _map_paths():
    with open(os.path.join(STATIC, "world-map.svg"), encoding="utf-8") as f:
        svg = f.read()
    paths = {}
    for name in ("graticule", "land", "borders"):
        found = re.search(r'<path id="lt-map-%s"[^>]*\sd="([^"]+)"' % name, svg)
        paths[name] = found.group(1) if found else ""
    return paths


SYMBOLS = """<defs>
<symbol id="m-p2p" viewBox="0 0 16 16"><circle cx="8" cy="8" r="5"/></symbol>
<symbol id="m-rpc" viewBox="0 0 16 16"><path d="M8 2L14 8L8 14L2 8Z"/></symbol>
<symbol id="m-both" viewBox="0 0 16 16"><rect x="3" y="3" width="10" height="10" rx="1.5"/></symbol>
</defs>"""


def _marker(group):
    x, y = round(group["x"], 1), round(group["y"], 1)
    members = group["nodes"]
    first = members[0]
    if len(members) == 1:
        label, _ = KINDS[first["kind"]]
        title = f"{first['ip']} · {label} · {place_name(first)} · {first['status']}"
        state = " is-offline" if first["status"] == "offline" else ""
        shape = (f'<use class="mk mk-{first["kind"]}{state}" href="#m-{first["kind"]}" '
                 f'x="{x - 8}" y="{y - 8}" width="16" height="16"/>')
    else:
        places = sorted({place_name(n) for n in members})
        where = places[0] if len(places) == 1 else f"{places[0]} and nearby"
        title = f"{len(members)} nodes · {where}"
        shape = (f'<circle class="mk-cluster" cx="{x}" cy="{y}" r="{radius(len(members)):.1f}"/>'
                 f'<text class="mk-cluster-text" x="{x}" y="{y}">{len(members)}</text>')
    ids = " ".join(row_id(n["ip"]) for n in members)
    return (f'<a class="lt-map-marker" href="#{row_id(first["ip"])}" data-rows="{e(ids)}" '
            f'aria-label="{e(title)}"><title>{e(title)}</title>'
            f'<circle class="mk-hit" cx="{x}" cy="{y}" r="12"/>{shape}</a>')


def _row(node):
    label, _ = KINDS[node["kind"]]
    ports = []
    if node["p2p_port"]:
        ports.append(f"P2P {node['p2p_port']}")
    if node["rpc_port"]:
        ports.append(f"RPC {node['rpc_port']}")
    status = {"online": ("online", "Online"), "behind": ("degraded", "Behind"),
              "offline": ("offline", "Offline")}[node["status"]]
    network = e(node["network"] or "")
    if node["asn"]:
        network += f' <span class="lt-muted">AS{node["asn"]}</span>'
    release = e(node["release"] or "")
    if node["pruned"]:
        release += ' <span class="lt-muted">pruned</span>'
    return (
        f'<tr id="{row_id(node["ip"])}" data-kind="{node["kind"]}" data-status="{node["status"]}">'
        f'<td><code>{e(node["ip"])}</code></td>'
        f'<td><span class="kind kind-{node["kind"]}"><svg class="kind-mark" viewBox="0 0 16 16" '
        f'width="14" height="14" aria-hidden="true"><use class="mk mk-{node["kind"]}" '
        f'href="#m-{node["kind"]}"/></svg>{e(label)}</span></td>'
        f'<td>{e(", ".join(ports))}</td>'
        f'<td data-sort="{e((node["country_name"] or "~") + " " + (node["city"] or ""))}">{e(place_name(node))}</td>'
        f'<td>{network}</td>'
        f'<td>{release}</td>'
        f'<td class="lt-num">{node["height"] or ""}</td>'
        f'<td><span class="lt-status lt-status-{status[0]}">{status[1]}</span></td>'
        f'<td class="lt-num" data-sort="{node["last_seen"] or 0}">{e(stamp(node["last_seen"]))}</td>'
        "</tr>"
    )


def counts(nodes):
    live = [n for n in nodes if n["status"] != "offline"]
    return {
        "nodes": len(nodes),
        "answering": len(live),
        "p2p": sum(n["kind"] == "p2p" for n in nodes),
        "rpc": sum(n["kind"] == "rpc" for n in nodes),
        "both": sum(n["kind"] == "both" for n in nodes),
        "countries": len({n["country"] for n in nodes if n["country"]}),
    }


def render(nodes, now):
    c = counts(nodes)
    paths = _map_paths()
    groups = cluster(nodes)
    # big groups first, so that single markers are drawn on top and stay clickable
    groups.sort(key=lambda g: -len(g["nodes"]))
    markers = "\n".join(_marker(g) for g in groups)
    rows = "\n".join(_row(n) for n in nodes)
    tiles = "".join(
        f'<div class="lt-stat-tile"><span class="lt-stat-label">{label}</span>'
        f'<span class="lt-stat-value">{value}</span></div>'
        for label, value in (("Nodes on the map", c["nodes"]), ("P2P only", c["p2p"]),
                             ("RPC only", c["rpc"]), ("P2P and RPC", c["both"]),
                             ("Countries", c["countries"])))
    legend = "".join(
        f'<li><span class="lt-map-legend-item"><svg class="kind-mark" viewBox="0 0 16 16" width="14" '
        f'height="14" aria-hidden="true"><use class="mk mk-{k}" href="#m-{k}"/></svg>{e(label)}</span></li>'
        for k, (label, _) in KINDS.items())
    filters = "".join(
        f'<button type="button" class="filter" data-filter="{k}" aria-pressed="false">{e(label)}</button>'
        for k, (label, _) in KINDS.items())
    generated = stamp(now)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>map.lateo.dev · Zephyr nodes on the public network</title>
<meta name="description" content="A world map and directory of the Zephyr nodes that can be found on the public network.">
<link rel="stylesheet" href="/assets/tokens.css">
<link rel="stylesheet" href="/assets/site.css">
<link rel="stylesheet" href="/assets/data.css">
<link rel="stylesheet" href="/assets/map.css">
<link rel="stylesheet" href="/assets/map-site.css">
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/assets/favicon-32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="/assets/apple-touch-icon-180.png">
<script src="/assets/map.js" defer></script>
</head>
<body>
<a class="lt-skip-link" href="#main">Skip to main content</a>
<header class="site">
  <div class="wrap">
    <a class="name" href="/"><picture>
      <source srcset="/assets/lockup-host-map-reversed.svg" media="(prefers-color-scheme: dark)">
      <img src="/assets/lockup-host-map.svg" width="165" height="28" alt="map.lateo.dev">
    </picture></a>
    <nav>
      <a href="#map">Map</a>
      <a href="#nodes">Nodes</a>
      <a href="#method">How it is made</a>
    </nav>
  </div>
</header>

<main id="main" class="wrap-main" tabindex="-1">
  <h1>Zephyr nodes on the public network</h1>
  <p class="lead">Every Zephyr node this site could find and reach, checked once
  per hour. Last update {e(generated)}.</p>

  <div class="lt-stat-grid">{tiles}</div>

  <section id="map" aria-labelledby="map-title">
    <h2 id="map-title">Map</h2>
    <div class="lt-map-frame">
      <svg class="lt-map-canvas" viewBox="{VIEW}" role="group" aria-labelledby="map-svg-title">
        <title id="map-svg-title">World map with {c["nodes"]} Zephyr nodes in {c["countries"]} countries</title>
        {SYMBOLS}
        <rect class="lt-map-water" width="{WIDTH}" height="{HEIGHT}"/>
        <path class="lt-map-graticule" d="{paths["graticule"]}"/>
        <path class="lt-map-land" fill-rule="evenodd" d="{paths["land"]}"/>
        <path class="lt-map-borders" d="{paths["borders"]}"/>
{markers}
      </svg>
      <div class="lt-map-toolbar">
        <ul class="lt-map-legend" aria-label="Legend">
          {legend}
          <li><span class="lt-map-legend-item"><svg class="kind-mark" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><use class="mk mk-p2p is-offline" href="#m-p2p"/></svg>Hollow: not answering now</span></li>
          <li><span class="lt-map-legend-item"><span class="cluster-swatch" aria-hidden="true">3</span>Several nodes close together</span></li>
        </ul>
        <span class="lt-muted lt-compact">A point is where the address block is registered, city level at best.</span>
      </div>
    </div>
    <p id="tip" class="tip" role="status" hidden></p>
  </section>

  <section id="nodes" aria-labelledby="nodes-title">
    <h2 id="nodes-title">Nodes</h2>
    <div class="controls" hidden>
      <div class="filters" role="group" aria-label="Show only">{filters}</div>
      <label class="search">Search <input type="search" id="q" autocomplete="off" spellcheck="false" placeholder="address, place or network"></label>
      <button type="button" id="reset" hidden>Show all</button>
      <span id="shown" class="lt-muted" role="status"></span>
    </div>
    <div class="lt-table-scroll" tabindex="0" role="region" aria-labelledby="nodes-caption">
      <table class="lt-table" id="node-table">
        <caption id="nodes-caption">{c["nodes"]} nodes, {c["answering"]} answering now</caption>
        <thead>
          <tr>
            <th scope="col">Address</th>
            <th scope="col">Kind</th>
            <th scope="col">Ports</th>
            <th scope="col">Place</th>
            <th scope="col">Network</th>
            <th scope="col">Release</th>
            <th scope="col" class="lt-num">Height</th>
            <th scope="col">Status</th>
            <th scope="col" class="lt-num">Last answer</th>
          </tr>
        </thead>
        <tbody>
{rows}
        </tbody>
      </table>
    </div>
    <p class="lt-muted">The same list as data: <a href="/nodes.json">nodes.json</a>.</p>
  </section>

  <section id="method" aria-labelledby="method-title">
    <h2 id="method-title">How this map is made</h2>
    <ul>
      <li><strong>Finding nodes.</strong> A node tells its peers about other nodes.
      The collector starts at the network's seed nodes, asks each node it reaches
      for the peers it shares, and tries those in turn. It only contacts addresses
      it learned this way and never scans address ranges.</li>
      <li><strong>Checking a node.</strong> Once per hour: the handshake every node
      answers on its P2P port, and <code>get_version</code> on its RPC port, the one
      it announces or else 17767, 443 and 80. An address that does not answer is
      tried again after a day, then less and less often.</li>
      <li><strong>What is shown.</strong> The address and ports, the kind of node,
      its height, and the release line its answers point to, which is an estimate.
      A P2P-only node shows a lower bound. A node that stays silent for 48 hours
      leaves the map.</li>
      <li><strong>What is not asked or shown.</strong> No administrative calls, no
      exact build, and nothing about how a node's RPC is configured. No host names.</li>
      <li><strong>Place and network.</strong> From
      <a href="https://db-ip.com">IP Geolocation by DB-IP</a> (CC BY 4.0). It
      describes address blocks, so a point is often a provider's data centre.</li>
    </ul>
    <h3>Your node on the map</h3>
    <p>A node that accepts connections is announced by its peers, and that is how
    it gets here. The collector's RPC requests carry a user agent that names
    map.lateo.dev. Questions about an entry: open an
    <a href="https://github.com/loki-notfound/lateo-map/issues">issue</a> or write
    to <a href="mailto:loki-notfound@proton.me">loki-notfound@proton.me</a>.</p>
  </section>
</main>

<footer class="site">
  <div class="wrap">
    <p>Source: <a href="https://github.com/loki-notfound/lateo-map">github.com/loki-notfound/lateo-map</a> (BSD-3-Clause).
    Location data: <a href="https://db-ip.com">IP Geolocation by DB-IP</a>.
    Not affiliated with ZephyrProtocol.</p>
  </div>
</footer>
</body>
</html>
"""


def data(nodes, now):
    return {"generated": int(now), "counts": counts(nodes),
            "source": "https://github.com/loki-notfound/lateo-map",
            "location_data": "IP Geolocation by DB-IP, https://db-ip.com, CC BY 4.0",
            "nodes": nodes}


def _write(path, text):
    """Replace a file in one step, so that a visitor never gets half a page."""
    directory = os.path.dirname(path)
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".new-")
    with os.fdopen(handle, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def build(db, config, now=None):
    """Write the site into `config.site_dir`. Returns the number of nodes."""
    now = time.time() if now is None else now
    out = config.site_dir
    assets = os.path.join(out, "assets")
    os.makedirs(assets, exist_ok=True)
    source = os.path.join(STATIC, "assets")
    for name in sorted(os.listdir(source)):
        target = os.path.join(assets, name)
        origin = os.path.join(source, name)
        if not os.path.exists(target) or os.path.getmtime(target) < os.path.getmtime(origin) \
                or os.path.getsize(target) != os.path.getsize(origin):
            shutil.copyfile(origin, target + ".new")
            os.chmod(target + ".new", 0o644)
            os.replace(target + ".new", target)
    for name in ("robots.txt", ".htaccess"):
        shutil.copyfile(os.path.join(STATIC, name), os.path.join(out, name + ".new"))
        os.chmod(os.path.join(out, name + ".new"), 0o644)
        os.replace(os.path.join(out, name + ".new"), os.path.join(out, name))
    nodes = load_nodes(db, config)
    _write(os.path.join(out, "nodes.json"), json.dumps(data(nodes, now), indent=1) + "\n")
    _write(os.path.join(out, "index.html"), render(nodes, now))
    return len(nodes)
