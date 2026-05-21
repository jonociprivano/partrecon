"""
scraper/runner.py — unified entry point for all scrapers + Claude parser.

Usage:
    python -m scraper.runner reddit
    python -m scraper.runner nam3forum
    python -m scraper.runner bimmerpost

Railway cron jobs call this with the appropriate argument.
After scraping, runs the Claude parser on any new (ai_parsed=0) listings.
"""

import sys
import logging
from pathlib import Path

# Make sure project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_scraper(name: str) -> int:
    """Run the named scraper. Returns number of new listings saved."""
    if name == "reddit":
        from scraper.fetch import main
    elif name == "nam3forum":
        from scraper.nam3forum import main
    elif name == "bimmerpost":
        from scraper.bimmerpost import main
    else:
        raise ValueError(f"Unknown scraper: {name!r}. Use reddit, nam3forum, or bimmerpost.")

    logger.info(f"Starting {name} scraper...")
    main()
    logger.info(f"{name} scraper complete.")


def run_parser():
    """
    Parse any new listings that haven't been through Claude yet.
    Runs after every scrape to keep latency low.
    """
    from database.db import get_unparsed_listings, mark_listing_parsed
    from scraper.parser import parse_listing

    unparsed = get_unparsed_listings(limit=50)
    if not unparsed:
        logger.info("Parser: no new listings to parse.")
        return

    logger.info(f"Parser: processing {len(unparsed)} new listings...")
    parsed = 0
    failed = 0

    for row in unparsed:
        result = parse_listing(row["title"])
        try:
            mark_listing_parsed(
                listing_id=row["id"],
                part_type=result["part_type"],
                condition=result["condition"],
                normalized_title=result["normalized_title"],
            )
            parsed += 1
            logger.info(
                f"  [{row['id']}] {result['part_type']} | {result['condition']} "
                f"| {result['normalized_title'][:60]}"
            )
        except Exception as e:
            failed += 1
            logger.warning(f"  [{row['id']}] Failed to save parse result: {e}")

    logger.info(f"Parser: done. Parsed={parsed} Failed={failed}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scraper.runner <reddit|nam3forum|bimmerpost>")
        sys.exit(1)

    scraper_name = sys.argv[1].lower()

    try:
        run_scraper(scraper_name)
        run_parser()
    except Exception as e:
        logger.error(f"Runner failed: {e}")
        sys.exit(1)
