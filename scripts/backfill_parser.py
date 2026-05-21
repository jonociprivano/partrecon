"""
scripts/backfill_parser.py — one-time script to run the Claude parser
over all existing listings that haven't been parsed yet (ai_parsed = 0).

Run this once after deploying Phase 2 to catch up on your existing ~500 listings.
Cost estimate: 500 listings × ~$0.00003 = ~$0.015 total (less than 2 cents).

Usage:
    cd ~/Desktop/partrecon
    source venv/bin/activate
    python scripts/backfill_parser.py

Progress is saved after every listing so you can safely interrupt and resume.
"""

import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from database.db import get_unparsed_listings, mark_listing_parsed
from scraper.parser import parse_listing


def main():
    logger.info("Starting backfill — fetching all unparsed listings...")

    total_parsed = 0
    total_failed = 0
    batch_size   = 50   # fetch in batches to avoid loading all into memory

    while True:
        batch = get_unparsed_listings(limit=batch_size)
        if not batch:
            break

        logger.info(f"Processing batch of {len(batch)} listings...")

        for row in batch:
            result = parse_listing(row["title"])
            try:
                mark_listing_parsed(
                    listing_id=row["id"],
                    part_type=result["part_type"],
                    condition=result["condition"],
                    normalized_title=result["normalized_title"],
                )
                total_parsed += 1
                logger.info(
                    f"  [{row['id']:4d}] {result['part_type']:<25} "
                    f"{result['condition']:<8} {result['normalized_title'][:55]}"
                )
            except Exception as e:
                total_failed += 1
                logger.warning(f"  [{row['id']}] Save failed: {e}")

            # Small delay to stay well within API rate limits
            time.sleep(0.1)

        if len(batch) < batch_size:
            break   # last batch

    logger.info(f"\nBackfill complete. Parsed: {total_parsed}  Failed: {total_failed}")


if __name__ == "__main__":
    main()
