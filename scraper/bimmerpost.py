import re
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlsplit, urlencode
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from database.db import init_db, insert_listing, detect_post_type, extract_price_float, image_backfill_candidates, record_image_check

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


_BROWSER_FETCHER = None


def fetch_page(url: str) -> BeautifulSoup:
    if _BROWSER_FETCHER is not None:
        return _BROWSER_FETCHER.load(url)
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def build_forum_url(base_url: str, forum_id: int, page: int = 1) -> str:
    # Both legacy /forums bases and hostname-only bases occur in the inventory.
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(base_url))
    if origin == "https://f80.bimmerpost.com" and forum_id == 619:
        path = "/forums/showforum/619/wheels-tires"
        query = {"sortby": 1, "sortorder": 0}
    else:
        path = "/forums/forumdisplay.php"
        query = {"f": forum_id, "sortby": 1, "sortorder": 0}
    if page > 1:
        query["page"] = page
    return origin + path + "?" + urlencode(query)


def parse_date(raw: str, now=None):
    if not raw:
        return None
    now = now or datetime.now(timezone.utc)
    relative = re.search(r"\b(\d+)\s+(minute|hour|day|week)s?\s+ago\b", raw, re.I)
    if relative:
        amount, unit = relative.groups()
        delta = timedelta(**{unit.lower() + "s": int(amount)})
        return (now - delta).isoformat()
    if re.search(r"\b(today|yesterday)\b", raw, re.I):
        day = now - timedelta(days=1 if "yesterday" in raw.lower() else 0)
        return day.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.search(raw)
        if m:
            try:
                dt = datetime.strptime(m.group().strip(), fmt)
                return dt.replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                continue
    return None


def scrape_thread_image(thread_url: str):
    """
    Fetch a Bimmerpost thread page and extract the first meaningful image
    from the first post body. Returns image URL or None.
    """
    try:
        time.sleep(IMAGE_DELAY)
        soup = fetch_page(thread_url)

        return parse_thread_image(soup, thread_url)

    except requests.HTTPError:
        raise
    except Exception:
        pass
    return None


def parse_thread_image(soup, thread_url):
    # Stay inside the original post; avatars and reply images are not listing photos.
    article = soup.select_one("article[data-postid]")
    post_body = (article.select_one(".postbit__content") if article else None)
    post_body = (post_body or soup.find("div", id=re.compile(r"post_message_\d+"))
                 or soup.find("div", class_="postbody"))
    if not post_body:
        return None
    for img in post_body.find_all("img"):
        src = urljoin(thread_url, img.get("src", "")) if img.get("src") else ""
        if not src.startswith(("https://", "http://")):
            continue
        lower = src.lower()
        if any(skip in lower for skip in _SKIP_IMAGE_DOMAINS):
            continue
        if any(ext in lower for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", "attachment.php")):
            return src
    return None


def parse_modern(soup, base_url, now=None):
    results = []
    for row in soup.find_all("div", id=_MODERN_THREAD_RE):
        if any("sticky" in cls for cls in row.get("class", [])):
            continue
        anchor = row.select_one(".thread_title a[href]")
        if not anchor:
            continue
        title = anchor.get_text(" ", strip=True)
        badges = row.select_one(".thread_classifieds_badge")
        badge_text = badges.get_text(" ", strip=True) if badges else ""
        if re.search(r"\bSOLD\b", badge_text, re.I):
            title = "[SOLD] " + title
        elif re.search(r"\bWTB\b", badge_text, re.I) and not re.search(r"\bWTB\b", title, re.I):
            title = "[WTB] " + title
        author = row.select_one(".thread_author_info")
        # Deliberately exclude .thread_lastpost: a bump is not the listing date.
        posted_at = parse_date(author.get_text(" ", strip=True), now=now) if author else None
        price_tag = badges.select_one(".c_price") if badges else None
        price = extract_price_float(price_tag.get_text(strip=True)) if price_tag else None
        results.append({"title": title, "url": urljoin(base_url, anchor["href"]),
                        "posted_at": posted_at, "price": price,
                        "post_type": detect_post_type(title)})
    return results


def scrape_classic(base_url: str, forum_id: int) -> list:
    soup = fetch_page(build_forum_url(base_url, forum_id))
    if soup.find("div", id=_MODERN_THREAD_RE):
        return parse_modern(soup, base_url)
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

        results.append({"title": title, "url": thread_url, "posted_at": posted_at})

    return results


def scrape_modern(base_url: str, forum_id: int) -> list:
    return scrape_classic(base_url, forum_id)


def domain_of(base_url: str) -> str:
    return base_url.split("//")[-1].split("/")[0]


def main():
    global _BROWSER_FETCHER
    if os.environ.get("BIMMERPOST_TRANSPORT", "http") == "browser":
        from scraper.bimmerpost_browser import BrowserPageFetcher
        with BrowserPageFetcher() as browser:
            _BROWSER_FETCHER = browser
            try:
                return _run()
            finally:
                _BROWSER_FETCHER = None
    return _run()


def _run():
    init_db()
    failures = fetched = 0
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
            if not listings:
                failures += 1
                print("  No recognized listing rows; source needs attention.")
        except requests.HTTPError as e:
            print(f"  Skipped — {e}")
            failures += 1
            if e.response is not None and e.response.status_code in (401,403,429): break
            continue

        fetched += len(listings)
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
                post_type=listing.get("post_type") or detect_post_type(t),
                price=listing["price"] if "price" in listing else extract_price_float(t),
            )
            if was_saved:
                totals[domain]["saved"] += 1
            else:
                totals[domain]["skipped"] += 1

    if not failures:
        for candidate in image_backfill_candidates("Bimmerpost"):
            try:
                image_url=scrape_thread_image(candidate['url'])
            except requests.HTTPError:
                failures += 1
                break
            record_image_check(candidate['id'],image_url)
            if image_url: totals[domain_of(candidate['url'])]['images'] += 1

    print("\n--- Totals by subdomain ---")
    grand_saved = grand_skipped = grand_images = 0
    for domain, counts in totals.items():
        print(f"  {domain:40s}  found={counts['found']:3d}  saved={counts['saved']:3d}  images={counts['images']:3d}  refreshed={counts['skipped']:3d}")
        grand_saved   += counts["saved"]
        grand_skipped += counts["skipped"]
        grand_images  += counts["images"]
    print(f"\n  Grand total — Saved: {grand_saved}  |  Images: {grand_images}  |  Skipped: {grand_skipped}")

    return {"fetched":fetched,"inserted":grand_saved,"refreshed":grand_skipped,"errors":failures}


if __name__ == "__main__":
    main()