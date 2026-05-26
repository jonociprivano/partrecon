import re
import sys
from datetime import datetime, timezone
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).parent.parent))
from database.db import init_db, insert_listing, detect_post_type, extract_price_float

USER_AGENT = "PartRecon/0.1"
SUBREDDITS = ["BimmerMarket", "E46", "E90", "F80", "BMWE46"]
LIMIT = 25

def fetch_new_posts(subreddit: str) -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit={LIMIT}"
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()["data"]["children"]

ANCHORED_SIGNALS = ["fs", "wts", "iso"]
ANYWHERE_SIGNALS = ["for sale", "wtb", "want to buy", "$"]
SKIP_SIGNALS     = ["weekly", "thread"]

_ANCHORED_RE = re.compile(
    "|".join(
        rf"(^{re.escape(s)}\b|[\[\(]{re.escape(s)}[\]\)])"
        for s in ANCHORED_SIGNALS
    ),
    re.IGNORECASE,
)

def is_listing(title: str) -> bool:
    lower = title.lower()
    if any(skip in lower for skip in SKIP_SIGNALS):
        return False
    if any(signal in lower for signal in ANYWHERE_SIGNALS):
        return True
    return bool(_ANCHORED_RE.search(title))

def parse_image_url(data: dict):
    """
    Try to get the best available image URL from a Reddit post.
    Priority:
    1. preview.images[0].resolutions — largest available preview (capped ~960px)
    2. preview.images[0].source     — full resolution preview
    3. url_overridden_by_dest       — direct image link (imgur, i.redd.it, etc.)
    4. thumbnail                    — small fallback
    """
    # 1 & 2: Reddit preview images (most reliable, works for most posts with images)
    preview = data.get("preview", {})
    images = preview.get("images", [])
    if images:
        resolutions = images[0].get("resolutions", [])
        if resolutions:
            # Pick largest resolution under 960px wide
            best = resolutions[-1]
            url = best.get("url", "").replace("&amp;", "&")
            if url.startswith("https://"):
                return url
        source = images[0].get("source", {})
        url = source.get("url", "").replace("&amp;", "&")
        if url.startswith("https://"):
            return url

    # 3: Direct image URL (imgur, i.redd.it)
    url_dest = data.get("url_overridden_by_dest", "")
    if url_dest and any(url_dest.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return url_dest

    # 4: Thumbnail fallback
    thumbnail = data.get("thumbnail", "")
    if thumbnail and thumbnail.startswith("http") and thumbnail not in ("self", "default", "nsfw", "spoiler"):
        return thumbnail

    return None

def parse_posted_at(data: dict) -> str:
    return datetime.fromtimestamp(data["created_utc"], tz=timezone.utc).isoformat()

def main():
    init_db()
    saved = 0
    filtered = 0
    skipped = 0
    for subreddit in SUBREDDITS:
        print(f"Fetching r/{subreddit}...")
        try:
            posts = fetch_new_posts(subreddit)
        except requests.HTTPError as e:
            print(f"  Skipped — {e}")
            continue
        for post in posts:
            data = post["data"]
            title = data["title"]
            if not is_listing(title):
                filtered += 1
                continue
            url = f"https://reddit.com{data['permalink']}"
            was_saved = insert_listing(
                source=subreddit,
                title=title,
                url=url,
                image_url=parse_image_url(data),
                post_text=data.get("selftext", ""),
                posted_at=parse_posted_at(data),
                listing_type="part",
                post_type=detect_post_type(title),
                price=extract_price_float(title),
            )
            if was_saved:
                saved += 1
            else:
                skipped += 1
    print(f"\nDone. Saved: {saved}  |  Filtered out: {filtered}  |  Skipped (duplicates): {skipped}")

if __name__ == "__main__":
    main()