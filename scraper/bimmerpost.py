import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from database.db import init_db, insert_listing, detect_post_type, extract_price_float, get_connection, _q

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DELAY = 3
IMAGE_DELAY = 2  # seconds between thread-page fetches

CLASSIC_FORUMS = [
    ("https://www.bimmerpost.com/forums", 178, "Exterior / Cosmetic Parts",      "part"),
    ("https://www.bimmerpost.com/forums", 184, "Interior Parts",                 "part"),
    ("https://www.bimmerpost.com/forums", 204, "Turbo Engine / Drivetrain",      "part"),
    ("https://www.bimmerpost.com/forums", 205, "Non-turbo Engine / Drivetrain",  "part"),
    ("https://www.bimmerpost.com/forums", 111, "Wheels and Tires",               "part"),
    ("https://www.bimmerpost.com/forums", 180, "Suspension / Brakes / Chassis",  "part"),
    ("https://www.bimmerpost.com/forums", 96,  "Cars for Sale",                  "vehicle"),
    ("https://e46m3.bimmerpost.com/forums", 847, "E46 M3 For Sale / Wanted",     "part"),
    ("https://e46m3.bimmerpost.com/forums", 852, "E46 M3 Owners Classifieds",    "part"),
    ("https://www.m3post.com/forums", 182, "E90/E92 M3 Private Sellers",         "part"),
    ("https://www.m3post.com/forums", 276, "E90/E92 Exterior Parts",             "part"),
    ("https://www.m3post.com/forums", 277, "E90/E92 Wheels and Tires",           "part"),
    ("https://www.m3post.com/forums", 279, "E90/E92 Engine / Drivetrain",        "part"),
    ("https://www.m3post.com/forums", 284, "E90/E92 Cars for Sale",              "vehicle"),
    ("https://f80.bimmerpost.com/forums", 617, "F80/F82 Exterior Parts",         "part"),
    ("https://f80.bimmerpost.com/forums", 619, "F80/F82 Wheels / Tires",         "part"),
    ("https://f80.bimmerpost.com/forums", 620, "F80/F82 Suspension / Brakes",    "part"),
    ("https://f80.bimmerpost.com/forums", 621, "F80/F82 Engine / Drivetrain",    "part"),
    ("https://f80.bimmerpost.com/forums", 624, "F80/F82 General Parts",          "part"),
    ("https://f80.bimmerpost.com/forums", 625, "F80/F82 Cars for Sale",          "vehicle"),
    ("https://f80.bimmerpost.com/forums", 626, "F80/F82 Interior Parts",         "part"),
    ("https://g80.bimmerpost.com/forums", 916, "G80/G82 Exhaust / Engine",       "part"),
    ("https://g80.bimmerpost.com/forums", 917, "G80/G82 Suspension / Brakes",    "part"),
    ("https://g80.bimmerpost.com/forums", 918, "G80/G82 Exterior / Interior",    "part"),
    ("https://g80.bimmerpost.com/forums", 919, "G80/G82 Wheels / Tires",         "part"),
    ("https://g80.bimmerpost.com/forums", 921, "G80/G82 Cars for Sale",          "vehicle"),
    ("https://f87.bimmerpost.com/forums", 657, "F87 M2 Members Classifieds",     "part"),
    ("https://g87.bimmerpost.com/forums", 979, "G87 M2 Members Classifieds",     "part"),
    ("https://f90.bimmerpost.com/forums", 717, "F90/G90 M5 Members Classifieds", "part"),
    ("https://f10.m5post.com/forums",     432, "F10 M5 Members Classifieds",     "part"),
]

MODERN_FORUMS = [
    ("https://x3.xbimmers.com", 719, "X3/X4 Members Classifieds", "part"),
]

_CLASSIC_THREAD_RE = re.compile(r"td_threadtitle_(\d+)")
_MODERN_THREAD_RE  = re.compile(r"^thread-row-(\d+)$")

_DATE_PATTERNS = [
    (re.compile(r"\d{2}-\d{2}-\d{4}\s+\d{1,2}:\d{2}\s+[AP]M"), "%m-%d-%Y %I:%M %p"),
    (re.compile(r"\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}"),            "%m-%d-%Y %H:%M"),
    (re.compile(r"\d{2}-\d{2}-\d{4}"),                           "%m-%d-%Y"),
    (re.compile(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}"),            "%Y-%m-%d %H:%M"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"),                           "%Y-%m-%d"),
]

# Domains/patterns to skip when looking for post images
_SKIP_IMAGE_DOMAINS = (
    "vbulletin", "smilie", "sprite", "avatar", "rank", "icon",
    "clear.gif", "spacer", "logo", "button", "nav", "ad.",
    "doubleclick", "googlead", "banner",
)


def fetch_page(url: str) -> BeautifulSoup:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def get_session_token(base_url: str) -> str:
    try:
        soup = fetch_page(base_url + "/forums/")
        for a in soup.find_all("a", href=True):
            m = re.search(r"[?&]s=([a-f0-9]{32})", a["href"])
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


_SESSION_CACHE: dict = {}


def build_forum_url(base_url: str, forum_id: int, page: int = 1) -> str:
    if base_url not in _SESSION_CACHE:
        _SESSION_CACHE[base_url] = get_session_token(base_url)
    token = _SESSION_CACHE[base_url]
    if token:
        url = f"{base_url}/forums/forumdisplay.php?s={token}&f={forum_id}"
    else:
        url = f"{base_url}/forumdisplay.php?f={forum_id}"
    if page > 1:
        url += f"&page={page}"
    return url


def parse_date(raw: str):
    if not raw:
        return None
    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.search(raw)
        if m:
            try:
                dt = datetime.strptime(m.group().strip(), fmt)
                return dt.replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                continue
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def scrape_thread_image(thread_url: str):
    """
    Fetch a Bimmerpost thread page and extract the first meaningful image
    from the first post body. Returns image URL or None.
    """
    try:
        time.sleep(IMAGE_DELAY)
        soup = fetch_page(thread_url)

        # vBulletin: first post body is in div.postbody or td.alt1 with class "alt1"
        post_body = (
            soup.find("div", class_="postbody")
            or soup.find("td", class_="alt1")
            or soup.find("div", id=re.compile(r"post_message_\d+"))
        )
        if not post_body:
            return None

        for img in post_body.find_all("img"):
            src = img.get("src", "")
            if not src or not src.startswith("http"):
                continue
            src_lower = src.lower()
            if any(skip in src_lower for skip in _SKIP_IMAGE_DOMAINS):
                continue
            # Must be a real image (jpg/png/gif/webp) or attachmentid pattern
            if (any(ext in src_lower for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"))
                    or "attachmentid" in src_lower
                    or "attachment.php" in src_lower):
                return src

    except Exception:
        pass
    return None


def save_image_url(listing_url: str, image_url: str) -> None:
    """Update image_url for a listing by its URL."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            _q("UPDATE listings SET image_url = ? WHERE url = ? AND image_url IS NULL"),
            (image_url, listing_url)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def scrape_classic(base_url: str, forum_id: int) -> list:
    soup = fetch_page(build_forum_url(base_url, forum_id))
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

        posted_at = None
        for cell in row.find_all("td"):
            text = cell.get_text(" ", strip=True)
            posted_at = parse_date(text)
            if posted_at:
                break

        if not posted_at:
            posted_at = now_iso()

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
            text = author_div.get_text(" ", strip=True)
            posted_at = parse_date(text)

        if not posted_at:
            posted_at = now_iso()

        results.append({"title": title, "url": thread_url, "posted_at": posted_at})

    return results


def domain_of(base_url: str) -> str:
    return base_url.split("//")[-1].split("/")[0]


def main():
    init_db()
    totals = defaultdict(lambda: {"found": 0, "saved": 0, "skipped": 0, "images": 0})
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
                # Fetch image from thread page for new listings only
                image_url = scrape_thread_image(listing["url"])
                if image_url:
                    save_image_url(listing["url"], image_url)
                    totals[domain]["images"] += 1
            else:
                totals[domain]["skipped"] += 1

    print("\n--- Totals by subdomain ---")
    grand_saved = grand_skipped = grand_images = 0
    for domain, counts in totals.items():
        print(f"  {domain:40s}  found={counts['found']:3d}  saved={counts['saved']:3d}  images={counts['images']:3d}  dupes={counts['skipped']:3d}")
        grand_saved   += counts["saved"]
        grand_skipped += counts["skipped"]
        grand_images  += counts["images"]
    print(f"\n  Grand total — Saved: {grand_saved}  |  Images: {grand_images}  |  Skipped: {grand_skipped}")


if __name__ == "__main__":
    main()