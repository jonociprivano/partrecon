"""
database/db.py — works with SQLite locally and PostgreSQL on Railway.
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from listing_utils import parse_price, native_identity, dedupe, is_available, is_marketplace_post

# ── Classification utilities ─────────────────────────────────────────────────
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
    return parse_price(title)


# ── DB config ────────────────────────────────────────────────────────────────
DB_PATH       = Path(__file__).parent.parent / "data" / "listings.db"
_DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _is_pg() -> bool:
    return bool(_DATABASE_URL)


def _q(sql: str) -> str:
    if _is_pg():
        return sql.replace("?", "%s")
    return sql


def get_connection():
    if _is_pg():
        from urllib.parse import urlparse
        import ssl as _ssl
        import pg8000

        parsed = urlparse(_DATABASE_URL)
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
    if row is None:
        return None
    if isinstance(row, (list, tuple)):
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    return dict(row)


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
    conn = get_connection()
    try:
        cur = conn.cursor()

        if _is_pg():
            cur.execute("SELECT pg_advisory_xact_lock(90622002)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS listings (
                    id                SERIAL PRIMARY KEY,
                    source            TEXT NOT NULL,
                    title             TEXT NOT NULL,
                    url               TEXT NOT NULL UNIQUE,
                    image_url         TEXT,
                    post_text         TEXT,
                    posted_at         TEXT,
                    created_at        TEXT NOT NULL,
                    listing_type      TEXT NOT NULL DEFAULT 'part',
                    post_type         TEXT DEFAULT 'UNKNOWN',
                    price             REAL,
                    is_new            INTEGER DEFAULT 0,
                    part_type         TEXT DEFAULT 'Unknown',
                    condition         TEXT DEFAULT 'Unknown',
                    normalized_title  TEXT,
                    ai_parsed         INTEGER DEFAULT 0
                )
            """)
            for col, defn in [
                ("listing_type",     "TEXT NOT NULL DEFAULT 'part'"),
                ("post_type",        "TEXT DEFAULT 'UNKNOWN'"),
                ("price",            "REAL"),
                ("is_new",           "INTEGER DEFAULT 0"),
                ("part_type",        "TEXT DEFAULT 'Unknown'"),
                ("condition",        "TEXT DEFAULT 'Unknown'"),
                ("normalized_title", "TEXT"),
                ("ai_parsed",        "INTEGER DEFAULT 0"),
            ]:
                cur.execute(f"ALTER TABLE listings ADD COLUMN IF NOT EXISTS {col} {defn}")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS email_signups (
                    id         SERIAL PRIMARY KEY,
                    email      TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS submitted_listings (
                    id            SERIAL PRIMARY KEY,
                    title         TEXT NOT NULL,
                    price         REAL,
                    chassis       TEXT,
                    part_type     TEXT,
                    condition     TEXT,
                    location      TEXT,
                    description   TEXT,
                    contact_email TEXT,
                    external_url  TEXT,
                    photo_urls    TEXT,
                    status        TEXT NOT NULL DEFAULT 'pending',
                    created_at    TEXT NOT NULL,
                    reviewed_at   TEXT
                )
            """)

        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS listings (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    source            TEXT NOT NULL,
                    title             TEXT NOT NULL,
                    url               TEXT NOT NULL UNIQUE,
                    image_url         TEXT,
                    post_text         TEXT,
                    posted_at         TEXT,
                    created_at        TEXT NOT NULL,
                    listing_type      TEXT NOT NULL DEFAULT 'part',
                    post_type         TEXT DEFAULT 'UNKNOWN',
                    price             REAL,
                    is_new            INTEGER DEFAULT 0,
                    part_type         TEXT DEFAULT 'Unknown',
                    condition         TEXT DEFAULT 'Unknown',
                    normalized_title  TEXT,
                    ai_parsed         INTEGER DEFAULT 0
                )
            """)
            for sql in [
                "ALTER TABLE listings ADD COLUMN listing_type     TEXT NOT NULL DEFAULT 'part'",
                "ALTER TABLE listings ADD COLUMN post_type        TEXT DEFAULT 'UNKNOWN'",
                "ALTER TABLE listings ADD COLUMN price            REAL",
                "ALTER TABLE listings ADD COLUMN is_new           INTEGER DEFAULT 0",
                "ALTER TABLE listings ADD COLUMN part_type        TEXT DEFAULT 'Unknown'",
                "ALTER TABLE listings ADD COLUMN condition        TEXT DEFAULT 'Unknown'",
                "ALTER TABLE listings ADD COLUMN normalized_title TEXT",
                "ALTER TABLE listings ADD COLUMN ai_parsed        INTEGER DEFAULT 0",
            ]:
                try:
                    cur.execute(sql)
                except sqlite3.OperationalError:
                    pass

            cur.execute("""
                CREATE TABLE IF NOT EXISTS email_signups (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    email      TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS submitted_listings (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    title         TEXT NOT NULL,
                    price         REAL,
                    chassis       TEXT,
                    part_type     TEXT,
                    condition     TEXT,
                    location      TEXT,
                    description   TEXT,
                    contact_email TEXT,
                    external_url  TEXT,
                    photo_urls    TEXT,
                    status        TEXT NOT NULL DEFAULT 'pending',
                    created_at    TEXT NOT NULL,
                    reviewed_at   TEXT
                )
            """)

        for name in ('last_checked_at','image_checked_at'):
            if _is_pg():
                cur.execute(f"ALTER TABLE listings ADD COLUMN IF NOT EXISTS {name} TEXT")
            else:
                columns={r[1] for r in cur.execute('PRAGMA table_info(listings)')}
                if name not in columns: cur.execute(f"ALTER TABLE listings ADD COLUMN {name} TEXT")
        conn.commit()
    finally:
        conn.close()


# ── Scraped listing queries ───────────────────────────────────────────────────
def get_listings_by_type(listing_type: str) -> list:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT * FROM listings WHERE listing_type = ? ORDER BY posted_at DESC"),
            (listing_type,),
        )
        rows=dedupe(_fetchall(cur))
        return [r for r in rows if is_available(r['title']) and is_marketplace_post(r['title'],r['source'])]
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
    if not is_marketplace_post(title,source):
        return False
    post_type=post_type or detect_post_type(title)
    if price is None: price=extract_price_float(title)
    now=datetime.now(timezone.utc).isoformat()
    conn=get_connection()
    try:
        cur=conn.cursor()
        if _is_pg(): cur.execute("SELECT pg_advisory_xact_lock(90622001)")
        else: cur.execute("BEGIN IMMEDIATE")
        cur.execute(_q("SELECT * FROM listings WHERE source = ? ORDER BY id DESC"),(source,))
        key=native_identity(source,url)
        existing=next((r for r in _fetchall(cur) if native_identity(source,r['url'])==key),None)
        if existing:
            changed=existing['title']!=title
            cur.execute(_q("""UPDATE listings SET title=?, price=?, post_type=?, listing_type=?,
                image_url=COALESCE(NULLIF(?,''), image_url), post_text=COALESCE(NULLIF(?,''),post_text),
                posted_at=COALESCE(posted_at,?), last_checked_at=?,
                normalized_title=CASE WHEN ? THEN ? ELSE normalized_title END,
                ai_parsed=CASE WHEN ? THEN 0 ELSE ai_parsed END WHERE id=?"""),
                (title,price,post_type,listing_type,image_url,post_text,posted_at,now,changed,title,changed,existing['id']))
            conn.commit()
            return False
        cur.execute(_q("""INSERT INTO listings
            (source,title,url,image_url,post_text,posted_at,created_at,listing_type,post_type,price,is_new,part_type,condition,normalized_title,ai_parsed,last_checked_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,1,'Unknown','Unknown',?,0,?)"""),
            (source,title,url,image_url,post_text,posted_at,now,listing_type,post_type,price,title,now))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_listing_parsed(listing_id: int, part_type: str,
                        condition: str, normalized_title: str) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            _q("""
            UPDATE listings
               SET part_type = ?,
                   condition = ?,
                   normalized_title = ?,
                   ai_parsed = 1
             WHERE id = ?
            """),
            (part_type, condition, normalized_title, listing_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_unparsed_listings(limit: int = 50) -> list:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT id, title FROM listings WHERE ai_parsed = 0 ORDER BY id DESC LIMIT ?"),
            (limit,),
        )
        return _fetchall(cur)
    finally:
        conn.close()


# ── Email signups ─────────────────────────────────────────────────────────────
def init_email_table():
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


# ── Submitted listings ────────────────────────────────────────────────────────
def insert_submitted_listing(title, price, chassis, part_type, condition,
                              location, description, contact_email,
                              external_url, photo_urls) -> int:
    photos_json = json.dumps(photo_urls) if isinstance(photo_urls, list) else photo_urls
    created_at = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            _q("""
            INSERT INTO submitted_listings
                (title, price, chassis, part_type, condition, location,
                 description, contact_email, external_url, photo_urls, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """),
            (title, price, chassis, part_type, condition, location,
             description, contact_email, external_url, photos_json, created_at)
        )
        conn.commit()
        if _is_pg():
            cur.execute("SELECT lastval()")
        else:
            cur.execute("SELECT last_insert_rowid()")
        row = cur.fetchone()
        return row[0]
    finally:
        conn.close()


def get_submitted_listings(status='pending') -> list:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT * FROM submitted_listings WHERE status = ? ORDER BY created_at DESC"),
            (status,)
        )
        rows = _fetchall(cur)
        for r in rows:
            try:
                r['photo_urls_list'] = json.loads(r.get('photo_urls') or '[]')
            except Exception:
                r['photo_urls_list'] = []
        return rows
    finally:
        conn.close()


def get_pending_count() -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM submitted_listings WHERE status = 'pending'")
        row = cur.fetchone()
        return (row[0]) or 0
    finally:
        conn.close()


def update_submitted_listing_status(listing_id: int, status: str):
    reviewed_at = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            _q("UPDATE submitted_listings SET status = ?, reviewed_at = ? WHERE id = ?"),
            (status, reviewed_at, listing_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_approved_submitted_listings() -> list:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM submitted_listings WHERE status = 'approved' ORDER BY created_at DESC"
        )
        rows = _fetchall(cur)
        for r in rows:
            try:
                r['photo_urls_list'] = json.loads(r.get('photo_urls') or '[]')
            except Exception:
                r['photo_urls_list'] = []
        return rows
    finally:
        conn.close()

def get_approved_submission(listing_id):
    conn=get_connection()
    try:
        cur=conn.cursor()
        cur.execute(_q("SELECT * FROM submitted_listings WHERE id=? AND status='approved'"),(listing_id,))
        row=_fetchone(cur)
        if row:
            try: row['photo_urls_list']=json.loads(row.get('photo_urls') or '[]')
            except (ValueError,TypeError): row['photo_urls_list']=[]
        return row
    finally: conn.close()


def image_backfill_candidates(source,limit=8):
    from datetime import timedelta
    cutoff=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    conn=get_connection()
    try:
        cur=conn.cursor()
        cur.execute(_q("""SELECT * FROM listings WHERE source=? AND (image_url IS NULL OR image_url='')
            AND (image_checked_at IS NULL OR image_checked_at<?) ORDER BY id DESC LIMIT ?"""),(source,cutoff,limit))
        return [r for r in dedupe(_fetchall(cur)) if is_available(r['title'])]
    finally: conn.close()


def record_image_check(listing_id,image_url):
    conn=get_connection()
    try:
        cur=conn.cursor()
        cur.execute(_q("UPDATE listings SET image_url=COALESCE(NULLIF(?,''),image_url),image_checked_at=? WHERE id=?"),
            (image_url,datetime.now(timezone.utc).isoformat(),listing_id))
        conn.commit()
    finally: conn.close()
