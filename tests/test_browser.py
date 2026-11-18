"""The page in a real browser: its script, its switches, its policy.

Needs Chromium or Chrome on the machine and is skipped without it. A site is
written from made-up nodes, served on localhost with the same Content
Security Policy as the real one, and loaded once per scenario with a small
script that plays a visitor and reports what it sees.
"""

import functools
import http.server
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest

from lateo_map import config, db as store, geo, site

NOW = 1_800_000_000
CHROME = next((path for path in map(shutil.which, (
    "chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")) if path), None)

PRELUDE = """
function rows() { return [].slice.call(document.querySelectorAll('#node-table tbody tr')); }
function shown(kind) { return rows().filter(function (r) { return !r.hidden && (!kind || r.dataset.kind === kind); }).length; }
function markers() { return document.querySelectorAll('.lt-map-marker').length; }
function click(selector) { document.querySelector(selector).click(); }
function report(result) { document.title = 'RESULT' + JSON.stringify(result); }
function play(steps) { window.addEventListener('load', function () { setTimeout(steps, 50); }); }
"""

SCENARIOS = {
    "start": """play(function () { report({
        rows: rows().length, shown: shown(), privateShown: shown('private'), markers: markers(),
        controls: !document.querySelector('.controls').hidden, zoom: !document.querySelector('.lt-map-zoom').hidden,
        titles: document.querySelectorAll('#markers title, .lt-map-canvas > title').length,
        rings: document.querySelectorAll('.lt-map-ring').length, stale: !document.getElementById('stale').hidden,
        legend: document.querySelectorAll('#legend button').length }); });""",
    "only_private": """play(function () {
        ['p2p', 'rpc', 'both'].forEach(function (k) { click('#legend button[data-value=' + k + ']'); });
        var none = { shown: shown(), markers: markers(), note: !document.getElementById('nothing').hidden };
        click('#legend button[data-value=private]');
        report({ none: none, shown: shown(), privateShown: shown('private'), markers: markers(),
                 note: !document.getElementById('nothing').hidden,
                 tablePressed: document.querySelector('.controls button[data-value=private]').getAttribute('aria-pressed') }); });""",
    "version": """play(function () {
        var v = document.getElementById('version'); v.value = 'v2.2.0'; v.dispatchEvent(new Event('change'));
        var one = shown(); click('#reset');
        report({ one: one, all: shown(), value: v.value }); });""",
    "pick": """play(function () {
        var group = [].filter.call(document.querySelectorAll('.lt-map-marker'), function (m) { return m.querySelector('.lt-map-group'); })[0];
        group.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        var picked = { shown: shown(), note: document.getElementById('tip').textContent,
                       noteInFrame: !!document.querySelector('.map-box #tip'),
                       clear: !document.querySelector('[data-zoom=clear]').hidden,
                       selected: document.querySelectorAll('.lt-map-marker.is-selected').length };
        document.querySelector('.lt-map-marker.is-selected').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        report({ picked: picked, after: shown(), noteHidden: document.getElementById('tip').hidden }); });""",
    "zoom": """play(function () {
        var before = markers();
        document.querySelector('[data-zoom=in] path').dispatchEvent(new MouseEvent('click', { bubbles: true }));
        click('[data-zoom=in]'); click('[data-zoom=in]');
        var view = document.querySelector('.lt-map-canvas').getAttribute('viewBox').split(' ').map(Number);
        var zoomed = markers(); click('[data-zoom=reset]');
        report({ before: before, width: view[2], zoomed: zoomed,
                 back: document.querySelector('.lt-map-canvas').getAttribute('viewBox') }); });""",
    "hover": """play(function () {
        var one = [].filter.call(document.querySelectorAll('.lt-map-marker'), function (m) { return m.querySelector('use'); })[0];
        var box = one.getBoundingClientRect();
        one.dispatchEvent(new PointerEvent('pointerenter', { clientX: box.left + 5, clientY: box.top + 5 }));
        var card = document.getElementById('hover');
        var single = { visible: !card.hidden, text: card.textContent };
        one.dispatchEvent(new PointerEvent('pointerleave'));
        report({ single: single, hiddenAgain: card.hidden }); });""",
    "refresh": """play(function () {
        click('#legend button[data-value=p2p]');
        var real = Date.now; Date.now = function () { return real() + 6 * 60 * 1000; };
        document.dispatchEvent(new Event('visibilitychange'));
        setTimeout(function () { report({ rows: rows().length, shown: shown(), markers: markers(),
            lead: /REFRESHED/.test(document.querySelector('.lead').textContent),
            p2p: document.querySelector('#legend button[data-value=p2p]').getAttribute('aria-pressed') }); }, 1500); });""",
    "old_page": """play(function () { report({ shown: shown(), markers: markers(),
        controls: !document.querySelector('.controls').hidden }); });""",
    "stale": """play(function () { report({ stale: !document.getElementById('stale').hidden }); });""",
    "phone": """play(function () {
        var frame = document.querySelector('.lt-map-frame'), svg = document.querySelector('.lt-map-canvas');
        var one = document.querySelector('.lt-map-marker use').getBoundingClientRect();
        document.querySelector('.lt-map-marker').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        var note = document.getElementById('tip').getBoundingClientRect(), box = frame.getBoundingClientRect();
        report({ screen: window.innerWidth, frame: frame.clientWidth, scroll: frame.scrollWidth,
                 map: Math.round(svg.getBoundingClientRect().width),
                 page: document.documentElement.scrollWidth, markers: markers(),
                 marker: Math.round(one.width), noteAbove: note.bottom <= box.top + 1, noteHeight: note.height }); });""",
    "desk": """play(function () {
        var svg = document.querySelector('.lt-map-canvas').getBoundingClientRect();
        var one = document.querySelector('.lt-map-marker use').getBoundingClientRect();
        document.querySelector('.lt-map-marker').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        var note = document.getElementById('tip').getBoundingClientRect();
        var frame = document.querySelector('.lt-map-frame').getBoundingClientRect();
        report({ map: svg.width, marker: one.width,
                 noteOnMap: note.top >= frame.top && note.bottom <= frame.bottom && note.height > 0 }); });""",
}


def make_site(directory):
    database = store.connect(":memory:")
    spots = [("8.8.8.1", "p2p", 50.48, 12.37, "Falkenstein", "DE", "Germany", "v2.3.0"),
             ("8.8.8.2", "p2p", 50.48, 12.37, "Falkenstein", "DE", "Germany", "v2.3.0"),
             ("8.8.8.3", "both", 50.11, 8.68, "Frankfurt", "DE", "Germany", "v2.3.0"),
             ("8.8.8.4", "rpc", 45.46, 9.19, "Milan", "IT", "Italy", "v2.2.0"),
             ("8.8.8.5", "p2p", 35.66, 139.70, "Shibuya City", "JP", "Japan", "v2.3.0"),
             ("8.8.8.6", "both", 37.35, -121.95, "Santa Clara", "US", "United States", "v2.3.0"),
             ("8.8.8.7", "p2p", 40.71, -74.00, "New York", "US", "United States", "v2.2.0")]
    for ip, kind, lat, lon, city, code, country, version in spots:
        store.hear(database, ip, 17766, 0, NOW, NOW)
        database.execute(
            "UPDATE nodes SET on_map = 1, has_p2p = ?, has_rpc = ?, rpc_port = ?, height = 909000,"
            " last_ok = ?, first_ok = ?, last_check = ?, lat = ?, lon = ?, city = ?, country = ?,"
            " country_name = ?, network = 'Example Net', asn = 64500, release = ? WHERE ip = ?",
            (int(kind != "rpc"), int(kind != "p2p"), 17767 if kind != "p2p" else 0, NOW, NOW, NOW,
             lat, lon, city, code, country, version, ip))
    for index, (lat, lon, city) in enumerate([(50.48, 12.37, "Falkenstein"), (-23.55, -46.63, "Sao Paulo")]):
        store.see_private(database, f"tag{index}", NOW, 909000,
                          geo.Place(country="XX", country_name="Somewhere", city=city, lat=lat, lon=lon,
                                    asn=64501, network="Home Net"))
    store.set_meta(database, "unreachable_at", NOW)
    site.build(database, config.Config(database=":memory:", site_dir=directory), NOW)
    database.close()


class Handler(http.server.SimpleHTTPRequestHandler):
    fetches = {}

    def end_headers(self):
        self.send_header("Content-Security-Policy", site.HEADERS)
        super().end_headers()

    def send(self, body, kind):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/scenario/"):
            name = path[len("/scenario/"):-len(".js")]
            self.send((PRELUDE + SCENARIOS[name]).encode(), "text/javascript")
            return
        if path.startswith("/run/"):
            name = path[len("/run/"):]
            with open(os.path.join(self.directory, "index.html"), encoding="utf-8") as f:
                page = f.read()
            page = page.replace("</head>", f'<script src="/scenario/{name}.js" defer></script></head>')
            Handler.fetches[name] = Handler.fetches.get(name, 0) + 1
            if name == "refresh" and Handler.fetches[name] > 1:
                # the page as a later round writes it: one node gone, another moment
                page = re.sub(r'<tr id="n-8-8-8-6".*?</tr>\n?', "", page, count=1, flags=re.S)
                page = page.replace("Last update", "REFRESHED Last update")
            if name == "old_page":
                # a page from before the newer parts existed
                for pattern in (r'<div id="hover"[^>]*></div>', r'<p id="tip"[^>]*></p>',
                                r'<div class="lt-map-zoom" hidden>.*?</div>', r'<label class="search">Version.*?</label>',
                                r'<p id="nothing"[^>]*>.*?</p>', r'<p id="stale"[^>]*>.*?</p>'):
                    page = re.sub(pattern, "", page, flags=re.S)
            if name == "stale":
                page = re.sub(r'data-epoch="\d+"', 'data-epoch="1000"', page)
            self.send(page.encode(), "text/html; charset=utf-8")
            return
        self.path = path
        super().do_GET()

    def log_message(self, *args):
        pass


@unittest.skipUnless(CHROME, "no Chromium or Chrome on this machine")
class BrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        make_site(cls.dir.name)
        cls.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(Handler, directory=cls.dir.name))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.dir.cleanup()

    def play(self, name, size="1280,900"):
        with tempfile.TemporaryDirectory() as profile:
            done = subprocess.run(
                [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--enable-logging=stderr",
                 "--v=0", f"--user-data-dir={profile}", f"--window-size={size}",
                 "--virtual-time-budget=6000", "--dump-dom",
                 f"http://127.0.0.1:{self.port}/run/{name}"],
                capture_output=True, text=True, timeout=120)
        problems = [line for line in done.stderr.splitlines()
                    if "Uncaught" in line or "Refused to" in line]
        self.assertEqual(problems, [], name)
        found = re.search(r"<title>RESULT(.*?)</title>", done.stdout, re.S)
        self.assertIsNotNone(found, f"{name}: the scenario did not report")
        return json.loads(found.group(1).replace("&quot;", '"').replace("&amp;", "&"))

    def test_start(self):
        r = self.play("start")
        self.assertEqual((r["rows"], r["shown"], r["privateShown"]), (9, 7, 0))
        self.assertTrue(r["controls"] and r["zoom"])
        self.assertGreaterEqual(r["markers"], 4)
        self.assertEqual(r["titles"], 0)            # no second tooltip of the browser
        self.assertGreater(r["rings"], 0)           # groups show their kinds
        self.assertFalse(r["stale"])
        self.assertEqual(r["legend"], 4)

    def test_everything_off_and_then_only_private(self):
        r = self.play("only_private")
        self.assertEqual(r["none"], {"shown": 0, "markers": 0, "note": True})
        self.assertEqual((r["shown"], r["privateShown"], r["markers"], r["note"]), (2, 2, 2, False))
        self.assertEqual(r["tablePressed"], "true")     # the switches at the table follow the legend

    def test_version_is_a_choice_of_its_own(self):
        r = self.play("version")
        self.assertEqual((r["one"], r["all"], r["value"]), (2, 7, ""))

    def test_pick_and_let_go(self):
        r = self.play("pick")
        self.assertGreaterEqual(r["picked"]["shown"], 2)
        self.assertLess(r["picked"]["shown"], 7)
        self.assertIn("picked", r["picked"]["note"])
        self.assertTrue(r["picked"]["noteInFrame"] and r["picked"]["clear"])
        self.assertEqual(r["picked"]["selected"], 1)
        self.assertEqual((r["after"], r["noteHidden"]), (7, True))

    def test_zoom(self):
        r = self.play("zoom")
        self.assertEqual(r["width"], 125)
        self.assertGreaterEqual(r["zoomed"], r["before"])
        self.assertEqual(r["back"], site.VIEW)

    def test_hover_card(self):
        r = self.play("hover")
        self.assertTrue(r["single"]["visible"])
        for text in ("Kind", "Place", "Version", "Height", "Example Net"):
            self.assertIn(text, r["single"]["text"])
        self.assertTrue(r["hiddenAgain"])

    def test_an_open_page_takes_over_new_rows_and_keeps_the_switches(self):
        r = self.play("refresh")
        self.assertEqual(r["rows"], 8)              # one node gone
        self.assertEqual(r["shown"], 2)             # P2P is still off: one RPC and the one "both" left
        self.assertTrue(r["lead"])
        self.assertEqual(r["p2p"], "false")

    def test_a_page_without_the_newer_parts_still_gets_its_map(self):
        r = self.play("old_page")
        self.assertEqual(r["shown"], 7)
        self.assertGreaterEqual(r["markers"], 4)
        self.assertTrue(r["controls"])

    def test_on_a_phone_the_map_fits_and_nothing_scrolls_sideways(self):
        r = self.play("phone", "390,800")
        self.assertLessEqual(r["screen"], 500)
        self.assertLessEqual(r["scroll"], r["frame"])       # the frame does not scroll
        self.assertLessEqual(r["map"], r["frame"])          # the map is as wide as its frame
        self.assertLessEqual(r["page"], r["screen"])        # and neither does the page
        self.assertGreater(r["markers"], 0)
        # a P2P marker is a circle of ten in a box of sixteen: as large as on a desk a thousand wide
        self.assertEqual(r["marker"], 10)
        self.assertTrue(r["noteAbove"] and r["noteHeight"] > 0)   # the note stands above the box

    def test_on_a_wide_screen_markers_grow_with_the_map_and_the_note_is_on_it(self):
        r = self.play("desk", "1600,900")
        self.assertGreater(r["map"], 1100)
        self.assertAlmostEqual(r["marker"], 10 * r["map"] / 1000, delta=0.5)
        self.assertTrue(r["noteOnMap"])

    def test_old_numbers_are_called_old(self):
        self.assertTrue(self.play("stale")["stale"])


if __name__ == "__main__":
    unittest.main()
