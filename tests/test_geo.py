import os
import tempfile
import unittest

from lateo_map import collector, config, db as store, geo, mmdb
from tests.mmdb_builder import MARKER, build, encode

CITY = {
    "8.8.8.0/24": {"city": {"names": {"en": "Mountain View", "de": "x"}},
                   "country": {"iso_code": "US", "names": {"en": "United States"}},
                   "location": {"latitude": 37.4, "longitude": -122.1},
                   "subdivisions": [{"names": {"en": "California"}}]},
    "9.9.0.0/16": {"country": {"iso_code": "CH", "names": {"en": "Switzerland"}}},
    "1.1.1.1/32": {"city": {"names": {"en": 7}}, "country": "odd",
                   "location": {"latitude": 500.0, "longitude": "x"}, "subdivisions": []},
}
ASN = {
    "8.8.8.0/24": {"autonomous_system_number": 15169, "autonomous_system_organization": "Example LLC"},
    "1.1.1.1/32": {"autonomous_system_number": "x", "autonomous_system_organization": ""},
}


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name, content):
        path = os.path.join(self.dir.name, name)
        with open(path, "wb") as f:
            f.write(content)
        return path


class MmdbTest(Files):
    def test_lookup_in_every_record_size(self):
        for size in (24, 28, 32):
            with mmdb.Reader(self.write(f"t{size}.mmdb", build(CITY, size))) as r:
                self.assertEqual(r.lookup("8.8.8.200")["country"]["iso_code"], "US", size)
                self.assertEqual(r.lookup("8.8.8.0")["location"]["longitude"], -122.1)
                self.assertEqual(r.lookup("9.9.255.255")["country"]["iso_code"], "CH")
                self.assertIsNone(r.lookup("8.8.9.0"))
                self.assertIsNone(r.lookup("127.0.0.1"))
                self.assertIsNone(r.lookup("2606:4700::1"))       # IPv4-only file
                self.assertEqual(r.lookup("::ffff:9.9.1.1")["country"]["iso_code"], "CH")

    def test_ipv6_tree_holds_both_families(self):
        nets = {"8.8.8.0/24": {"v": 4}, "2606:4700::/32": {"v": 6}}
        with mmdb.Reader(self.write("v6.mmdb", build(nets, 28, ip_version=6))) as r:
            self.assertEqual(r.lookup("8.8.8.8"), {"v": 4})
            self.assertEqual(r.lookup("2606:4700:4700::1111"), {"v": 6})
            self.assertIsNone(r.lookup("2a01::1"))
            self.assertIsNone(r.lookup("8.8.4.4"))

    def test_pointer_in_data(self):
        whole = build({"8.8.8.0/24": {"a": "shared"}})
        # a second record whose value points at the first record's map
        tree, rest = whole.split(bytes(16), 1)
        data, meta = rest.split(MARKER)
        pointer = bytes([0x20, 0x00])       # pointer of size 0 to data offset 0
        reader_file = tree + bytes(16) + data + encode("k")[:0] + pointer + MARKER + meta
        with mmdb.Reader(self.write("p.mmdb", reader_file)) as r:
            value, _ = r._decode(r.data_start + len(data), 0)
            self.assertEqual(value, {"a": "shared"})

    def test_damaged_files_are_refused(self):
        good = build(CITY)
        for bad in (b"", b"nothing here", good[:len(good) - 5],
                    good.replace(b"record_size", b"record_sizf")):
            with self.assertRaises(mmdb.FormatError):
                mmdb.Reader(self.write("bad.mmdb", bad or b" ")).close()

    def test_loop_of_pointers_ends(self):
        whole = build({"8.8.8.0/24": {"a": 1}})
        tree, rest = whole.split(bytes(16), 1)
        data, meta = rest.split(MARKER)
        looping = bytes([0x20, 0x00])       # the record is a pointer to itself
        with mmdb.Reader(self.write("l.mmdb", tree + bytes(16) + looping + data[2:] + MARKER + meta)) as r:
            with self.assertRaises(mmdb.FormatError):
                r.lookup("8.8.8.8")


class GeoTest(Files):
    def data(self):
        self.write("city.mmdb", build(CITY, 28))
        self.write("asn.mmdb", build(ASN, 24, database_type="Test-ASN"))
        g = geo.open_dir(self.dir.name)
        self.addCleanup(g.close)
        return g

    def test_place(self):
        p = self.data().place("8.8.8.8")
        self.assertEqual((p.country, p.country_name, p.region, p.city, p.lat, p.lon, p.asn, p.network),
                         ("US", "United States", "California", "Mountain View", 37.4, -122.1,
                          15169, "Example LLC"))

    def test_partial_and_odd_records(self):
        g = self.data()
        p = g.place("9.9.9.9")
        self.assertEqual((p.country, p.city, p.lat, p.asn), ("CH", None, None, None))
        p = g.place("1.1.1.1")
        self.assertEqual((p.country, p.city, p.region, p.lat, p.lon, p.asn, p.network),
                         (None, None, None, None, None, None, None))
        self.assertEqual(g.place("203.0.113.9"), geo.Place())

    def test_a_node_by_name_gets_the_network_its_name_leads_to(self):
        self.write("city.mmdb", build(CITY, 28))
        self.write("asn.mmdb", build(ASN, 24, database_type="Test-ASN"))
        database = store.connect(":memory:")
        self.addCleanup(database.close)
        cfg = config.Config(database=":memory:", geo_dir=self.dir.name,
                            named_nodes=[{"name": "node.example", "port": 443}])

        class Probes:
            @staticmethod
            def get_version(host, port, timeout):
                from lateo_map import rpc
                return rpc.Version(13, 11, 905)

            @staticmethod
            def resolve(name):
                return ["203.0.113.9", "8.8.8.8"]      # the first has no record

        collector.check_named(database, cfg, Probes, 1000)
        row = store.named(database, "node.example")
        self.assertEqual((row["asn"], row["network"], row["height"]), (15169, "Example LLC", 905))

    def test_no_files_means_no_locations(self):
        self.assertIsNone(geo.open_dir(self.dir.name))
        self.assertIsNone(geo.open_dir(""))

    def test_locate_fills_nodes_on_the_map_once_per_data_version(self):
        g = self.data()
        database = store.connect(":memory:")
        self.addCleanup(database.close)
        for ip, on_map in (("8.8.8.8", 1), ("9.9.9.9", 1), ("8.8.8.9", 0)):
            store.hear(database, ip, 17766, 0, 0, 0)
            database.execute("UPDATE nodes SET on_map = ? WHERE ip = ?", (on_map, ip))
        cfg = config.Config(database=":memory:")
        self.assertEqual(collector.locate(database, cfg, g), 2)
        row = database.execute("SELECT * FROM nodes WHERE ip = '8.8.8.8'").fetchone()
        self.assertEqual((row["country"], row["city"], row["asn"], row["network"]),
                         ("US", "Mountain View", 15169, "Example LLC"))
        self.assertIsNone(database.execute("SELECT country FROM nodes WHERE ip = '8.8.8.9'").fetchone()[0])
        self.assertEqual(collector.locate(database, cfg, g), 0)
        self.assertEqual(collector.locate(database, cfg), 0)      # no geo_dir: nothing happens

    def test_fetch_puts_only_good_files_in_place(self):
        asked = []

        def download(url, target):
            asked.append(url)
            if "2026-12" in url:
                raise OSError("not there yet")
            kind = "Test-City" if "city" in url else "Test-ASN"
            with open(target, "wb") as f:
                f.write(build(CITY if "city" in url else ASN, database_type=kind))

        december = 1_796_100_000      # 2026-12-01
        target = os.path.join(self.dir.name, "geo")
        self.assertEqual(geo.fetch(target, december, download), {"city": "2026-11", "asn": "2026-11"})
        self.assertEqual(sorted(os.listdir(target)), ["asn.mmdb", "city.mmdb"])
        self.assertEqual(asked[0], "https://download.db-ip.com/free/dbip-city-lite-2026-12.mmdb.gz")

        def wrong(url, target):
            with open(target, "wb") as f:
                f.write(build(CITY, database_type="Something else"))

        def content():
            with open(os.path.join(target, "city.mmdb"), "rb") as f:
                return f.read()

        before = content()
        with self.assertRaises(RuntimeError):
            geo.fetch(target, december, wrong)
        self.assertEqual(content(), before)
        self.assertEqual(sorted(os.listdir(target)), ["asn.mmdb", "city.mmdb"])


# the first schema, as databases made before the location step have it
V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE nodes (
    ip TEXT PRIMARY KEY, p2p_port INTEGER NOT NULL DEFAULT 0, rpc_port INTEGER NOT NULL DEFAULT 0,
    on_map INTEGER NOT NULL DEFAULT 0, has_p2p INTEGER NOT NULL DEFAULT 0,
    has_rpc INTEGER NOT NULL DEFAULT 0, height INTEGER, release TEXT,
    pruned INTEGER NOT NULL DEFAULT 0, misses INTEGER NOT NULL DEFAULT 0,
    first_heard INTEGER NOT NULL, last_heard INTEGER NOT NULL, first_ok INTEGER, last_ok INTEGER,
    last_check INTEGER, next_check INTEGER NOT NULL, asked_peers INTEGER NOT NULL DEFAULT 0,
    tried_rpc INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;
CREATE INDEX nodes_next_check ON nodes (next_check);
CREATE TABLE checks (ip TEXT NOT NULL, at INTEGER NOT NULL, p2p_ok INTEGER NOT NULL,
                     rpc_ok INTEGER NOT NULL, height INTEGER);
CREATE INDEX checks_at ON checks (at);
INSERT INTO meta VALUES ('schema', '1');
INSERT INTO nodes (ip, first_heard, last_heard, next_check) VALUES ('8.8.8.8', 1, 1, 1);
"""


class MigrationTest(Files):
    def test_schema_two_loses_the_old_private_count(self):
        path = os.path.join(self.dir.name, "two.sqlite")
        database = store.connect(path)
        store.set_meta(database, "schema", "2")
        store.set_meta(database, "unreachable_nodes", "[]")
        database.close()
        database = store.connect(path)
        self.addCleanup(database.close)
        self.assertEqual(store.get_meta(database, "schema"), store.SCHEMA_VERSION)
        self.assertIsNone(store.get_meta(database, "unreachable_nodes"))
        self.assertEqual(database.execute("SELECT count(*) FROM private_nodes").fetchone()[0], 0)

    def test_schema_three_gains_the_column_for_the_daily_try(self):
        path = os.path.join(self.dir.name, "three.sqlite")
        database = store.connect(path)
        database.execute("DROP TABLE private_nodes")
        database.execute("CREATE TABLE private_nodes (tag TEXT PRIMARY KEY, last_seen INTEGER NOT NULL,"
                         " height INTEGER, country TEXT, country_name TEXT, city TEXT, lat REAL, lon REAL,"
                         " asn INTEGER, network TEXT) WITHOUT ROWID")
        database.execute("INSERT INTO private_nodes (tag, last_seen) VALUES ('t', 5)")
        store.set_meta(database, "schema", "3")
        database.close()
        database = store.connect(path)
        self.addCleanup(database.close)
        self.assertEqual(store.get_meta(database, "schema"), store.SCHEMA_VERSION)
        self.assertEqual(tuple(database.execute("SELECT tag, probed FROM private_nodes").fetchone()), ("t", 0))

    def test_schema_one_is_brought_up_to_date(self):
        import sqlite3
        path = os.path.join(self.dir.name, "old.sqlite")
        old = sqlite3.connect(path)
        old.executescript(V1)
        old.close()
        database = store.connect(path)
        self.addCleanup(database.close)
        self.assertEqual(store.get_meta(database, "schema"), store.SCHEMA_VERSION)
        row = database.execute("SELECT ip, located, country FROM nodes").fetchone()
        self.assertEqual(tuple(row), ("8.8.8.8", 0, None))

    def test_unknown_schema_is_refused(self):
        path = os.path.join(self.dir.name, "new.sqlite")
        database = store.connect(path)
        store.set_meta(database, "schema", "99")
        database.close()
        with self.assertRaises(RuntimeError):
            store.connect(path)


if __name__ == "__main__":
    unittest.main()
