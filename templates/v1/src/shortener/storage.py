import sqlite3
import threading


class Storage:
    """SQLite-backed store. One connection guarded by a lock (adequate for a single node)."""

    def __init__(self, path=":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        self._migrate()

    def _migrate(self):
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS links(code TEXT PRIMARY KEY, url TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS clicks(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, ts REAL NOT NULL, ip TEXT);
                CREATE INDEX IF NOT EXISTS idx_clicks_code ON clicks(code);
                """
            )

    def insert_link(self, code, url, created_at):
        with self._lock, self._conn:
            try:
                self._conn.execute("INSERT INTO links(code, url, created_at) VALUES (?,?,?)", (code, url, created_at))
            except sqlite3.IntegrityError:
                return False
        return True

    def get_link(self, code):
        with self._lock:
            row = self._conn.execute("SELECT code, url, created_at FROM links WHERE code=?", (code,)).fetchone()
        if row is None:
            return None
        return {"code": row[0], "url": row[1], "created_at": row[2]}

    def delete_link(self, code):
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM links WHERE code=?", (code,))
            self._conn.execute("DELETE FROM clicks WHERE code=?", (code,))
        return cur.rowcount > 0

    def record_click(self, code, ts, ip):
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO clicks(code, ts, ip) VALUES (?,?,?)", (code, ts, ip))

    def click_stats(self, code):
        with self._lock:
            total, last = self._conn.execute("SELECT COUNT(*), MAX(ts) FROM clicks WHERE code=?", (code,)).fetchone()
            days = self._conn.execute(
                "SELECT date(ts,'unixepoch'), COUNT(*) FROM clicks WHERE code=? GROUP BY 1 ORDER BY 1", (code,)
            ).fetchall()
        return {"total_clicks": total, "last_access": last, "clicks_by_day": {d: c for d, c in days}}
