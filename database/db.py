import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "listings.db"

# ── Shared classification utilities ──────────────────────────────────────────
_WTB_RE     = re.compile(r'\b(wtb|want\s+to\s+buy|looking\s+for)\b', re.IGNORECASE)
_ISO_RE     = re.compile(r'\biso\b', re.IGNORECASE)
_PARTOUT_RE = re.compile(r'\bpart(?:ing)?\s*out\b', re.IGNORECASE)
_FS_RE      = re.compile(r'\b(fs|for\s+sale|wts|selling)\b', re.IGNORECASE)
_PRICE_RE   = re.compile(r'\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)([kK])?')


def detect_post_type(title: str) -> str:
    """Return 'WTB', 'ISO', 'PART OUT', 'FS', or 'UNKNOWN'."""
    if _WTB_RE.search(title):     return 'WTB'
    if _ISO_RE.search(title):     return 'ISO'
    if _PARTOUT_RE.search(title): return 'PART OUT'
    if _FS_RE.search(title):      return 'FS'
    return 'UNKNOWN'


def extract_price_float(title: str):
    """Return price as float (e.g. 1200.0) or None if not found."""
    m = _PRICE_RE.search(title)
    if not m:
        return None
    num = float(m.group(1).replace(',', ''))
    if m.group(2):          # k / K suffix
        num *= 1000
    return round(num, 2)


# ── Database ──────────────────────────────────────────────────────────────────
def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    with get_connection() as conn:
        conn.execute("""
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
        # Safe migrations — silently skip if column already exists
        migrations = [
            "ALTER TABLE listings ADD COLUMN listing_type TEXT NOT NULL DEFAULT 'part'",
            "ALTER TABLE listings ADD COLUMN post_type    TEXT DEFAULT 'UNKNOWN'",
            "ALTER TABLE listings ADD COLUMN price        REAL",
            "ALTER TABLE listings ADD COLUMN is_new       INTEGER DEFAULT 0",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass


def init_email_table():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_signups (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
        """)


def save_email(email: str) -> bool:
    with get_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO email_signups (email, created_at) VALUES (?, ?)",
                (email, datetime.now(timezone.utc).isoformat()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_listing_by_id(listing_id: int):
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        return dict(row) if row else None


def get_listings_by_source(source: str, exclude_id: int, limit: int = 4):
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM listings WHERE source = ? AND id != ? ORDER BY posted_at DESC LIMIT ?",
            (source, exclude_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_listings_by_type(listing_type: str):
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM listings WHERE listing_type = ? ORDER BY posted_at DESC",
            (listing_type,),
        ).fetchall()
        return [dict(row) for row in rows]


def insert_listing(source, title, url, image_url, post_text, posted_at,
                   listing_type="part", post_type=None, price=None):
    if post_type is None:
        post_type = detect_post_type(title)
    if price is None:
        price = extract_price_float(title)
    created_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        try:
            conn.execute(
                """
                INSERT INTO listings
                    (source, title, url, image_url, post_text, posted_at,
                     created_at, listing_type, post_type, price, is_new)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (source, title, url, image_url, post_text, posted_at,
                 created_at, listing_type, post_type, price),
            )
            return True
        except sqlite3.IntegrityError:
            return False
