"""Anchored change sets applied by the implementation agent in brownfield scenarios.

Each op is ("replace", path, old, new) | ("append", path, text) | ("create", path, text).
`replace` requires `old` to occur exactly once; otherwise the patch fails (no fuzzy matching).
"""
S = "src/shortener/"
T = "tests/"

# ---------------------------------------------------------------- expiry (brownfield) ---
EXPIRY_SRC = [
    ("replace", S + "storage.py",
     "links(code TEXT PRIMARY KEY, url TEXT NOT NULL, created_at REAL NOT NULL);",
     "links(code TEXT PRIMARY KEY, url TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL);"),
    ("replace", S + "storage.py",
     '                """\n            )\n',
     '                """\n            )\n'
     '            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(links)")}\n'
     '            if "expires_at" not in cols:  # forward-only, non-destructive migration\n'
     '                self._conn.execute("ALTER TABLE links ADD COLUMN expires_at REAL")\n'),
    ("replace", S + "storage.py", "def insert_link(self, code, url, created_at):",
     "def insert_link(self, code, url, created_at, expires_at=None):"),
    ("replace", S + "storage.py",
     '"INSERT INTO links(code, url, created_at) VALUES (?,?,?)", (code, url, created_at)',
     '"INSERT INTO links(code, url, created_at, expires_at) VALUES (?,?,?,?)", (code, url, created_at, expires_at)'),
    ("replace", S + "storage.py", '"SELECT code, url, created_at FROM links WHERE code=?"',
     '"SELECT code, url, created_at, expires_at FROM links WHERE code=?"'),
    ("replace", S + "storage.py", 'return {"code": row[0], "url": row[1], "created_at": row[2]}',
     'return {"code": row[0], "url": row[1], "created_at": row[2], "expires_at": row[3]}'),
    ("append", S + "errors.py", "\n\nclass ExpiredError(ShortenerError):\n    pass\n"),
    ("append", S + "validation.py",
     "\n\nMAX_TTL_SECONDS = 365 * 24 * 3600\n\n\ndef validate_ttl(ttl):\n"
     "    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= MAX_TTL_SECONDS:\n"
     '        raise ValidationError(f"ttl_seconds must be an integer between 1 and {MAX_TTL_SECONDS}")\n'
     "    return ttl\n"),
    ("replace", S + "service.py", "from .errors import AliasTakenError, CodeGenerationError, NotFoundError",
     "from .errors import AliasTakenError, CodeGenerationError, ExpiredError, NotFoundError"),
    ("replace", S + "service.py", "from .validation import validate_alias, validate_url",
     "from .validation import validate_alias, validate_ttl, validate_url"),
    ("replace", S + "service.py", "def shorten(self, url, alias=None):", "def shorten(self, url, alias=None, ttl_seconds=None):"),
    ("replace", S + "service.py", "        now = self._clock()\n        if alias:",
     "        now = self._clock()\n        expires_at = now + validate_ttl(ttl_seconds) if ttl_seconds is not None else None\n        if alias:"),
    ("replace", S + "service.py", "insert_link(alias, url, now)", "insert_link(alias, url, now, expires_at)"),
    ("replace", S + "service.py", "insert_link(code, url, now)", "insert_link(code, url, now, expires_at)"),
    ("replace", S + "service.py", 'return {"code": code, "short_url": f"{self._base_url}/{code}", "url": url}',
     'return {"code": code, "short_url": f"{self._base_url}/{code}", "url": url, "expires_at": expires_at}'),
    ("replace", S + "service.py", "            raise NotFoundError(code)\n        self._storage.record_click(",
     "            raise NotFoundError(code)\n        if link[\"expires_at\"] is not None and self._clock() >= link[\"expires_at\"]:\n"
     "            raise ExpiredError(code)\n        self._storage.record_click("),
    ("replace", S + "app.py", "    custom_alias: str | None = None\n", "    custom_alias: str | None = None\n    ttl_seconds: int | None = None\n"),
    ("replace", S + "app.py", "class ShortenResponse(BaseModel):\n    code: str\n    short_url: str\n    url: str\n",
     "class ShortenResponse(BaseModel):\n    code: str\n    short_url: str\n    url: str\n    expires_at: float | None = None\n"),
    ("replace", S + "app.py", "from .errors import AliasTakenError, CodeGenerationError, NotFoundError",
     "from .errors import AliasTakenError, CodeGenerationError, ExpiredError, NotFoundError"),
    ("replace", S + "app.py", "svc.shorten(req.url, req.custom_alias)", "svc.shorten(req.url, req.custom_alias, req.ttl_seconds)"),
    ("replace", S + "app.py",
     '        except NotFoundError:\n            raise HTTPException(status_code=404, detail="not found")\n        return RedirectResponse',
     '        except ExpiredError:\n            raise HTTPException(status_code=410, detail="link expired")\n'
     '        except NotFoundError:\n            raise HTTPException(status_code=404, detail="not found")\n        return RedirectResponse'),
]

EXPIRY_TESTS = [
    ("create", T + "test_expiry.py", '''import sqlite3

import pytest
from fastapi.testclient import TestClient

from shortener.app import create_app
from shortener.errors import ExpiredError
from shortener.service import LinkService
from shortener.storage import Storage
from shortener.validation import ValidationError


class Clock:
    t = 1_700_000_000.0

    def __call__(self):
        return self.t


def test_link_expires_after_ttl():
    clock = Clock()
    svc = LinkService(Storage(), clock=clock)
    out = svc.shorten("https://example.com", ttl_seconds=60)
    assert out["expires_at"] == clock.t + 60
    assert svc.resolve(out["code"]) == "https://example.com"
    clock.t += 60
    with pytest.raises(ExpiredError):
        svc.resolve(out["code"])


def test_no_ttl_never_expires():
    clock = Clock()
    svc = LinkService(Storage(), clock=clock)
    code = svc.shorten("https://example.com")["code"]
    clock.t += 10 ** 9
    assert svc.resolve(code) == "https://example.com"


@pytest.mark.parametrize("ttl", [0, -5, True, "x", 10 ** 9])
def test_invalid_ttl_rejected(ttl):
    with pytest.raises(ValidationError):
        LinkService(Storage()).shorten("https://example.com", ttl_seconds=ttl)


def test_migration_upgrades_legacy_database(tmp_path):
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE links(code TEXT PRIMARY KEY, url TEXT NOT NULL, created_at REAL NOT NULL);"
                      "INSERT INTO links VALUES('old1234','https://example.com',1.0);")
    con.commit()
    con.close()
    link = Storage(str(db)).get_link("old1234")
    assert link["url"] == "https://example.com" and link["expires_at"] is None


def test_api_returns_410_for_expired_link():
    clock = Clock()
    client = TestClient(create_app(LinkService(Storage(), clock=clock)), follow_redirects=False)
    code = client.post("/shorten", json={"url": "https://example.com", "ttl_seconds": 5}).json()["code"]
    assert client.get(f"/{code}").status_code == 302
    clock.t += 6
    assert client.get(f"/{code}").status_code == 410
'''),
]

# ---------------------------------------------------------------- rate limit (brownfield) ---
RATELIMIT_SRC = [
    ("create", S + "ratelimit.py", '''import threading
import time
from collections import OrderedDict


class TokenBucketLimiter:
    """Per-key token bucket. Bounded memory: least-recently-seen keys are evicted."""

    def __init__(self, capacity=20, refill_per_sec=1.0, clock=time.monotonic, max_keys=10_000):
        self._capacity = capacity
        self._rate = refill_per_sec
        self._clock = clock
        self._max_keys = max_keys
        self._buckets = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key):
        """Return (allowed, retry_after_seconds)."""
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.pop(key, (float(self._capacity), now))
            tokens = min(self._capacity, tokens + (now - last) * self._rate)
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            self._buckets[key] = (tokens, now)
            while len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)
        retry_after = 0.0 if allowed or self._rate <= 0 else (1.0 - tokens) / self._rate
        return allowed, retry_after
'''),
    ("replace", S + "app.py", "from .service import LinkService", "from .ratelimit import TokenBucketLimiter\nfrom .service import LinkService"),
    ("replace", S + "app.py", "def create_app(service=None):", "def create_app(service=None, rate_limiter=None):"),
    ("replace", S + "app.py", '    app = FastAPI(title="URL Shortener", version="1.0.0")\n',
     '    app = FastAPI(title="URL Shortener", version="1.1.0")\n'
     '    limiter = rate_limiter or TokenBucketLimiter(\n'
     '        capacity=int(os.getenv("RATE_LIMIT_BURST", "20")),\n'
     '        refill_per_sec=float(os.getenv("RATE_LIMIT_PER_SEC", "1")),\n    )\n'),
    ("replace", S + "app.py", "    def shorten(req: ShortenRequest):\n        try:\n",
     "    def shorten(req: ShortenRequest, request: Request):\n"
     '        ok, retry_after = limiter.allow(request.client.host if request.client else "unknown")\n'
     "        if not ok:\n"
     '            raise HTTPException(status_code=429, detail="rate limit exceeded",\n'
     '                                headers={"Retry-After": str(max(1, int(retry_after + 0.999)))})\n'
     "        try:\n"),
]

RATELIMIT_TESTS = [
    ("create", T + "test_ratelimit.py", '''from fastapi.testclient import TestClient

from shortener.app import create_app
from shortener.ratelimit import TokenBucketLimiter


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_bucket_exhausts_and_refills():
    clock = Clock()
    lim = TokenBucketLimiter(capacity=2, refill_per_sec=1.0, clock=clock)
    assert lim.allow("a")[0] and lim.allow("a")[0]
    ok, retry = lim.allow("a")
    assert not ok and 0 < retry <= 1.0
    clock.t += 1.0
    assert lim.allow("a")[0]


def test_keys_are_independent_and_memory_bounded():
    lim = TokenBucketLimiter(capacity=1, refill_per_sec=0.0, max_keys=3)
    assert lim.allow("a")[0] and lim.allow("b")[0]
    assert not lim.allow("a")[0]
    for k in "cdef":
        lim.allow(k)
    assert len(lim._buckets) == 3


def test_api_returns_429_with_retry_after_but_never_limits_redirects():
    client = TestClient(create_app(rate_limiter=TokenBucketLimiter(capacity=2, refill_per_sec=0.001)),
                        follow_redirects=False)
    codes = [client.post("/shorten", json={"url": "https://example.com"}) for _ in range(3)]
    assert [r.status_code for r in codes] == [201, 201, 429]
    assert int(codes[2].headers["Retry-After"]) >= 1
    for _ in range(5):
        assert client.get(f"/{codes[0].json()['code']}").status_code == 302
'''),
]

# ---------------------------------------------------------------- cache + privacy (ambiguous) ---
CACHE_PRIVACY_SRC = [
    ("create", S + "cache.py", '''import threading
import time
from collections import OrderedDict


class LRUCache:
    """Thread-safe LRU with a per-entry TTL, which bounds staleness if invalidation is ever missed."""

    def __init__(self, max_size=10_000, ttl_seconds=60.0, clock=time.monotonic):
        self._max, self._ttl, self._clock = max_size, ttl_seconds, clock
        self._data = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, expires = item
            if self._clock() >= expires:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def put(self, key, value):
        with self._lock:
            self._data[key] = (value, self._clock() + self._ttl)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def pop(self, key):
        with self._lock:
            self._data.pop(key, None)
'''),
    ("create", S + "privacy.py", '''import hashlib
import hmac
import os
import secrets

# Per-process salt unless configured: visitors are pseudonymous and not linkable across restarts.
_SALT = (os.getenv("VISITOR_SALT") or secrets.token_hex(16)).encode()


def hash_visitor(ip):
    """Keyed hash so raw client IPs are never persisted."""
    if not ip:
        return None
    return hmac.new(_SALT, ip.encode(), hashlib.sha256).hexdigest()[:16]
'''),
    ("replace", S + "storage.py", '                """\n            )\n',
     '                """\n            )\n'
     "            # Irreversible data migration: scrub raw IPs stored before pseudonymisation (hashes contain no '.' or ':').\n"
     '            self._conn.execute("UPDATE clicks SET ip=NULL WHERE ip LIKE \'%.%\' OR ip LIKE \'%:%\'")\n'),
    ("replace", S + "service.py", "from .codec import generate_code",
     "from .cache import LRUCache\nfrom .codec import generate_code"),
    ("replace", S + "service.py", "from .validation import validate_alias, validate_url",
     "from .privacy import hash_visitor\nfrom .validation import validate_alias, validate_url"),
    ("replace", S + "service.py", 'clock=time.time, code_len=7):', 'clock=time.time, code_len=7, cache_size=10_000):'),
    ("replace", S + "service.py", "        self._code_len = code_len\n",
     "        self._code_len = code_len\n        self._cache = LRUCache(max_size=cache_size)\n"),
    ("replace", S + "service.py", "    def resolve(self, code, ip=None):\n        link = self._storage.get_link(code)\n",
     "    def _lookup(self, code):\n        link = self._cache.get(code)\n        if link is None:\n"
     "            link = self._storage.get_link(code)\n            if link is not None:\n"
     "                self._cache.put(code, link)\n        return link\n\n"
     "    def resolve(self, code, ip=None):\n        link = self._lookup(code)\n"),
    ("replace", S + "service.py", "self._storage.record_click(code, self._clock(), ip)",
     "self._storage.record_click(code, self._clock(), hash_visitor(ip))"),
    ("replace", S + "service.py", "        if not self._storage.delete_link(code):\n            raise NotFoundError(code)",
     "        deleted = self._storage.delete_link(code)\n        self._cache.pop(code)\n        if not deleted:\n            raise NotFoundError(code)"),
    ("replace", S + "app.py", "import os\n\nfrom fastapi import FastAPI, HTTPException, Request",
     "import os\nimport secrets\n\nfrom fastapi import FastAPI, Header, HTTPException, Request"),
    ("replace", S + "app.py", "    def stats(code: str):\n",
     "    def stats(code: str, x_admin_token: str | None = Header(default=None)):\n"
     '        expected = os.getenv("ADMIN_TOKEN")  # fail closed when unset\n'
     '        if not expected or not secrets.compare_digest(x_admin_token or "", expected):\n'
     '            raise HTTPException(status_code=401, detail="admin token required")\n'),
]

# Updates to pre-existing tests made necessary by the (deliberate) /stats contract change.
CACHE_PRIVACY_TEST_MIGRATION = [
    ("replace", T + "test_api.py", "def client():\n    return TestClient(",
     'def client(monkeypatch):\n    monkeypatch.setenv("ADMIN_TOKEN", "t0k3n")\n    return TestClient('),
    ("replace", T + "test_api.py", 'client.get(f"/stats/{code}").json()', 'client.get(f"/stats/{code}", headers=ADMIN).json()'),
    ("replace", T + "test_api.py", 'client.get("/stats/nope123").status_code == 404',
     'client.get("/stats/nope123", headers=ADMIN).status_code == 404'),
    ("replace", T + "test_api.py", "from shortener.app import create_app\n",
     'from shortener.app import create_app\n\nADMIN = {"X-Admin-Token": "t0k3n"}\n'),
]

CACHE_PRIVACY_TESTS = [
    ("create", T + "test_cache_privacy.py", '''import sqlite3

from fastapi.testclient import TestClient

from shortener.app import create_app
from shortener.cache import LRUCache
from shortener.privacy import hash_visitor
from shortener.service import LinkService
from shortener.storage import Storage


def test_lru_evicts_and_expires():
    t = [0.0]
    c = LRUCache(max_size=2, ttl_seconds=10, clock=lambda: t[0])
    c.put("a", 1); c.put("b", 2); c.get("a"); c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1
    t[0] = 11
    assert c.get("a") is None


class CountingStorage(Storage):
    reads = 0

    def get_link(self, code):
        CountingStorage.reads += 1
        return super().get_link(code)


def test_cache_serves_repeat_reads_and_delete_invalidates():
    svc = LinkService(CountingStorage())
    code = svc.shorten("https://example.com")["code"]
    CountingStorage.reads = 0
    for _ in range(5):
        svc.resolve(code)
    assert CountingStorage.reads == 1
    svc.delete(code)
    import pytest
    from shortener.errors import NotFoundError
    with pytest.raises(NotFoundError):
        svc.resolve(code)


def test_raw_ip_is_never_persisted(tmp_path):
    db = str(tmp_path / "p.db")
    svc = LinkService(Storage(db))
    code = svc.shorten("https://example.com")["code"]
    svc.resolve(code, ip="203.0.113.9")
    stored = [r[0] for r in sqlite3.connect(db).execute("SELECT ip FROM clicks")]
    assert stored and "203.0.113.9" not in stored and stored[0] == hash_visitor("203.0.113.9")


def test_legacy_raw_ips_are_scrubbed_on_migration(tmp_path):
    db = str(tmp_path / "l.db")
    Storage(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO clicks(code, ts, ip) VALUES ('x', 1.0, '198.51.100.7')")
    con.commit(); con.close()
    Storage(db)
    assert sqlite3.connect(db).execute("SELECT ip FROM clicks").fetchone()[0] is None


def test_stats_requires_admin_token_and_fails_closed(monkeypatch):
    client = TestClient(create_app(), follow_redirects=False)
    code = client.post("/shorten", json={"url": "https://example.com"}).json()["code"]
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert client.get(f"/stats/{code}", headers={"X-Admin-Token": "anything"}).status_code == 401
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    assert client.get(f"/stats/{code}").status_code == 401
    assert client.get(f"/stats/{code}", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get(f"/stats/{code}", headers={"X-Admin-Token": "s3cret"}).status_code == 200
'''),
]
