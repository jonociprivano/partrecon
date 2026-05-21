import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from database.db import init_db, insert_listing, detect_post_type, extract_price_float

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DELAY = 3

# Classic vBulletin layout: td id="td_threadtitle_NNN"
# (base_url, forum_id, label, listing_type)
CLASSIC_FORUMS = [
    # ── bimmerpost.com main (E46, E9x, F80/F82 general) ──────────────────────
    ("https://www.bimmerpost.com/forums", 178, "Exterior / Cosmetic Parts",      "part"),
    ("https://www.bimmerpost.com/forums", 184, "Interior Parts",                 "part"),
    ("https://www.bimmerpost.com/forums", 204, "Turbo Engine / Drivetrain",      "part"),
    ("https://www.bimmerpost.com/forums", 205, "Non-turbo Engine / Drivetrain",  "part"),
    ("https://www.bimmerpost.com/forums", 111, "Wheels and Tires",               "part"),
    ("https://www.bimmerpost.com/forums", 180, "Suspension / Brakes / Chassis",  "part"),
    ("https://www.bimmerpost.com/forums", 96,  "Cars for Sale",                  "vehicle"),

    # ── E46 M3 ────────────────────────────────────────────────────────────────
    ("https://e46m3.bimmerpost.com/forums", 847, "E46 M3 For Sale / Wanted",     "part"),
    ("https://e46m3.bimmerpost.com/forums", 852, "E46 M3 Owners Classifieds",    "part"),

    # ── E90/E92 M3 (m3post.com) ───────────────────────────────────────────────
    ("https://www.m3post.com/forums", 182, "E90/E92 M3 Private Sellers",         "part"),
    ("https://www.m3post.com/forums", 276, "E90/E92 Exterior Parts",             "part"),
    ("https://www.m3post.com/forums", 277, "E90/E92 Wheels and Tires",           "part"),
    ("https://www.m3post.com/forums", 279, "E90/E92 Engine / Drivetrain",        "part"),
    ("https://www.m3post.com/forums", 284, "E90/E92 Cars for Sale",              "vehicle"),

    # ── F80/F82 M3/M4 ─────────────────────────────────────────────────────────
    ("https://f80.bimmerpost.com/forums", 616, "F80/F82 Members Classifieds",    "part"),
    ("https://f80.bimmerpost.com/forums", 617, "F80/F82 Exterior Parts",         "part"),
    ("https://f80.bimmerpost.com/forums", 618, "F80/F82 Interior Parts",         "part"),
    ("https://f80.bimmerpost.com/forums", 619, "F80/F82 Wheels / Tires",         "part"),
    ("https://f80.bimmerpost.com/forums", 622, "F80/F82 Electronics",            "part"),
    ("https://f80.bimmerpost.com/forums", 625, "F80/F82 Cars for Sale",          "vehicle"),

    # ── G80/G82 M3/M4 ─────────────────────────────────────────────────────────
    ("https://g80.bimmerpost.com/forums", 911, "G80/G82 Members Classifieds",    "part"),
    ("https://g80.bimmerpost.com/forums", 912, "G80/G82 Exterior Parts",         "part"),
    ("https://g80.bimmerpost.com/forums", 913, "G80/G82 Interior Parts",         "part"),
    ("https://g80.bimmerpost.com/forums", 921, "G80/G82 Cars for Sale",          "vehicle"),

    # ── F87 M2 ────────────────────────────────────────────────────────────────
    ("https://f87.bimmerpost.com/forums", 657, "F87 M2 Members Classifieds",     "part"),

    # ── G87 M2 ────────────────────────────────────────────────────────────────
    ("https://g87.bimmerpost.com/forums", 979, "G87 M2 Members Classifieds",     "part"),

    # ── F90/G90 M5 ───────────────────────────────────────────────────────────
    ("https://f90.bimmerpost.com/forums", 717, "F90/G90 M5 Members Classifieds", "part"),

    # ── F10 M5 ────────────────────────────────────────────────────────────────
    ("https://f10.m5post.com/forums",     432, "F10 M5 Members Classifieds",     "part"),
]

# Modern vBulletin layout: div id="thread-row-NNN", slug URLs
MODERN_FORUMS = [
    ("https://x3.xbimmers.com", 719, "X3/X4 Members Classifieds", "part"),
]

_CLASSIC_THREAD_RE = re.compile(r"td_threadtitle_(\d+)")
_CLASSIC_DATE_RE   = re.compile(r"\d{2}-\d{2}-\d{4}\s+\d{1,2}:\d{2}\s+[AP]M")
_MODERN_THREAD_RE  = re.compile(r"^thread-row-(\d+)$")
_MODERN_DATE_RE    = re.compile(r"\d{2}-\d{2}-\d{4}")


def fetch_page(url: str) -> BeautifulSoup:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def parse_classic_date(raw: str):
    m = _CLASSIC_DATE_RE.search(raw)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(), "%m-%d-%Y %I:%M %p")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def parse_modern_date(raw: str):
    m = _MODERN_DATE_RE.search(raw)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(), "%m-%d-%Y")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def scrape_classic(base_url: str, forum_id: int) -> list:
    soup = fetch_page(f"{base_url}/forumdisplay.php?f={forum_id}")
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
        posted_at = parse_classic_date(date_td.get_text(" ", strip=True)) if date_td else None

        results.append({"title": title, "url": thread_url, "posted_at": posted_at})

    return results


def scrape_modern(base_url: str, forum_id: int) -> list:
    soup = fetch_page(f"{base_url}/forums/forumdisplay.php?f={forum_id}")
    results = []

    for div in soup.find_all("div", id=_MODERN_THREAD_RE):
        title_div = div.find("div", class_="thread_title")
        if not title_div:
            continue
        title_tag = title_div.find("a")
        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        href = title_tag["href"]
        thread_url = f"{base_url}{href}" if href.startswith("/") else href

        posted_at = None
        author_div = div.find("div", class_="thread_author_info")
        if author_div:
            sr_span = author_div.find("span", class_="sr-only")
            if sr_span:
                posted_at = parse_modern_date(sr_span.get_text(strip=True))

        results.append({"title": title, "url": thread_url, "posted_at": posted_at})

    return results


def domain_of(base_url: str) -> str:
    return base_url.split("//")[-1].split("/")[0]


def main():
    init_db()
    totals = defaultdict(lambda: {"found": 0, "saved": 0, "skipped": 0})
    first = True

    all_forums = (
        [("classic", *f) for f in CLASSIC_FORUMS]
        + [("modern",  *f) for f in MODERN_FORUMS]
    )

    for style, base_url, forum_id, label, listing_type in all_forums:
        if not first:
            print(f"  Waiting {DELAY}s...")
            time.sleep(DELAY)
        first = False

        domain = domain_of(base_url)
        print(f"Scraping [{listing_type}] {label} ({domain} f={forum_id})...")
        try:
            listings = scrape_classic(base_url, forum_id) if style == "classic" else scrape_modern(base_url, forum_id)
            print(f"  Found {len(listings)} threads.")
        except requests.HTTPError as e:
            print(f"  Skipped — {e}")
            continue

        totals[domain]["found"] += len(listings)
        for listing in listings:
            t = listing["title"]
            was_saved = insert_listing(
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
            if was_saved:
                totals[domain]["saved"] += 1
            else:
                totals[domain]["skipped"] += 1

    print("\n--- Totals by subdomain ---")
    grand_saved = grand_skipped = 0
    for domain, counts in totals.items():
        print(f"  {domain:40s}  found={counts['found']:3d}  saved={counts['saved']:3d}  dupes={counts['skipped']:3d}")
        grand_saved   += counts["saved"]
        grand_skipped += counts["skipped"]
    print(f"\n  Grand total — Saved: {grand_saved}  |  Skipped (duplicates): {grand_skipped}")


if __name__ == "__main__":
    main()
