"""What the collector remembers, in one SQLite file.

`nodes` has a row per address ever heard of. A row is on the map while
`on_map` is set: the address answered within the last two days. `checks`
keeps the single results of nodes on the map for a limited time.
All times are seconds since 1970, UTC.
"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS nodes (
    ip          TEXT PRIMARY KEY,
    p2p_port    INTEGER NOT NULL DEFAULT 0,   -- 0: not known
    rpc_port    INTEGER NOT NULL DEFAULT 0,   -- 0: none announced
    on_map      INTEGER NOT NULL DEFAULT 0,
    has_p2p     INTEGER NOT NULL DEFAULT 0,   -- as of the last answer
    has_rpc     INTEGER NOT NULL DEFAULT 0,
    height      INTEGER,
    release     TEXT,
    pruned      INTEGER NOT NULL DEFAULT 0,
    misses      INTEGER NOT NULL DEFAULT 0,   -- checks without an answer, in a row
    first_heard INTEGER NOT NULL,
    last_heard  INTEGER NOT NULL,             -- a peer mentioned it, or it answered
    first_ok    INTEGER,
    last_ok     INTEGER,
    last_check  INTEGER,
    next_check  INTEGER NOT NULL,
    asked_peers INTEGER NOT NULL DEFAULT 0,
    tried_rpc   INTEGER NOT NULL DEFAULT 0,   -- the usual RPC port, unannounced
    country     TEXT,                         -- location and network: see geo.py
    country_name TEXT,
    region      TEXT,
    city        TEXT,
    lat         REAL,
    lon         REAL,
    asn         INTEGER,
    network     TEXT,
    located     INTEGER NOT NULL DEFAULT 0    -- version of the data used, 0: not yet
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS nodes_next_check ON nodes (next_check);
CREATE TABLE IF NOT EXISTS checks (
    ip      TEXT NOT NULL,
    at      INTEGER NOT NULL,
    p2p_ok  INTEGER NOT NULL,
    rpc_ok  INTEGER NOT NULL,
    height  INTEGER
);
CREATE INDEX IF NOT EXISTS checks_at ON checks (at);
CREATE TABLE IF NOT EXISTS private_nodes (     -- see own.py; no address in here
    tag          TEXT PRIMARY KEY,             -- keyed fingerprint of the address
    last_seen    INTEGER NOT NULL,
    height       INTEGER,
    country      TEXT,
    country_name TEXT,
    city         TEXT,
    lat          REAL,
    lon          REAL,
    asn          INTEGER,
    network      TEXT,
    probed       INTEGER NOT NULL DEFAULT 0    -- when it was last tried from outside
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS named_nodes (       -- nodes reached by a host name; see config.py
    name       TEXT PRIMARY KEY,
    port       INTEGER NOT NULL,
    height     INTEGER,
    release    TEXT,
    misses     INTEGER NOT NULL DEFAULT 0,
    first_ok   INTEGER,
    last_ok    INTEGER,
    last_check INTEGER,
    asn        INTEGER,                        -- the network its name leads to
    network    TEXT
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS daily (             -- the highest count of each day
    day    TEXT NOT NULL,                      -- YYYY-MM-DD, UTC
    metric TEXT NOT NULL,                      -- kind, country, release, private
    key    TEXT NOT NULL,
    value  INTEGER NOT NULL,
    PRIMARY KEY (day, metric, key)
) WITHOUT ROWID;
"""
SCHEMA_VERSION = "5"

# from one schema version to the next
MIGRATIONS = {
    "1": """
        ALTER TABLE nodes ADD COLUMN country TEXT;
        ALTER TABLE nodes ADD COLUMN country_name TEXT;
        ALTER TABLE nodes ADD COLUMN region TEXT;
        ALTER TABLE nodes ADD COLUMN city TEXT;
        ALTER TABLE nodes ADD COLUMN lat REAL;
        ALTER TABLE nodes ADD COLUMN lon REAL;
        ALTER TABLE nodes ADD COLUMN asn INTEGER;
        ALTER TABLE nodes ADD COLUMN network TEXT;
        ALTER TABLE nodes ADD COLUMN located INTEGER NOT NULL DEFAULT 0;
    """,
    # the tables private_nodes and daily are new and made by SCHEMA; the count
    # of private nodes moves out of meta
    "2": """
        DELETE FROM meta WHERE key IN ('unreachable_nodes', 'unreachable');
    """,
    # private_nodes gains the column `probed`; see _add_missing_columns
    "3": "",
    # the table named_nodes is new and made by SCHEMA
    "4": "",
}

# Columns added to a table that SCHEMA creates. A database from before the
# table existed gets the whole table from SCHEMA; one that has the table in
# its older form gets the column here.
ADDED_COLUMNS = (("private_nodes", "probed", "INTEGER NOT NULL DEFAULT 0"),
                 ("named_nodes", "asn", "INTEGER"), ("named_nodes", "network", "TEXT"))


def _add_missing_columns(db):
    for table, column, kind in ADDED_COLUMNS:
        have = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def connect(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript(SCHEMA)
    db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
    found = get_meta(db, "schema")
    _add_missing_columns(db)
    while found in MIGRATIONS:
        db.executescript("BEGIN;" + MIGRATIONS[found] + "COMMIT;")
        found = str(int(found) + 1)
        set_meta(db, "schema", found)
    if found != SCHEMA_VERSION:
        db.close()
        raise RuntimeError(f"database has schema {found}, this code needs {SCHEMA_VERSION}")
    return db


def get_meta(db, key, default=None):
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(db, key, value):
    db.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
               (key, str(value)))


def count(db):
    return db.execute("SELECT count(*) FROM nodes").fetchone()[0]


def hear(db, ip, p2p_port, rpc_port, now, first_check, room=True):
    """A peer mentioned this address. A new address is stored with its first
    check at `first_check`, unless there is no room. A known one only has its
    mention noted, and a port filled in where the stored one does not work.
    Returns True when the address was new and stored."""
    row = db.execute("SELECT p2p_port, rpc_port, has_p2p, has_rpc, misses FROM nodes WHERE ip = ?",
                     (ip,)).fetchone()
    if row is None:
        if not room:
            return False
        db.execute("INSERT INTO nodes (ip, p2p_port, rpc_port, first_heard, last_heard, next_check)"
                   " VALUES (?, ?, ?, ?, ?, ?)", (ip, p2p_port, rpc_port, now, now, first_check))
        return True
    working = row["misses"] == 0
    new_p2p = p2p_port if p2p_port and not (working and row["has_p2p"]) else row["p2p_port"]
    new_rpc = rpc_port if rpc_port and not (working and row["has_rpc"]) else row["rpc_port"]
    db.execute("UPDATE nodes SET last_heard = ?, p2p_port = ?, rpc_port = ? WHERE ip = ?",
               (now, new_p2p, new_rpc, ip))
    return False


def next_due(db, now):
    """The address to check now: nodes on the map go before addresses that
    are not, so that a backlog of silent addresses never delays them."""
    return db.execute("SELECT * FROM nodes WHERE next_check <= ?"
                      " ORDER BY on_map DESC, next_check LIMIT 1", (now,)).fetchone()


def claim(db, ip, until):
    """Take an address out of the queue while its check is under way. The
    outcome sets the real time of the next check."""
    db.execute("UPDATE nodes SET next_check = ? WHERE ip = ?", (until, ip))


def record_answer(db, ip, now, next_check, *, has_p2p, has_rpc, p2p_port, rpc_port,
                  height, release, pruned):
    db.execute(
        "UPDATE nodes SET on_map = 1, has_p2p = ?, has_rpc = ?, p2p_port = ?, rpc_port = ?,"
        " height = ?, release = ?, pruned = ?, misses = 0, last_heard = ?,"
        " first_ok = coalesce(first_ok, ?), last_ok = ?, last_check = ?, next_check = ?"
        " WHERE ip = ?",
        (int(has_p2p), int(has_rpc), p2p_port, rpc_port, height, release, int(pruned), now,
         now, now, now, next_check, ip))
    db.execute("INSERT INTO checks VALUES (?, ?, ?, ?, ?)",
               (ip, now, int(has_p2p), int(has_rpc), height))


def record_miss(db, ip, now, next_check, on_map, misses):
    row = db.execute("SELECT on_map FROM nodes WHERE ip = ?", (ip,)).fetchone()
    db.execute("UPDATE nodes SET misses = ?, on_map = ?, last_check = ?, next_check = ?"
               " WHERE ip = ?", (misses, int(on_map), now, next_check, ip))
    if row and row["on_map"]:
        db.execute("INSERT INTO checks VALUES (?, ?, 0, 0, NULL)", (ip, now))


def mark_tried_rpc(db, ip, now):
    db.execute("UPDATE nodes SET tried_rpc = ? WHERE ip = ?", (now, ip))


def mark_asked_peers(db, ip, now):
    db.execute("UPDATE nodes SET asked_peers = ? WHERE ip = ?", (now, ip))


def prune(db, now, keep_checks, forget_after):
    db.execute("DELETE FROM checks WHERE at < ?", (now - keep_checks,))
    db.execute("DELETE FROM nodes WHERE on_map = 0 AND last_heard < ?", (now - forget_after,))
    db.execute("DELETE FROM checks WHERE ip NOT IN (SELECT ip FROM nodes)")


def unlocated(db, version):
    """Addresses on the map whose location was not looked up in this data."""
    return [row["ip"] for row in db.execute(
        "SELECT ip FROM nodes WHERE on_map = 1 AND located != ?", (version,))]


def set_place(db, ip, place, version):
    db.execute("UPDATE nodes SET country = ?, country_name = ?, region = ?, city = ?, lat = ?,"
               " lon = ?, asn = ?, network = ?, located = ? WHERE ip = ?",
               (place.country, place.country_name, place.region, place.city, place.lat,
                place.lon, place.asn, place.network, version, ip))


def see_private(db, tag, now, height, place, probed=None):
    """Note a private node. `probed` is given when it was just tried from
    outside; otherwise the time of the last try is kept."""
    db.execute(
        "INSERT INTO private_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (tag) DO UPDATE SET"
        " last_seen = excluded.last_seen, height = excluded.height, country = excluded.country,"
        " country_name = excluded.country_name, city = excluded.city, lat = excluded.lat,"
        " lon = excluded.lon, asn = excluded.asn, network = excluded.network,"
        " probed = CASE WHEN excluded.probed > 0 THEN excluded.probed ELSE probed END",
        (tag, now, height, place.country, place.country_name, place.city, place.lat, place.lon,
         place.asn, place.network, probed or 0))


def private_probed(db, tag):
    row = db.execute("SELECT probed FROM private_nodes WHERE tag = ?", (tag,)).fetchone()
    return row["probed"] if row else 0


def forget_private(db, tag):
    db.execute("DELETE FROM private_nodes WHERE tag = ?", (tag,))


def private_nodes(db, since):
    """The private nodes seen since then, ordered by place."""
    return db.execute(
        "SELECT height, country, country_name, city, lat, lon, asn, network, last_seen"
        " FROM private_nodes WHERE last_seen >= ?"
        " ORDER BY coalesce(country_name, '~'), coalesce(city, '~'), height", (since,)).fetchall()


def note_daily(db, day, metric, key, value):
    """Keep the highest value of the day."""
    db.execute("INSERT INTO daily VALUES (?, ?, ?, ?) ON CONFLICT (day, metric, key)"
               " DO UPDATE SET value = max(value, excluded.value)", (day, metric, key or "", value))


def named(db, name):
    return db.execute("SELECT * FROM named_nodes WHERE name = ?", (name,)).fetchone()


def note_named(db, name, port, now, height=None, release=None):
    """The outcome of a check of a node by name; no height means no answer."""
    db.execute("INSERT OR IGNORE INTO named_nodes (name, port) VALUES (?, ?)", (name, port))
    if height is None:
        db.execute("UPDATE named_nodes SET port = ?, misses = misses + 1, last_check = ? WHERE name = ?",
                   (port, now, name))
    else:
        db.execute("UPDATE named_nodes SET port = ?, height = ?, release = ?, misses = 0,"
                   " first_ok = coalesce(first_ok, ?), last_ok = ?, last_check = ? WHERE name = ?",
                   (port, height, release, now, now, now, name))


def set_named_network(db, name, asn, network):
    db.execute("UPDATE named_nodes SET asn = ?, network = ? WHERE name = ?", (asn, network, name))


def keep_named(db, names):
    """Drop the nodes by name that are no longer in the settings."""
    for row in db.execute("SELECT name FROM named_nodes").fetchall():
        if row["name"] not in names:
            db.execute("DELETE FROM named_nodes WHERE name = ?", (row["name"],))
