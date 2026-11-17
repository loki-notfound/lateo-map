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
    tried_rpc   INTEGER NOT NULL DEFAULT 0    -- the usual RPC port, unannounced
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
"""
SCHEMA_VERSION = "1"


def connect(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript(SCHEMA)
    db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
    found = get_meta(db, "schema")
    if found != SCHEMA_VERSION:
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
