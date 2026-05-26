import re
import sys
import time
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
IMAGE_DELAY = 2

PAGES = [
    ("https://nam3forum.com/forums/forum/classifieds/members-parts-for-sale-wtb/e46", "part"),
    ("https://nam3forum.com/forums/forum/classifieds/members-parts-for-sale-wtb/e9x", "part"),
    ("https://nam3forum.com/forums/forum/classifieds/member-vehicle-sales/bmw",        "vehicle"),
]

# Domains/patterns to skip when extracting post images
_SKIP_IMAGE_DOMAINS = (
    "gravatar", "avatar", "rank", "icon", "sprite", "logo",
    "button", "nav", "ad.", "doubleclick", "googlead", "banner",
    "clear.gif", "spacer", "emoji",
)


def fetch_page(url: str) -> BeautifulSoup:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def parse_date(raw: str):
    try:
        dt = datetime.strptime(raw.strip(), "%m-%d-%Y, %I:%M %p")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def scrape_thread_image(thread_url: str):
    """
    Fetch a NAM3Forum (XenForo) thread page and extract the first meaningful
    image from the first post body. Returns image URL or None.
    """
    try:
        time.sleep(IMAGE_DELAY)
        soup = fetch_page(thread_url)

        # XenForo: first post body is article.message or div.message-body
        post_body = (
            soup.find("article", class_=re.compile(r"message"))
            or soup.find("div", class_="message-body")
            or soup.find("div", class_="bbWrapper")
        )
        if not post_body:
            return None

        for img in post_body.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "")
            if not src:
                continue
            # Make relative URLs absolute
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = "https://nam3forum.com" + src
            if not src.startswith("http"):
                continue
            src_lower = src.lower()
            if any(skip in src_lower for skip in _SKIP_IMAGE_DOMAINS):
                continue
            if (any(ext in src_lower for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"))
                    or "attachments" in src_lower
                    or "data/attachments" in src_lower):
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


def scrape_page(url: str) -> list:
    soup = fetch_page(url)
    results = []

    for cell in soup.find_all("td", class_="cell-topic"):
        wrapper = cell.find("div", class_="topic-wrapper")
        if not wrapper:
            continue

        prefix_span = wrapper.find("span", class_="js-prefix")
        if prefix_span and "sticky" in prefix_span.get_text(strip=True).lower():
            continue

        title_tag = wrapper.find("a", class_="topic-title")
        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        thread_url = title_tag["href"]

        label_tag = wrapper.find("a", class_="js-topic-prefix")
        if label_tag:
            label = label_tag.get_text(strip=True).rstrip(":")
            title = f"[{label}] {title}"

        posted_at = None
        info_div = cell.find("div", class_="topic-info")
        if info_div:
            date_span = info_div.find("span", class_="date")
            if date_span:
                posted_at = parse_date(date_span.get_text(strip=True))

        results.append({"title": title, "url": thread_url, "posted_at": posted_at})

    return results


def main():
    init_db()
    saved = 0
    skipped = 0
    images = 0

    for i, (page_url, listing_type) in enumerate(PAGES):
        if i > 0:
            print(f"  Waiting {DELAY}s before next request...")
            time.sleep(DELAY)

        print(f"Scraping {page_url} ...")
        try:
            listings = scrape_page(page_url)
            print(f"  Found {len(listings)} listings ({listing_type}).")
        except requests.HTTPError as e:
            print(f"  Skipped — {e}")
            continue

        for listing in listings:
            t = listing["title"]
            was_saved = insert_listing(
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
            if was_saved:
                saved += 1
                # Fetch image from thread page for new listings only
                image_url = scrape_thread_image(listing["url"])
                if image_url:
                    save_image_url(listing["url"], image_url)
                    images += 1
            else:
                skipped += 1

    print(f"\nDone. Saved: {saved}  |  Images: {images}  |  Skipped (duplicates): {skipped}")


if __name__ == "__main__":
    main()