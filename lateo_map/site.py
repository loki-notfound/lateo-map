"""Writes the static site of the map from what the collector knows.

One page with the map, the table of nodes and the explanation, a JSON file
with the same nodes, and the stylesheets, icons and script copied from
`static/`. Everything a node or the location data supplied is escaped; the
page loads nothing from other hosts and carries no inline style or script.
"""

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import time

from . import db as store, release

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

WIDTH, HEIGHT = 1000, 500       # the world map, equirectangular
VIEW = "0 14 1000 400"         # the part shown: without the polar regions
CLUSTER_RADIUS = 13             # map units; places closer than this are drawn as one
BEHIND_BLOCKS = 10
BLOCK_SECONDS = 120

KINDS = {  # key: label
    "p2p": "P2P",
    "rpc": "RPC",
    "both": "P2P and RPC",
    "private": "Private",
}

HEADERS = (
    "default-src 'none'; style-src 'self'; img-src 'self'; script-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def asset(name):
    """The address of a file under assets/, with a mark of its content. A
    changed file gets a new address, so that neither a browser nor a cache in
    front of the site goes on serving the old one beside a new page."""
    with open(os.path.join(STATIC, "assets", name), "rb") as f:
        mark = hashlib.sha256(f.read()).hexdigest()[:10]
    return f"/assets/{name}?v={mark}"


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


def load_named(db, config):
    """The nodes reached by a host name: in the table, under their name, with
    the note from the settings where the place would be, and without a spot
    on the map."""
    named = []
    for entry in config.named_nodes:
        row = store.named(db, entry["name"])
        if row is None or row["first_ok"] is None:
            continue        # never answered: not shown
        if row["last_ok"] is None or (row["last_check"] or 0) - row["last_ok"] >= config.drop_after:
            continue        # silent for as long as any node is kept
        named.append({
            "ip": None, "name": row["name"], "link": entry.get("link") or None,
            "note": entry.get("note") or "Worldwide",
            "id": "d-" + re.sub(r"[^0-9a-z]", "-", row["name"].lower()),
            "kind": "rpc", "p2p_port": None, "rpc_port": row["port"],
            "status": "offline" if row["misses"] >= config.offline_after_misses else "online",
            "height": row["height"], "release": row["release"], "pruned": False,
            "country": None, "country_name": None, "region": None, "city": None,
            "lat": None, "lon": None, "asn": row["asn"], "network": row["network"],
            "first_seen": row["first_ok"], "last_seen": row["last_ok"],
        })
    return named


def load_hidden(db, config, now, fresh=2 * 3600):
    """The private nodes (they run but cannot be reached from outside; see
    own.py) as the collector knows them: place, network and height, no
    address. None when they are not counted or the count is old."""
    at = store.get_meta(db, "unreachable_at")
    if at is None or now - float(at) > fresh:
        return None
    hidden = []
    rows = store.private_nodes(db, float(at) - config.private_hold)
    for index, row in enumerate(rows, 1):
        hidden.append({
            "ip": None, "id": f"h-{index}", "kind": "private", "p2p_port": None, "rpc_port": None,
            "status": "online", "height": row["height"],
            "release": release.from_height(row["height"]) if row["height"] else None,
            "pruned": False, "country": row["country"], "country_name": row["country_name"],
            "region": None, "city": row["city"], "lat": row["lat"], "lon": row["lon"],
            "asn": row["asn"], "network": row["network"],
            "first_seen": None, "last_seen": row["last_seen"],
        })
    return hidden


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


def row_id(node):
    if node.get("id"):
        return node["id"]
    return "n-" + re.sub(r"[^0-9a-f]", "-", node["ip"].lower())


def title_of(node):
    if node["kind"] == "private":
        return f"Private node · {place_name(node)}"
    return f"{node.get('name') or node['ip']} · {KINDS[node['kind']]} · {place_name(node)} · {node['status']}"


def place_name(node):
    if node.get("note"):
        return node["note"]
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


# the drawing inside a button at the map: its middle is the middle of the button
ICON = ('<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="%s" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>')

SYMBOLS = """<defs>
<symbol id="lt-marker-p2p" viewBox="0 0 16 16"><circle cx="8" cy="8" r="5"/></symbol>
<symbol id="lt-marker-rpc" viewBox="0 0 16 16"><path d="M8 2L14 8L8 14L2 8Z"/></symbol>
<symbol id="lt-marker-both" viewBox="0 0 16 16"><rect x="3" y="3" width="10" height="10" rx="1.5"/></symbol>
<symbol id="lt-marker-private" viewBox="0 0 16 16"><path d="M8 2.5L14 13H2Z"/></symbol>
</defs>"""


def _marker(group):
    x, y = round(group["x"], 1), round(group["y"], 1)
    members = group["nodes"]
    first = members[0]
    if len(members) == 1:
        title = title_of(first)
        state = " is-offline" if first["status"] == "offline" else ""
        shape = (f'<use class="lt-map-kind lt-map-kind-{first["kind"]}{state}" href="#lt-marker-{first["kind"]}" '
                 f'x="{x - 8}" y="{y - 8}" width="16" height="16"/>')
    else:
        places = sorted({place_name(n) for n in members})
        where = places[0] if len(places) == 1 else f"{places[0]} and nearby"
        title = f"{len(members)} nodes · {where}"
        shape = (f'<circle class="lt-map-group" cx="{x}" cy="{y}" r="{radius(len(members)):.1f}"/>'
                 f'<text class="lt-map-group-text" x="{x}" y="{y}">{len(members)}</text>')
    ids = " ".join(row_id(n) for n in members)
    return (f'<a class="lt-map-marker" href="#{row_id(first)}" data-rows="{e(ids)}" '
            f'aria-label="{e(title)}"><title>{e(title)}</title>'
            f'<circle class="lt-map-hit" cx="{x}" cy="{y}" r="12"/>{shape}</a>')


def _row(node):
    label = KINDS[node["kind"]]
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
    version = e(node["release"] or "")
    if node["pruned"]:
        version += ' <span class="lt-muted">pruned</span>'
    if node.get("name"):
        address = f'<code>{e(node["name"])}</code>'
        if node.get("link"):
            address = f'<a href="{e(node["link"])}">{address}</a>'
    elif node["ip"]:
        address = f'<code>{e(node["ip"])}</code>'
    else:
        address = '<span class="lt-muted">not shown</span>'
    spot = ""
    if node["lat"] is not None and node["lon"] is not None:
        x, y = project(node["lat"], node["lon"])
        spot = f' data-x="{x:.1f}" data-y="{y:.1f}"'
    return (
        f'<tr id="{row_id(node)}"{" hidden" if node["kind"] == "private" else ""} '
        f'data-kind="{node["kind"]}" data-status="{node["status"]}" '
        f'data-release="{e(node["release"] or "")}" data-place="{e(place_name(node))}" '
        f'data-title="{e(title_of(node))}"{spot}>'
        f'<td>{address}</td>'
        f'<td><span class="kind kind-{node["kind"]}"><svg class="lt-map-kind-mark" viewBox="0 0 16 16" '
        f'width="14" height="14" aria-hidden="true"><use class="lt-map-kind lt-map-kind-{node["kind"]}" '
        f'href="#lt-marker-{node["kind"]}"/></svg>{e(label)}</span></td>'
        f'<td>{e(", ".join(ports))}</td>'
        f'<td data-sort="{e((node["country_name"] or "~") + " " + (node["city"] or ""))}">{e(place_name(node))}</td>'
        f'<td>{network}</td>'
        f'<td>{version}</td>'
        f'<td class="lt-num">{node["height"] or ""}</td>'
        f'<td><span class="lt-status lt-status-{status[0]}">{status[1]}</span></td>'
        f'<td class="lt-num" data-sort="{node["last_seen"] or 0}">{e(stamp(node["last_seen"]))}</td>'
        "</tr>"
    )


def counts(nodes):
    reachable = [n for n in nodes if n["kind"] != "private"]
    live = [n for n in reachable if n["status"] != "offline"]
    return {
        "nodes": len(reachable),
        "answering": len(live),
        "p2p": sum(n["kind"] == "p2p" for n in nodes),
        "rpc": sum(n["kind"] == "rpc" for n in nodes),
        "both": sum(n["kind"] == "both" for n in nodes),
        "countries": len({n["country"] for n in reachable if n["country"]}),
    }


HIDDEN_NOTE = """<li><strong>Private nodes</strong> connect out but accept no connections, for
      example behind a home router. They are seen as peers that stay connected to
      this site's own nodes, so there are more than shown here. Their place,
      network and height are kept; their address is not, only a short-lived
      fingerprint that counts each node once.</li>
      """


NAMED_NOTE = """<li><strong>Nodes by name</strong> are reached through a host name that
      answers from many places at once. They have no single place, so they stand
      in the table and not on the map.</li>
      """


def _mark(kind, extra=""):
    return (f'<svg class="lt-map-kind-mark" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">'
            f'<use class="lt-map-kind lt-map-kind-{kind}{extra}" href="#lt-marker-{kind}"/></svg>')


def _preview(site_url, summary):
    """What a messenger or a search engine shows for a link to the page."""
    if not site_url:
        return ""
    base = site_url.rstrip("/")
    return f"""<meta property="og:type" content="website">
<meta property="og:site_name" content="lateo">
<meta property="og:title" content="Zephyr node map">
<meta property="og:description" content="{e(summary)}">
<meta property="og:url" content="{e(base)}/">
<meta property="og:image" content="{e(base)}{asset("og.png")}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
"""


def render(nodes, now, hidden=None, site_url=""):
    """`nodes` are the public nodes; `hidden` the private ones (not reachable
    from outside), or None when they are not counted."""
    c = counts(nodes)
    everything = nodes + (hidden or [])
    kinds = [k for k in KINDS if k != "private" or hidden is not None]
    paths = _map_paths()
    # the private nodes are off until a visitor switches them on
    groups = cluster(nodes)
    # big groups first, so that single markers are drawn on top and stay clickable
    groups.sort(key=lambda g: -len(g["nodes"]))
    markers = "\n".join(_marker(g) for g in groups)
    rows = "\n".join(_row(n) for n in everything)
    numbers = [("Public nodes", c["nodes"])]
    if hidden is not None:
        numbers.append(("Private nodes", len(hidden)))
    numbers += [("P2P only", c["p2p"]), ("RPC only", c["rpc"]), ("P2P and RPC", c["both"]),
                ("Countries", c["countries"])]
    tiles = "".join(
        f'<div class="lt-stat-tile"><span class="lt-stat-label">{label}</span>'
        f'<span class="lt-stat-value">{value}</span></div>' for label, value in numbers)
    # the script turns these into switches for what the map shows
    legend = "".join(
        f'<li data-filter="kind" data-value="{k}"><span class="lt-map-legend-item">'
        f'{_mark(k)}{e(KINDS[k])}</span></li>' for k in kinds)
    filters = "".join(
        f'<button type="button" class="lt-map-switch" data-filter="kind" data-value="{k}" '
        f'aria-pressed="{"false" if k == "private" else "true"}">{_mark(k)}{e(KINDS[k])}</button>'
        for k in kinds)
    versions = sorted({n["release"] for n in everything if n["release"]}, reverse=True)
    version_choice = "".join(f'<option value="{e(v)}">{e(v)}</option>' for v in versions)
    caption = f'{c["nodes"]} public nodes, {c["answering"]} answering'
    if hidden is not None:
        caption += f' · {len(hidden)} private nodes, hidden until switched on'
    generated = stamp(now)
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    hidden_note = HIDDEN_NOTE if hidden is not None else ""
    named_note = NAMED_NOTE if any(n.get("name") for n in nodes) else ""
    if hidden is not None:
        lead = ("Public Zephyr nodes found on the network, and private nodes seen "
                "connecting to it. Checked once per hour.")
        summary = "World map and directory of Zephyr nodes: public nodes found on the network and private nodes seen connecting to it."
    else:
        lead = "Public Zephyr nodes found on the network. Checked once per hour."
        summary = "World map and directory of the public Zephyr nodes found on the network."
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>map.lateo.dev · Zephyr node map</title>
<meta name="description" content="{summary}">
{_preview(site_url, summary)}<link rel="stylesheet" href="{asset("tokens.css")}">
<link rel="stylesheet" href="{asset("site.css")}">
<link rel="stylesheet" href="{asset("data.css")}">
<link rel="stylesheet" href="{asset("map.css")}">
<link rel="stylesheet" href="{asset("map-site.css")}">
<link rel="icon" href="{asset("favicon.svg")}" type="image/svg+xml">
<link rel="icon" href="{asset("favicon-32.png")}" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="{asset("apple-touch-icon-180.png")}">
<script src="{asset("map.js")}" defer></script>
</head>
<body>
<a class="lt-skip-link" href="#main">Skip to main content</a>
<header class="site">
  <div class="wrap">
    <a class="name" href="/"><picture>
      <source srcset="{asset("lockup-host-map-reversed.svg")}" media="(prefers-color-scheme: dark)">
      <img src="{asset("lockup-host-map.svg")}" width="165" height="28" alt="map.lateo.dev">
    </picture></a>
    <nav>
      <a href="#map">Map</a>
      <a href="#nodes">Nodes</a>
      <a href="#method">How it is made</a>
    </nav>
  </div>
</header>

<main id="main" class="wrap-main" tabindex="-1">
  <h1>Zephyr node map</h1>
  <p class="lead">{lead} Last update <time id="updated" datetime="{iso}" data-epoch="{int(now)}">{e(generated)}</time>.</p>
  <p id="stale" class="lt-callout lt-callout-warning" role="status" hidden>These numbers are older than they should be: the map has not been updated for a while.</p>

  <div class="lt-stat-grid">{tiles}</div>

  <section id="map" aria-label="Map">
    <div class="lt-map-frame">
      <svg class="lt-map-canvas lt-map-zoomable" viewBox="{VIEW}" data-detail="{asset("world-map-50m.svg")}" role="group" aria-label="World map with {len(everything)} Zephyr nodes">
        {SYMBOLS}
        <rect class="lt-map-water" width="{WIDTH}" height="{HEIGHT}"/>
        <path class="lt-map-graticule" d="{paths["graticule"]}"/>
        <path class="lt-map-land" fill-rule="evenodd" d="{paths["land"]}"/>
        <path class="lt-map-borders" d="{paths["borders"]}"/>
        <g id="markers">
{markers}
        </g>
      </svg>
      <div class="lt-map-zoom" hidden>
        <button type="button" data-zoom="in" aria-label="Zoom in" title="Zoom in">{ICON % "M8 3v10M3 8h10"}</button>
        <button type="button" data-zoom="out" aria-label="Zoom out" title="Zoom out">{ICON % "M3 8h10"}</button>
        <button type="button" data-zoom="reset" aria-label="Show the whole world" title="Whole world">{ICON % "M3 6V3h3M10 3h3v3M13 10v3h-3M6 13H3v-3"}</button>
        <button type="button" data-zoom="clear" aria-label="Let go of the picked nodes" title="Let go of the picked nodes" hidden>{ICON % "M4 4l8 8M12 4l-8 8"}</button>
      </div>
      <p id="tip" class="lt-map-note" role="status" hidden></p>
      <div id="hover" class="lt-map-card" hidden></div>
      <div class="lt-map-toolbar">
        <ul class="lt-map-legend" id="legend" aria-label="Legend">
          {legend}
          <li><span class="lt-map-legend-item">{_mark("p2p", " is-offline")}Hollow: not answering now</span></li>
          <li><span class="lt-map-legend-item"><span class="lt-map-group-swatch" aria-hidden="true">3</span>Several nodes close together</span></li>
        </ul>
        <span class="lt-muted lt-compact">Places are approximate.<span id="legend-hint" hidden> Click a kind to show or hide it. Zoom with the buttons, a double click, or Ctrl and the wheel.</span></span>
      </div>
    </div>
  </section>

  <section id="nodes" aria-labelledby="nodes-title">
    <h2 id="nodes-title">Nodes</h2>
    <div class="controls" hidden>
      <div class="filters" role="group" aria-label="Kinds shown">{filters}</div>
      <label class="search">Version <select id="version"><option value="">All</option>{version_choice}</select></label>
      <label class="search">Search <input type="search" id="q" autocomplete="off" spellcheck="false" placeholder="address, place or network"></label>
      <button type="button" id="reset" hidden>Back to the start</button>
      <span id="shown" class="lt-muted" role="status"></span>
    </div>
    <p id="nothing" class="lt-muted" hidden>No node is shown: every kind is switched off, or nothing matches the version or the search.</p>
    <div class="lt-table-scroll" tabindex="0" role="region" aria-labelledby="nodes-caption">
      <table class="lt-table" id="node-table">
        <caption id="nodes-caption">{e(caption)}</caption>
        <thead>
          <tr>
            <th scope="col">Address</th>
            <th scope="col">Kind</th>
            <th scope="col">Ports</th>
            <th scope="col">Place</th>
            <th scope="col">Network</th>
            <th scope="col">Version</th>
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
      <li><strong>Public nodes</strong> accept connections. They are found through
      the peer lists that nodes share, starting at the seed nodes; only addresses
      learned that way are contacted. Each is checked once per hour with the P2P
      handshake and <code>get_version</code>. A node that is silent for 48 hours
      leaves the map.</li>
{hidden_note}{named_note}      <li><strong>Version</strong> is an indication, read from the hardforks a node
      knows. Exact builds, host names and how a node's RPC is set up are not
      asked for or shown.</li>
      <li><strong>Place and network</strong> come from
      <a href="https://db-ip.com">IP Geolocation by DB-IP</a> and describe address
      blocks: a point is often a provider's data centre.</li>
    </ul>
    <p>Questions about an entry: open an
    <a href="https://github.com/loki-notfound/lateo-map/issues">issue</a> or write
    to <!--email_off--><a href="mailto:loki-notfound@proton.me">loki-notfound@proton.me</a><!--/email_off-->. The
    collector's requests name map.lateo.dev in their user agent.</p>
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


def data(nodes, now, hidden=None):
    out = {"generated": int(now), "counts": counts(nodes),
           "source": "https://github.com/loki-notfound/lateo-map",
           "location_data": "IP Geolocation by DB-IP, https://db-ip.com, CC BY 4.0",
           "nodes": nodes}
    if hidden is not None:
        keep = ("country", "country_name", "city", "lat", "lon", "asn", "network", "height", "release")
        out["counts"]["private"] = len(hidden)
        out["private"] = [{k: n[k] for k in keep} for n in hidden]
    return out


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
    # the nodes by name first: they are few and are what a visitor may look for
    nodes = load_named(db, config) + load_nodes(db, config)
    hidden = load_hidden(db, config, now)
    _write(os.path.join(out, "nodes.json"), json.dumps(data(nodes, now, hidden), indent=1) + "\n")
    _write(os.path.join(out, "index.html"), render(nodes, now, hidden, config.site_url))
    return len(nodes)
