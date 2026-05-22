"""
scripts/backfill_pages.py — one-time backfill of pages 1-2 for all forums.

Scrapes page 1 and page 2 of every Bimmerpost and NAM3Forum forum,
saving any listings not already in the database.

Run once locally:
    cd ~/Desktop/partrecon
    source venv/bin/activate
    export DATABASE_URL=postgresql://postgres:gfpBUMkkBemtEVgycHZnlENTrlgKdJSU@autorack.proxy.rlwy.net:56741/railway
    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/backfill_pages.py
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

import requests
from bs4 import BeautifulSoup
from database.db import init_db, insert_listing, detect_post_type, extract_price_float
from scraper.bimmerpost import (
    CLASSIC_FORUMS, MODERN_FORUMS, scrape_classic, scrape_modern,
    domain_of, USER_AGENT, DELAY
)
from scraper.nam3forum import PAGES as NAM3_PAGES, scrape_page, parse_date

PAGES_TO_SCRAPE = 2


def scrape_classic_page(base_url: str, forum_id: int, page: int) -> list:
    """Scrape a specific page of a classic vBulletin forum."""
    if page == 1:
        url = f"{base_url}/forumdisplay.php?f={forum_id}"
    else:
        url = f"{base_url}/forumdisplay.php?f={forum_id}&page={page}"

    import re
    from datetime import datetime, timezone

    _CLASSIC_THREAD_RE = re.compile(r"td_threadtitle_(\d+)")
    _CLASSIC_DATE_RE   = re.compile(r"\d{2}-\d{2}-\d{4}\s+\d{1,2}:\d{2}\s+[AP]M")

    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results = []

    for td in soup.find_all("td", id=_CLASSIC_THREAD_RE):
        thread_id = _CLASSIC_THREAD_RE.search(td["id"]).group(1)
        row = td.parent

        status_td = row.find("td", id=f"td_threadstatusicon_{thread_id}")
        if status_td:
            icon_div = status_td.find("div")
            if icon_div and any("sticky" in c for c in icon_div.get("class", [])):
                continue

        title_tag = td.find("a", id=f"thread_title_{thread_id}")
        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        thread_url = f"{base_url}/showthread.php?t={thread_id}"

        date_td = row.find("td", class_="alt2", title=lambda t: t and t.startswith("Replies:"))
        posted_at = None
        if date_td:
            m = _CLASSIC_DATE_RE.search(date_td.get_text(" ", strip=True))
            if m:
                try:
                    dt = datetime.strptime(m.group(), "%m-%d-%Y %I:%M %p")
                    posted_at = dt.replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    pass

        results.append({"title": title, "url": thread_url, "posted_at": posted_at})

    return results


def scrape_nam3forum_page(url: str, page: int) -> list:
    """Scrape a specific page of a NAM3Forum section."""
    if page > 1:
        url = f"{url}?page={page}"
    return scrape_page(url)


def main():
    init_db()
    total_saved = 0
    total_skipped = 0
    first = True

    # ── Bimmerpost ────────────────────────────────────────────────────────────
    logger.info("=== Bimmerpost backfill (pages 1-2) ===")
    all_forums = (
        [("classic", *f) for f in CLASSIC_FORUMS]
        + [("modern",  *f) for f in MODERN_FORUMS]
    )

    for style, base_url, forum_id, label, listing_type in all_forums:
        for page in range(1, PAGES_TO_SCRAPE + 1):
            if not first:
                time.sleep(DELAY)
            first = False

            domain = domain_of(base_url)
            logger.info(f"  [{listing_type}] {label} p{page} ({domain} f={forum_id})")
            try:
                if style == "classic":
                    listings = scrape_classic_page(base_url, forum_id, page)
                else:
                    # Modern forums — page param works differently, skip page 2 for now
                    if page > 1:
                        continue
                    listings = scrape_modern(base_url, forum_id)
                logger.info(f"    Found {len(listings)} threads")
            except requests.HTTPError as e:
                logger.warning(f"    Skipped — {e}")
                continue

            for listing in listings:
                t = listing["title"]
                saved = insert_listing(
                    source="Bimmerpost",
                    title=t,
                    url=listing["url"],
                    image_url=None,
                    post_text="",
                    posted_at=listing["posted_at"],
                    listing_type=listing_type,
                    post_type=detect_post_type(t),
                    price=extract_price_float(t),
                )
                if saved:
                    total_saved += 1
                else:
                    total_skipped += 1

    # ── NAM3Forum ─────────────────────────────────────────────────────────────
    logger.info("=== NAM3Forum backfill (pages 1-2) ===")
    for page_url, listing_type in NAM3_PAGES:
        for page in range(1, PAGES_TO_SCRAPE + 1):
            time.sleep(DELAY)
            logger.info(f"  [{listing_type}] {page_url} p{page}")
            try:
                listings = scrape_nam3forum_page(page_url, page)
                logger.info(f"    Found {len(listings)} threads")
            except requests.HTTPError as e:
                logger.warning(f"    Skipped — {e}")
                continue

            for listing in listings:
                t = listing["title"]
                saved = insert_listing(
                    source="NAM3Forum",
                    title=t,
                    url=listing["url"],
                    image_url=None,
                    post_text="",
                    posted_at=listing["posted_at"],
                    listing_type=listing_type,
                    post_type=detect_post_type(t),
                    price=extract_price_float(t),
                )
                if saved:
                    total_saved += 1
                else:
                    total_skipped += 1

    logger.info(f"\n=== Done. Saved: {total_saved} | Skipped (dupes): {total_skipped} ===")

    # Run Claude parser on any new unparsed listings
    if total_saved > 0:
        logger.info("Running Claude parser on new listings...")
        from database.db import get_unparsed_listings, mark_listing_parsed
        from scraper.parser import parse_listing
        unparsed = get_unparsed_listings(limit=200)
        parsed = 0
        for row in unparsed:
            result = parse_listing(row["title"])
            mark_listing_parsed(row["id"], result["part_type"],
                                result["condition"], result["normalized_title"])
            parsed += 1
            time.sleep(0.1)
        logger.info(f"Parsed {parsed} new listings.")


if __name__ == "__main__":
    main()
