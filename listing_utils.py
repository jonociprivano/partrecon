"""Shared money, identity, and explicit status rules."""
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urlunparse

PRICE_RE = re.compile(r'\$\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)\s*([kK])?(?![\d.,])')

def parse_price(text):
    match = PRICE_RE.search(text or '')
    if not match:
        return None
    return round(float(match[1].replace(',','')) * (1000 if match[2] else 1),2)

def format_price(value):
    if value is None:
        return ''
    return '$' + (f'{value:,.0f}' if float(value).is_integer() else f'{value:,.2f}')

def native_identity(source,url):
    parsed=urlparse(url or '')
    host=(parsed.hostname or '').lower().removeprefix('www.')
    if host=='nam3forum.com':
        match=re.search(r'/([0-9]+)-[^/]+',parsed.path)
        if match: return 'nam3:'+match[1]
    if any(host==d or host.endswith('.'+d) for d in ('bimmerpost.com','m3post.com','m5post.com','xbimmers.com')):
        ident=parse_qs(parsed.query).get('t',[''])[0]
        match=re.search(r'/showthread/(\d+)(?:/|$)',parsed.path)
        if ident.isdigit() or match:
            return 'bimmerpost:'+(ident if ident.isdigit() else match[1])
    match=re.search(r'/r/[^/]+/comments/([a-z0-9]+)',parsed.path,re.I)
    if host in ('reddit.com','old.reddit.com') and match:
        return 'reddit:'+match[1].lower()
    return (source or '')+':'+urlunparse(parsed._replace(fragment=''))

def is_available(title):
    text=re.sub(r'^\s*(?:\[?(?:FS|WTS|WTB|FT)\]?[:\s-]*)','',title or '',flags=re.I)
    return not bool(re.search(r'^\s*\[?(?:sold|withdrawn|expired|closed|no longer available)\b|\[(?:sold|withdrawn|expired|closed)\]|\s[-–—:]\s*(?:sold|withdrawn|expired|closed)\s*$',text,re.I))

def is_marketplace_post(title,source):
    text=(title or '').lower()
    if re.search(r'\b(?:scam alert|posting rules|forum rules|seller feedback)\b',text): return False
    if source not in ('NAM3Forum','Bimmerpost','PartRecon'):
        if re.search(r'\b(?:is this|is it|does this|what is|how much is)\b.*\b(?:worth|price|cost)\b|\b(?:weekly|megathread)\b',text): return False
        return bool(re.search(r'^\s*(?:\[(?:FS|S|WTB|B|T)\]|(?:FS|WTS|WTB|ISO)\b)|\b(?:for sale|selling|want to buy|parting out)\b',text,re.I))
    return True

def is_recent(value):
    if not value: return False
    try:
        date=datetime.fromisoformat(value.replace('Z','+00:00'))
        if date.tzinfo is None: date=date.replace(tzinfo=timezone.utc)
        return timedelta(0)<=datetime.now(timezone.utc)-date<timedelta(hours=24)
    except (ValueError,TypeError): return False

def dedupe(rows):
    grouped={}
    for row in sorted(rows,key=lambda x:(x.get('last_checked_at') or '', x.get('id') or 0),reverse=True):
        key=native_identity(row.get('source'),row.get('url'))
        if key not in grouped: grouped[key]=dict(row)
        elif not grouped[key].get('image_url') and row.get('image_url'): grouped[key]['image_url']=row['image_url']
    def date_key(row):
        value=row.get('posted_at') or row.get('created_at') or ''
        try:
            date=datetime.fromisoformat(value.replace('Z','+00:00'))
            if date.tzinfo is None: date=date.replace(tzinfo=timezone.utc)
            return date.timestamp()
        except (ValueError,TypeError): return float('-inf')
    return sorted(grouped.values(),key=lambda r:(date_key(r),r.get('id') or 0),reverse=True)
