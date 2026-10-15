"""One forum page and one thread; never connects to a database or runs the AI parser."""
import json
import time

from listing_utils import is_available
from scraper.bimmerpost import build_forum_url, parse_modern, parse_thread_image
from scraper.bimmerpost_browser import BrowserPageFetcher


def probe(fetcher):
    url = build_forum_url('https://f80.bimmerpost.com/forums', 619)
    rows = parse_modern(fetcher.load(url), url)
    if not rows:
        raise RuntimeError('No recognized classifieds')
    eligible = [r for r in rows if is_available(r['title']) and r['post_type'] not in ('WTB', 'ISO')]
    report = {'status': 'ok', 'database_writes': 0, 'pages': 1, 'listings': len(rows),
              'sold': sum(not is_available(r['title']) for r in rows),
              'wanted': sum(r['post_type'] in ('WTB', 'ISO') for r in rows),
              'with_price': sum(r['price'] is not None for r in rows),
              'with_posted_date': sum(r['posted_at'] is not None for r in rows),
              'photo_found': False, 'photo_loaded': False}
    if eligible:
        time.sleep(3)
        thread = fetcher.load(eligible[0]['url'])
        report['pages'] += 1
        image = parse_thread_image(thread, eligible[0]['url'])
        report['photo_found'] = bool(image)
        report['photo_loaded'] = bool(image and fetcher.image_loaded(image))
    return report


def main():
    try:
        with BrowserPageFetcher() as fetcher:
            report = probe(fetcher)
    except Exception as error:
        # Omit response HTML, URLs, secrets, cookies and post bodies from logs.
        report = {'status': 'needs_attention', 'database_writes': 0, 'error_type': type(error).__name__}
        response = getattr(error, 'response', None)
        if response is not None:
            report['http_status'] = response.status_code
    print('BIMMERPOST_PROBE ' + json.dumps(report), flush=True)
    return 0 if report['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
