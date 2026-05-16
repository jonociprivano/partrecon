"""
Backfill post_type, price, and is_new for all existing listings.
Run once from the project root:
    python scripts/backfill.py
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from database.db import get_connection, detect_post_type, extract_price_float


def compute_is_new(created_at: str) -> int:
    if not created_at:
        return 0
    try:
        dt = datetime.fromisoformat(created_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return 1 if (datetime.now(timezone.utc) - dt) < timedelta(hours=24) else 0
    except Exception:
        return 0


def main():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at FROM listings"
        ).fetchall()

        updates = []
        for row_id, title, created_at in rows:
            updates.append((
                detect_post_type(title),
                extract_price_float(title),
                compute_is_new(created_at),
                row_id,
            ))

        conn.executemany(
            "UPDATE listings SET post_type=?, price=?, is_new=? WHERE id=?",
            updates,
        )
        print(f"Backfilled {len(updates)} rows.\n")

        print("Counts by post_type:")
        for pt, n in conn.execute(
            "SELECT post_type, COUNT(*) FROM listings GROUP BY post_type ORDER BY COUNT(*) DESC"
        ).fetchall():
            print(f"  {pt or 'NULL':12s}  {n}")

        print("\nListings with price extracted:")
        priced = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE price IS NOT NULL"
        ).fetchone()[0]
        print(f"  {priced}")

        print("\nNew listings (last 24h):")
        new = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_new = 1"
        ).fetchone()[0]
        print(f"  {new}")


if __name__ == "__main__":
    main()
