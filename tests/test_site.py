import json
import os
import re
import tempfile
import unittest

from lateo_map import config, db as store, site

NOW = 1_800_000_000


def add(db, ip, *, p2p=True, rpc=False, misses=0, height=1000, last_ok=NOW, lat=50.0, lon=8.0,
        city="Frankfurt", network="Example Net", release="v2.3.0"):
    store.hear(db, ip, 17766, 0, NOW, NOW)
    db.execute(
        "UPDATE nodes SET on_map = 1, has_p2p = ?, has_rpc = ?, rpc_port = ?, misses = ?, height = ?,"
        " last_ok = ?, first_ok = ?, lat = ?, lon = ?, city = ?, country = 'DE',"
        " country_name = 'Germany', network = ?, asn = 64500, release = ? WHERE ip = ?",
        (int(p2p), int(rpc), 17767 if rpc else 0, misses, height, last_ok, last_ok, lat, lon, city,
         network, release, ip))


class SiteTest(unittest.TestCase):
    def setUp(self):
        self.db = store.connect(":memory:")
        self.addCleanup(self.db.close)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cfg = config.Config(database=":memory:", site_dir=self.dir.name)

    def page(self):
        site.build(self.db, self.cfg, NOW)
        with open(os.path.join(self.dir.name, "index.html"), encoding="utf-8") as f:
            return f.read()

    def test_files_are_written(self):
        add(self.db, "8.8.8.8")
        self.assertEqual(site.build(self.db, self.cfg, NOW), 1)
        self.assertEqual(sorted(os.listdir(self.dir.name)),
                         [".htaccess", "assets", "index.html", "nodes.json", "robots.txt"])
        for name in ("site.css", "map.css", "map-site.css", "map.js", "favicon.svg"):
            self.assertIn(name, os.listdir(os.path.join(self.dir.name, "assets")))
        self.assertFalse([n for n in os.listdir(self.dir.name) if n.startswith(".new")])

    def test_kinds_and_status(self):
        add(self.db, "8.8.8.1")
        add(self.db, "8.8.8.2", p2p=False, rpc=True)
        add(self.db, "8.8.8.3", rpc=True, misses=2)
        add(self.db, "8.8.8.4", height=900)
        nodes = {n["ip"]: n for n in site.load_nodes(self.db, self.cfg)}
        self.assertEqual([nodes[f"8.8.8.{i}"]["kind"] for i in (1, 2, 3, 4)], ["p2p", "rpc", "both", "p2p"])
        self.assertEqual([nodes[f"8.8.8.{i}"]["status"] for i in (1, 2, 3, 4)],
                         ["online", "online", "offline", "behind"])
        self.assertEqual((nodes["8.8.8.1"]["p2p_port"], nodes["8.8.8.1"]["rpc_port"]), (17766, None))
        self.assertEqual(nodes["8.8.8.2"]["p2p_port"], None)

    def test_a_node_checked_earlier_in_the_hour_is_not_behind(self):
        add(self.db, "8.8.8.1", height=1000, last_ok=NOW)
        add(self.db, "8.8.8.2", height=975, last_ok=NOW - 3000)      # 25 blocks ago
        add(self.db, "8.8.8.3", height=960, last_ok=NOW - 3000)      # really 15 behind
        nodes = {n["ip"]: n["status"] for n in site.load_nodes(self.db, self.cfg)}
        self.assertEqual(nodes, {"8.8.8.1": "online", "8.8.8.2": "online", "8.8.8.3": "behind"})

    def test_only_nodes_on_the_map_and_nothing_about_the_rpc_mode(self):
        add(self.db, "8.8.8.1")
        store.hear(self.db, "9.9.9.9", 17766, 0, NOW, NOW)       # heard of, never answered
        page = self.page()
        self.assertIn("8.8.8.1", page)
        self.assertNotIn("9.9.9.9", page)
        with open(os.path.join(self.dir.name, "nodes.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual([n["ip"] for n in data["nodes"]], ["8.8.8.1"])
        self.assertEqual(data["counts"]["nodes"], 1)
        for text in (page.split('id="method"')[0], json.dumps(data)):
            self.assertNotRegex(text.lower(), r"restricted|unrestricted")

    def test_text_from_outside_is_escaped(self):
        add(self.db, "8.8.8.1", city='<script>alert(1)</script>', network='A & B "x" <b>',
            release="<i>v9</i>")
        page = self.page()
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<b>", page)
        self.assertNotIn("<i>v9", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("A &amp; B &quot;x&quot; &lt;b&gt;", page)

    def test_page_fits_its_content_security_policy(self):
        add(self.db, "8.8.8.1")
        page = self.page()
        self.assertNotRegex(page, r"<style|\sstyle=|\son[a-z]+=|javascript:")
        self.assertRegex(page, r'<script src="/assets/map\.js\?v=[0-9a-f]{10}" defer></script>')
        self.assertEqual(len(re.findall(r"<script", page)), 1)
        for url in re.findall(r'(?:src|srcset|href)="([^"]+)"', page):
            if url.startswith(("/", "#")):
                continue
            # the only links that leave the site are plain links a visitor clicks
            self.assertRegex(url, r"^(https://(db-ip\.com|github\.com/)|mailto:)", url)
        self.assertNotRegex(page, r'<(link|script|img|source|use)[^>]+(https?:)?//')
        with open(os.path.join(self.dir.name, ".htaccess")) as f:
            self.assertIn(site.HEADERS, f.read())
        self.assertIn("IP Geolocation by DB-IP", page)

    def test_nodes_at_one_spot_become_one_marker(self):
        for i in range(1, 5):
            add(self.db, f"8.8.8.{i}")
        add(self.db, "8.8.4.4", lat=35.7, lon=139.7, city="Tokyo")
        add(self.db, "8.8.4.5", lat=None, lon=None, city=None)
        groups = site.cluster(site.load_nodes(self.db, self.cfg))
        self.assertEqual(sorted(len(g["nodes"]) for g in groups), [1, 4])
        page = self.page()
        self.assertEqual(page.count('class="lt-map-marker"'), 2)
        self.assertIn("4 nodes · Frankfurt, Germany", page)
        self.assertEqual(page.count("<tr id="), 6)       # the one without a place is in the table

    def test_markers_never_touch(self):
        spots = [(50.0 + i * 0.7, 8.0 + i * 1.1) for i in range(12)]
        for i, (lat, lon) in enumerate(spots):
            add(self.db, f"8.8.9.{i + 1}", lat=lat, lon=lon, city=f"C{i}")
        groups = site.cluster(site.load_nodes(self.db, self.cfg))
        self.assertEqual(sum(len(g["nodes"]) for g in groups), 12)
        for i, a in enumerate(groups):
            for b in groups[i + 1:]:
                distance = ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5
                self.assertGreaterEqual(distance + 1, site.radius(len(a["nodes"])) + site.radius(len(b["nodes"])))

    def hide(self, entries, at=NOW - 600):
        from lateo_map import geo
        self.db.execute("DELETE FROM private_nodes")
        for index, entry in enumerate(entries):
            place = geo.Place(**{k: entry.get(k) for k in
                                 ("country", "country_name", "city", "lat", "lon", "asn", "network")})
            store.see_private(self.db, f"tag{index}", at, entry.get("height"), place)
        store.set_meta(self.db, "unreachable_at", at)

    def test_private_nodes_are_listed_without_an_address_and_off_at_first(self):
        add(self.db, "8.8.8.1")
        self.assertNotIn("Private", self.page())
        entry = {"country": "NL", "country_name": "Netherlands", "city": "Utrecht",
                 "lat": 52.1, "lon": 5.1, "asn": 64501, "network": "Home Net", "height": 909000}
        self.hide([entry, dict(entry, city="Zwolle", lat=52.5, lon=6.1, height=None)])
        page = self.page()
        self.assertIn('Private nodes</span><span class="lt-stat-value">2<', page)
        labels = re.findall(r'lt-stat-label">([^<]+)<', page)
        self.assertEqual(labels[:3], ["Public nodes", "Private nodes", "P2P only"])
        self.assertIn("<strong>Private nodes</strong> connect out", page)
        self.assertIn("and private nodes seen connecting to it", page)
        self.assertEqual(page.count('data-kind="private"'), 2)
        # off until a visitor switches them on: rows hidden, no marker, the switch not pressed
        self.assertIn('<tr id="h-1" hidden data-kind="private" data-status="online" data-release="v2.3.0"', page)
        self.assertEqual(page.count('class="lt-map-marker"'), 1)
        self.assertIn('data-filter="kind" data-value="private" aria-pressed="false"', page)
        self.assertIn('data-filter="kind" data-value="p2p" aria-pressed="true"', page)
        self.assertIn("not shown", page)
        self.assertIn('data-filter="kind" data-value="private"', page)
        self.assertIn("1 public nodes, 1 answering · 2 private nodes, hidden until switched on", page)
        with open(os.path.join(self.dir.name, "nodes.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["counts"]["private"], 2)
        self.assertEqual(data["counts"]["nodes"], 1)
        self.assertEqual(sorted(data["private"][0]),
                         ["asn", "city", "country", "country_name", "height", "lat", "lon",
                          "network", "release"])
        self.assertIn("Home Net", page)
        self.assertIn('<li data-filter="kind" data-value="private">', page)
        self.assertEqual([n["ip"] for n in data["nodes"]], ["8.8.8.1"])
        self.hide([entry], at=NOW - 3 * 3600)
        self.assertNotIn("Private", self.page())

    def test_filters_for_kind_and_version(self):
        add(self.db, "8.8.8.1", release="v2.3.0")
        add(self.db, "8.8.8.2", release="v2.2.0", rpc=True)
        page = self.page()
        # the version is a choice of its own, not a switch among the kinds
        self.assertIn('<select id="version"><option value="">All</option>'
                      '<option value="v2.3.0">v2.3.0</option><option value="v2.2.0">v2.2.0</option></select>', page)
        self.assertNotIn('data-filter="release"', page)
        self.assertIn('<p id="nothing" class="lt-muted" hidden>', page)
        for kind in ("p2p", "rpc", "both"):
            self.assertIn(f'data-filter="kind" data-value="{kind}"', page)
        self.assertNotIn('data-value="private"', page)
        self.assertIn('data-release="v2.2.0"', page)
        self.assertRegex(page, r'<tr id="n-8-8-8-1"[^>]* data-x="522\.2" data-y="111\.1"')

    def test_asset_links_change_when_a_file_changes(self):
        add(self.db, "8.8.8.1")
        page = self.page()
        links = re.findall(r'(?:href|src|srcset)="(/assets/[^"]+)"', page)
        self.assertGreaterEqual(len(links), 9)
        for link in links:
            self.assertRegex(link, r"^/assets/[a-z0-9.-]+\?v=[0-9a-f]{10}$")
        self.assertEqual(site.asset("map.js"), site.asset("map.js"))
        self.assertNotEqual(site.asset("map.js").split("=")[1], site.asset("site.css").split("=")[1])

    def test_map_has_no_heading_but_keeps_a_name(self):
        page = self.page()
        self.assertNotIn(">Map</h2>", page)
        self.assertIn('<section id="map" aria-label="Map">', page)
        # no title element on the map or in it besides the markers': a browser shows one as a tooltip
        self.assertNotIn("map-svg-title", page)
        self.assertRegex(page, r'<svg class="lt-map-canvas lt-map-zoomable"[^>]* aria-label="World map with \d+ Zephyr nodes"')
        self.assertIn('<div id="hover" class="lt-map-card" hidden></div>', page)
        self.assertIn('data-zoom="clear"', page)
        # the note about a pick is written on the map, not under it
        frame = page[page.index('class="lt-map-frame"'):page.index('class="lt-map-toolbar"')]
        self.assertIn('<p id="tip" class="lt-map-note" role="status" hidden></p>', frame)

    def test_address_is_kept_out_of_a_proxy_that_rewrites_addresses(self):
        # such a proxy adds a script of its own, which the policy and the map links do not want
        self.assertIn('<!--email_off--><a href="mailto:', self.page())

    def test_link_preview_only_with_the_address_of_the_site(self):
        add(self.db, "8.8.8.1")
        self.assertNotIn("og:", self.page())
        self.cfg.site_url = "https://map.example/"
        page = self.page()
        self.assertIn('<meta property="og:title" content="Zephyr node map">', page)
        self.assertIn('<meta property="og:url" content="https://map.example/">', page)
        self.assertRegex(page, r'<meta property="og:image" content="https://map\.example/assets/og\.png\?v=[0-9a-f]{10}">')
        self.assertIn("og.png", os.listdir(os.path.join(self.dir.name, "assets")))

    def test_finer_outlines_are_on_offer_for_the_script(self):
        add(self.db, "8.8.8.1")
        self.assertRegex(self.page(), r'data-detail="/assets/world-map-50m\.svg\?v=[0-9a-f]{10}"')
        self.assertIn("world-map-50m.svg", os.listdir(os.path.join(self.dir.name, "assets")))

    def test_a_node_by_name_is_in_the_table_and_not_on_the_map(self):
        add(self.db, "8.8.8.1")
        self.cfg.named_nodes = [{"name": "node.example", "port": 443, "note": "Behind a proxy, three hosts",
                                 "link": "https://node.example/"}]
        self.assertNotIn("node.example", self.page())            # never answered: not shown
        store.note_named(self.db, "node.example", 443, NOW, 1000, "v2.3.0")
        page = self.page()
        row = page[page.index('<tr id="d-node-example"'):]
        row = row[:row.index("</tr>")]
        self.assertIn('<a href="https://node.example/"><code>node.example</code></a>', row)
        self.assertIn("RPC 443", row)
        self.assertIn("Behind a proxy, three hosts", row)
        store.set_named_network(self.db, "node.example", 64999, "Proxy Net")
        self.assertIn('Proxy Net <span class="lt-muted">AS64999</span>', self.page())
        self.assertIn('data-kind="rpc"', row)
        self.assertNotIn("data-x=", row)                         # no spot on the map
        self.assertEqual(page.count('class="lt-map-marker"'), 1)
        self.assertLess(page.index('<tr id="d-node-example"'), page.index('<tr id="n-8-8-8-1"'))
        self.assertIn("2 public nodes, 2 answering", page)
        self.assertIn("<strong>Nodes by name</strong>", page)
        with open(os.path.join(self.dir.name, "nodes.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual((data["nodes"][0]["name"], data["nodes"][0]["ip"], data["counts"]["rpc"]),
                         ("node.example", None, 1))
        # silent for two checks: shown as offline; silent for two days: gone
        store.note_named(self.db, "node.example", 443, NOW + 3600)
        store.note_named(self.db, "node.example", 443, NOW + 7200)
        self.assertIn("Offline", self.page())
        store.note_named(self.db, "node.example", 443, NOW + 3 * 86400)
        self.assertNotIn("node.example", self.page())

    def test_empty_map(self):
        page = self.page()
        self.assertIn("0 public nodes, 0 answering", page)
        self.assertIn("Public Zephyr nodes found on the network. Checked once per hour.", page)
        self.assertNotIn("Private", page)
        self.assertNotIn("private nodes", page)

    def test_projection(self):
        self.assertEqual(site.project(90, -180), (0.0, 0.0))
        self.assertEqual(site.project(0, 0), (500.0, 250.0))
        self.assertEqual(site.row_id({"ip": "2606:4700::1"}), "n-2606-4700--1")


if __name__ == "__main__":
    unittest.main()
