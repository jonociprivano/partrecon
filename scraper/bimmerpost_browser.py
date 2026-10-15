"""Ordinary Chromium transport; no login, saved cookies, proxies or challenge solving."""
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup


class BrowserPageFetcher:
    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.runtime = sync_playwright().start()
        try:
            self.browser = self.runtime.chromium.launch(headless=True)
            self.context = self.browser.new_context()
            self.page = self.context.new_page()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        try:
            if getattr(self, 'browser', None):
                self.browser.close()
        finally:
            self.runtime.stop()

    def load(self, url):
        host = (urlsplit(url).hostname or '').lower()
        allowed = ('bimmerpost.com', 'm3post.com', 'm5post.com', 'xbimmers.com')
        if urlsplit(url).scheme != 'https' or not any(host == d or host.endswith('.' + d) for d in allowed):
            raise ValueError('Unsupported Bimmerpost URL')
        response = self.page.goto(url, wait_until='domcontentloaded', timeout=30000)
        if response is None:
            raise RuntimeError('Bimmerpost returned no document response')
        if response.status >= 400:
            error_response = requests.Response()
            error_response.status_code = response.status
            raise requests.HTTPError(f'Bimmerpost denied browser request ({response.status})', response=error_response)
        # A 200 challenge/login page is also a failure. Never interact with it.
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        try:
            self.page.wait_for_selector(
                '[id^="thread-row-"], td[id^="td_threadtitle_"], article[data-postid], div[id^="post_message_"]',
                state='attached', timeout=10000)
        except PlaywrightTimeout:
            raise RuntimeError('No public listing/post content; page may require authorization') from None
        return BeautifulSoup(self.page.content(), 'html.parser')

    def image_loaded(self, url):
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        try:
            self.page.wait_for_function(
                "url => Array.from(document.images).some(i => i.src === url && i.complete && i.naturalWidth > 0)",
                arg=url, timeout=10000)
            return True
        except PlaywrightTimeout:
            return False
