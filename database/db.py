"""
database/db.py — works with SQLite locally and PostgreSQL on Railway.

When DATABASE_URL env var is set, PostgreSQL is used (Railway).
Otherwise falls back to local SQLite at data/listings.db.

Key differences handled automatically:
  - Placeholders:  SQLite uses ?   /  PostgreSQL uses %s
  - Auto-increment: AUTOINCREMENT  /  SERIAL
  - IntegrityError: sqlite3        /  psycopg2 (both caught by string match)
"""
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# ── Classification utilities (shared by scrapers) ────────────────────────────
_WTB_RE     = re.compile(r'\b(wtb|want\s+to\s+buy|looking\s+for)\b', re.IGNORECASE)
_ISO_RE     = re.compile(r'\biso\b', re.IGNORECASE)
_PARTOUT_RE = re.compile(r'\bpart(?:ing)?\s*out\b', re.IGNORECASE)
_FS_RE      = re.compile(r'\b(fs|for\s+sale|wts|selling)\b', re.IGNORECASE)
_PRICE_RE   = re.compile(r'\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)([kK])?')


def detect_post_type(title: str) -> str:
    if _WTB_RE.search(title):     return 'WTB'
    if _ISO_RE.search(title):     return 'ISO'
    if _PARTOUT_RE.search(title): return 'PART OUT'
    if _FS_RE.search(title):      return 'FS'
    return 'UNKNOWN'


def extract_price_float(title: str):
    m = _PRICE_RE.search(title)
    if not m:
        return None
    num = float(m.group(1).replace(',', ''))
    if m.group(2):
        num *= 1000
    return round(num, 2)


# ── DB config ────────────────────────────────────────────────────────────────
DB_PATH      = Path(__file__).parent.parent / "data" / "listings.db"
_DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _is_pg() -> bool:
    return bool(_DATABASE_URL)


def _q(sql: str) -> str:
    """Convert SQLite ? placeholders to PostgreSQL %s (pg8000 uses %s too)."""
    if _is_pg():
        return sql.replace("?", "%s")
    return sql


def get_connection():
    if _is_pg():
        from urllib.parse import urlparse
        import ssl as _ssl
        import pg8000

        parsed = urlparse(_DATABASE_URL)

        # Railway requires SSL; disable cert verification for internal connections
        ssl_ctx = _ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = _ssl.CERT_NONE

        return pg8000.connect(
            host=parsed.hostname,
            database=parsed.path.lstrip("/"),
            user=parsed.username,
            password=parsed.password,
            port=parsed.port or 5432,
            ssl_context=ssl_ctx,
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row, cur):
    """Convert a db row to a plain dict regardless of driver."""
    if row is None:
        return None
    if isinstance(row, (list, tuple)):
        # pg8000 returns plain tuples; get column names from cursor.description
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    return dict(row)  # sqlite3.Row


def _fetchall(cur) -> list:
    rows = cur.fetchall()
    if not rows:
        return []
    return [_row_to_dict(r, cur) for r in rows]


def _fetchone(cur):
    return _row_to_dict(cur.fetchone(), cur)


def _is_unique_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "unique" in msg or "duplicate" in msg


# ── Schema ───────────────────────────────────────────────────────────────────
def init_db():
    """Create tables and run safe migrations. Safe to call on every startup."""
    conn = get_connection()
    try:
        cur = conn.cursor()

        if _is_pg():
            # PostgreSQL — SERIAL for autoincrement, IF NOT EXISTS for migrations
            cur.execute("""
                CREATE TABLE IF NOT EXISTS listings (
                    id           SERIAL PRIMARY KEY,
                    source       TEXT NOT NULL,
                    title        TEXT NOT NULL,
                    url          TEXT NOT NULL UNIQUE,
                    image_url    TEXT,
                    post_text    TEXT,
                    posted_at    TEXT,
                    created_at   TEXT NOT NULL,
                    listing_type TEXT NOT NULL DEFAULT 'part',
                    post_type    TEXT DEFAULT 'UNKNOWN',
                    price        REAL,
                    is_new       INTEGER DEFAULT 0
                )
            """)
            for col, defn in [
                ("listing_type", "TEXT NOT NULL DEFAULT 'part'"),
                ("post_type",    "TEXT DEFAULT 'UNKNOWN'"),
                ("price",        "REAL"),
                ("is_new",       "INTEGER DEFAULT 0"),
            ]:
                cur.execute(
                    f"ALTER TABLE listings ADD COLUMN IF NOT EXISTS {col} {defn}"
                )
            cur.execute("""
                CREATE TABLE IF NOT EXISTS email_signups (
                    id         SERIAL PRIMARY KEY,
                    email      TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
            """)

        else:
            # SQLite — AUTOINCREMENT, try/except migrations
            cur.execute("""
                CREATE TABLE IF NOT EXISTS listings (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    source       TEXT NOT NULL,
                    title        TEXT NOT NULL,
                    url          TEXT NOT NULL UNIQUE,
                    image_url    TEXT,
                    post_text    TEXT,
                    posted_at    TEXT,
                    created_at   TEXT NOT NULL,
                    listing_type TEXT NOT NULL DEFAULT 'part',
                    post_type    TEXT DEFAULT 'UNKNOWN',
                    price        REAL,
                    is_new       INTEGER DEFAULT 0
                )
            """)
            for sql in [
                "ALTER TABLE listings ADD COLUMN listing_type TEXT NOT NULL DEFAULT 'part'",
                "ALTER TABLE listings ADD COLUMN post_type    TEXT DEFAULT 'UNKNOWN'",
                "ALTER TABLE listings ADD COLUMN price        REAL",
                "ALTER TABLE listings ADD COLUMN is_new       INTEGER DEFAULT 0",
            ]:
                try:
                    cur.execute(sql)
                except sqlite3.OperationalError:
                    pass  # column already exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS email_signups (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    email      TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
            """)

        conn.commit()
    finally:
        conn.close()


# ── Queries ──────────────────────────────────────────────────────────────────
def get_listings_by_type(listing_type: str) -> list:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT * FROM listings WHERE listing_type = ? ORDER BY posted_at DESC"),
            (listing_type,),
        )
        return _fetchall(cur)
    finally:
        conn.close()


def get_listing_by_id(listing_id: int):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(_q("SELECT * FROM listings WHERE id = ?"), (listing_id,))
        return _fetchone(cur)
    finally:
        conn.close()


def get_listings_by_source(source: str, exclude_id: int, limit: int = 4) -> list:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT * FROM listings WHERE source = ? AND id != ? "
               "ORDER BY posted_at DESC LIMIT ?"),
            (source, exclude_id, limit),
        )
        return _fetchall(cur)
    finally:
        conn.close()


def insert_listing(source, title, url, image_url, post_text, posted_at,
                   listing_type="part", post_type=None, price=None) -> bool:
    if post_type is None:
        post_type = detect_post_type(title)
    if price is None:
        price = extract_price_float(title)
    created_at = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            _q("""
            INSERT INTO listings
                (source, title, url, image_url, post_text, posted_at,
                 created_at, listing_type, post_type, price, is_new)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """),
            (source, title, url, image_url, post_text, posted_at,
             created_at, listing_type, post_type, price),
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        if _is_unique_error(e):
            return False
        raise
    finally:
        conn.close()


# ── Email signups ─────────────────────────────────────────────────────────────
def init_email_table():
    """Kept for backward compat — init_db() now handles this table."""
    init_db()


def save_email(email: str) -> bool:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            _q("INSERT INTO email_signups (email, created_at) VALUES (?, ?)"),
            (email, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        if _is_unique_error(e):
            return False
        raise
    finally:
        conn.close()
