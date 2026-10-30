"""SQLite cache so runs can resume, re-runs don't pay twice, and new installs get flagged."""
import json
import sqlite3
import threading
import time
from contextlib import closing

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS tiles_done (key TEXT PRIMARY KEY, done_at REAL);
CREATE TABLE IF NOT EXISTS arrays (osm_id TEXT PRIMARY KEY, state TEXT, data TEXT, first_seen REAL);
CREATE TABLE IF NOT EXISTS lookups (kind TEXT, site_id TEXT, data TEXT, at REAL, PRIMARY KEY (kind, site_id));
CREATE TABLE IF NOT EXISTS sites_seen (site_id TEXT PRIMARY KEY, state TEXT, first_seen REAL, last_seen REAL);
"""


class Store:
    def __init__(self, path: str | None = None):
        import os

        path = path or settings.db_path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()  # one connection shared by worker threads

    # --- map tiles / arrays -------------------------------------------------
    def done_tiles(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT key FROM tiles_done")}

    def save_tile(self, key: str, arrays: list[dict]) -> None:
        now = time.time()
        with self.db:
            for a in arrays:
                self.db.execute(
                    "INSERT INTO arrays(osm_id,state,data,first_seen) VALUES(?,?,?,?) "
                    "ON CONFLICT(osm_id) DO UPDATE SET data=excluded.data",
                    (a["osm_id"], a["state"], json.dumps(a), now),
                )
            self.db.execute("INSERT OR REPLACE INTO tiles_done(key,done_at) VALUES(?,?)", (key, now))

    def reset_tiles(self, state: str) -> None:
        """Forget which tiles were scanned so the next run re-reads the map (used by the monthly refresh)."""
        with self.db:
            self.db.execute("DELETE FROM tiles_done WHERE key LIKE ?", (f"{state}:%",))

    def arrays(self, state: str) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM arrays WHERE state=?", (state,))]

    # --- paid lookups (Regrid, roof checks) are cached forever -------------
    def get(self, kind: str, site_id: str):
        with self.lock:
            r = self.db.execute("SELECT data FROM lookups WHERE kind=? AND site_id=?", (kind, site_id)).fetchone()
        return json.loads(r[0]) if r else None

    def put(self, kind: str, site_id: str, data) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO lookups(kind,site_id,data,at) VALUES(?,?,?,?)",
                            (kind, site_id, json.dumps(data), time.time()))

    # --- new-site tracking ---------------------------------------------------
    def mark_seen(self, state: str, site_ids: list[str]) -> set[str]:
        """Record sites seen this run; return the ids that were never seen before."""
        now = time.time()
        known = {r[0] for r in self.db.execute("SELECT site_id FROM sites_seen WHERE state=?", (state,))}
        first_run = not known
        new = set()
        with self.db:
            for sid in site_ids:
                if sid in known:
                    self.db.execute("UPDATE sites_seen SET last_seen=? WHERE site_id=?", (now, sid))
                else:
                    self.db.execute("INSERT INTO sites_seen VALUES(?,?,?,?)", (sid, state, now, now))
                    new.add(sid)
        return set() if first_run else new

    def close(self) -> None:
        with closing(self.db):
            pass
