import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import db
from listing_utils import is_available
from scraper import bimmerpost as bp
from scraper import bimmerpost_probe as probe_module
from scraper.bimmerpost_browser import BrowserPageFetcher

BASE = 'https://f80.bimmerpost.com/forums'
THREAD = '''<article data-postid="1"><aside><img src="/avatar.jpg"></aside>
<section><div class="postbit__content"><img src="/forums/attachment.php?attachmentid=4047077"></div></section></article>
<article data-postid="2"><div class="postbit__content"><img src="https://example.com/reply.jpg"></div></article>'''


class CurrentBimmerpostTests(unittest.TestCase):
    def setUp(self):
        self.soup = BeautifulSoup((Path(__file__).parent / 'fixtures/bimmerpost_modern.html').read_text(), 'html.parser')
        self.now = datetime(2026, 10, 15, 4, tzinfo=timezone.utc)
        self.rows = bp.parse_modern(self.soup, BASE, now=self.now)

    def test_live_fixture_prices_separate_status_and_original_dates(self):
        by_id = {r['url'].split('/showthread/')[1].split('/')[0]: r for r in self.rows}
        self.assertEqual(len(self.rows), 36)
        self.assertEqual(by_id['2260044']['price'], 900)
        self.assertEqual(by_id['2259876']['price'], 1200)
        self.assertEqual(by_id['2260044']['posted_at'], (self.now-timedelta(hours=9)).isoformat())
        self.assertFalse(is_available(by_id['2259068']['title']))
        self.assertEqual(by_id['2259504']['post_type'], 'WTB')
        self.assertEqual(by_id['2259716']['posted_at'], '2026-10-12T00:00:00+00:00')
        self.assertTrue(all(r['url'].startswith(BASE+'/showthread/') for r in self.rows))

    def test_relative_and_unknown_dates(self):
        self.assertEqual(bp.parse_date('Posted on Yesterday', self.now), '2026-10-14T00:00:00+00:00')
        self.assertEqual(bp.parse_date('Posted on 2 days ago', self.now), '2026-10-13T04:00:00+00:00')
        self.assertIsNone(bp.parse_date('Unknown'))
        soup=BeautifulSoup('<div id="thread-row-1"><div class="thread_title"><a href="/forums/showthread/1/a">Wheel</a></div><div class="thread_lastpost">10-14-2026</div></div>', 'html.parser')
        self.assertIsNone(bp.parse_modern(soup, BASE)[0]['posted_at'])

    def test_classic_inventory_autodetects_modern_markup(self):
        with patch.object(bp, 'fetch_page', return_value=self.soup):
            self.assertEqual(len(bp.scrape_classic(BASE, 619)), 36)

    def test_url_normalization_without_session_harvesting(self):
        known = bp.build_forum_url(BASE, 619)
        self.assertIn('/forums/showforum/619/wheels-tires?sortby=1&sortorder=0', known)
        for base in (BASE, 'https://x3.xbimmers.com'):
            url=bp.build_forum_url(base, 719, page=2)
            self.assertNotIn('/forums/forums/', url)
            self.assertEqual(url.count('/forums/'), 1)
            self.assertIn('page=2', url)
            self.assertNotIn('s=',url)

    def test_relative_photo_is_from_first_post_not_avatar_or_reply(self):
        soup=BeautifulSoup(THREAD, 'html.parser')
        self.assertEqual(bp.parse_thread_image(soup, BASE+'/showthread/1/wheel'),BASE+'/attachment.php?attachmentid=4047077')
        soup.select_one('.postbit__content img').decompose()
        self.assertIsNone(bp.parse_thread_image(soup, BASE+'/showthread/1/wheel'))

    def test_probe_has_no_database_or_parser_access(self):
        fetcher=Mock()
        fetcher.load.side_effect=[self.soup, BeautifulSoup(THREAD, 'html.parser')]
        fetcher.image_loaded.return_value=True
        with patch.object(db, 'get_connection', side_effect=AssertionError('No database access')),patch.object(db, 'init_db', side_effect=AssertionError('No schema changes')),patch.object(probe_module.time, 'sleep'):
            report=probe_module.probe(fetcher)
        self.assertEqual(report['database_writes'],0)
        self.assertEqual(report['listings'],36)
        self.assertTrue(report['photo_found'])
        self.assertTrue(report['photo_loaded'])
        self.assertEqual(fetcher.load.call_count,2)

    def test_probe_denied_stops_after_one_request(self):
        fetcher=Mock()
        response=requests.Response();response.status_code=403
        fetcher.load.side_effect=requests.HTTPError(response=response)
        with self.assertRaises(requests.HTTPError):probe_module.probe(fetcher)
        fetcher.load.assert_called_once()

    def test_browser_denied_does_not_read_or_retry_page(self):
        browser=BrowserPageFetcher();browser.page=Mock()
        browser.page.goto.return_value.status=403
        with self.assertRaises(requests.HTTPError):browser.load(bp.build_forum_url(BASE,619))
        browser.page.goto.assert_called_once()
        browser.page.content.assert_not_called()
        browser.page.wait_for_selector.assert_not_called()

    def test_browser_challenge_is_failure_not_empty_success(self):
        from playwright.sync_api import TimeoutError
        browser=BrowserPageFetcher();browser.page=Mock()
        browser.page.goto.return_value.status=200
        browser.page.wait_for_selector.side_effect=TimeoutError('No public content')
        with self.assertRaises(RuntimeError):browser.load(bp.build_forum_url(BASE,619))
        browser.page.content.assert_not_called()
        browser.page.goto.assert_called_once()

    def test_worker_passes_source_price_and_status_to_storage(self):
        rows=self.rows[:1]+[r for r in self.rows if r['title'].startswith('[SOLD]')][:1]
        with patch.object(bp,'CLASSIC_FORUMS',[(BASE,619,'Wheels','part')]),patch.object(bp,'MODERN_FORUMS',[]),patch.object(bp,'init_db'),patch.object(bp,'scrape_classic',return_value=rows),patch.object(bp,'insert_listing',return_value=True) as save,patch.object(bp,'image_backfill_candidates',return_value=[]):
            report=bp._run()
        self.assertEqual(report['fetched'],2)
        self.assertEqual(save.call_args_list[0].kwargs['price'],900)
        self.assertTrue(save.call_args_list[1].kwargs['title'].startswith('[SOLD]'))

    def test_unrecognized_page_reports_failure(self):
        with patch.object(bp,'CLASSIC_FORUMS',[(BASE,619,'Wheels','part')]),patch.object(bp,'MODERN_FORUMS',[]),patch.object(bp,'init_db'),patch.object(bp,'scrape_classic',return_value=[]),patch.object(bp,'image_backfill_candidates') as images:
            report=bp._run()
        self.assertEqual(report['errors'],1)
        images.assert_not_called()

    def test_saved_badge_price_overrides_outdated_title_price(self):
        with patch.object(db,'init_db'):
            import app
        row=app.enrich({'title':'FS Wheels $1,200','price':900,'source':'Bimmerpost','url':BASE+'/showthread/1/wheels'})
        self.assertEqual(row['price_value'],900)
        self.assertEqual(row['price'],'$900')

    def test_photo_denial_stops_backfill(self):
        response=requests.Response();response.status_code=403
        error=requests.HTTPError(response=response)
        with patch.object(bp,'CLASSIC_FORUMS',[(BASE,619,'Wheels','part')]),patch.object(bp,'MODERN_FORUMS',[]),patch.object(bp,'init_db'),patch.object(bp,'scrape_classic',return_value=self.rows[:1]),patch.object(bp,'insert_listing',return_value=False),patch.object(bp,'image_backfill_candidates',return_value=[{'id':1,'url':BASE+'/showthread/1/wheel'},{'id':2,'url':BASE+'/showthread/2/wheel'}]),patch.object(bp,'scrape_thread_image',side_effect=error) as image,patch.object(bp,'record_image_check') as save:
            report=bp._run()
        self.assertEqual(report['errors'],1)
        image.assert_called_once();save.assert_not_called()
