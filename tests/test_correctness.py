import os
import sys
import json
import sqlite3
import tempfile
import unittest
import shutil
import subprocess
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch,Mock
from bs4 import BeautifulSoup

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DATABASE_URL']=''
from database import db
from listing_utils import parse_price,native_identity,is_recent,is_marketplace_post,is_available,dedupe
with patch.object(db,'init_db'):
    import app
from scraper import runner,fetch,nam3forum,bimmerpost,parser


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.config=patch.object(db,'DB_PATH',Path(self.temp.name)/'test.db');self.config.start()
        self.pg=patch.object(db,'_DATABASE_URL','');self.pg.start()
        db.init_db()
    def tearDown(self):
        self.pg.stop();self.config.stop();self.temp.cleanup()
    def add(self,title='[FS] Wheels $1200',url='https://nam3forum.com/forums/e46/123-wheels',image=None):
        return db.insert_listing('NAM3Forum',title,url,image,'','2026-10-01T12:00:00+00:00')
    def test_prices_and_refresh_preserve_photo_and_first_discovery(self):
        self.assertTrue(self.add(image='https://example.com/wheel.jpg'))
        first=db.get_listings_by_type('part')[0]
        self.assertEqual(first['price'],1200)
        self.assertFalse(self.add(title='[FS] Wheels $950',url='https://nam3forum.com/forums/e46/123-reduced-price'))
        row=db.get_listings_by_type('part')[0]
        self.assertEqual(row['price'],950)
        self.assertEqual(row['created_at'],first['created_at'])
        self.assertEqual(row['image_url'],'https://example.com/wheel.jpg')
        self.assertIsNotNone(row['last_checked_at'])
        self.assertEqual(row['posted_at'],first['posted_at'])
        self.assertEqual(row['normalized_title'],'[FS] Wheels $950')
    def test_refresh_sold_does_not_leave_available_copy(self):
        self.add()
        self.add(title='[SOLD] Wheels $1200')
        self.assertEqual(db.get_listings_by_type('part'),[])
        with db.get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM listings').fetchone()[0],1)
    def test_existing_duplicates_hidden_and_records_preserved(self):
        self.add()
        with db.get_connection() as conn:
            conn.execute("INSERT INTO listings(source,title,url,created_at,listing_type) VALUES(?,?,?,?,?)",('NAM3Forum','Wheels $1200','https://nam3forum.com/forums/e46/123-renamed','2026-10-01','part'))
        self.assertEqual(len(db.get_listings_by_type('part')),1)
        with db.get_connection() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM listings').fetchone()[0],2)
    def test_backfill_is_bounded_and_cools_down(self):
        for i in range(12):self.add(url=f'https://nam3forum.com/forums/e46/{i+100}-wheel')
        candidates=db.image_backfill_candidates('NAM3Forum')
        self.assertEqual(len(candidates),8)
        first=candidates[0]
        db.record_image_check(first['id'],None)
        self.assertNotIn(first['id'],[r['id'] for r in db.image_backfill_candidates('NAM3Forum',30)])
        db.record_image_check(first['id'],'https://example.com/photo.jpg')
        self.assertEqual(db.get_listing_by_id(first['id'])['image_url'],'https://example.com/photo.jpg')
    def test_schema_can_run_twice(self):
        self.add();db.init_db()
        self.assertEqual(len(db.get_listings_by_type('part')),1)
    def test_approved_detail_only_and_escaping(self):
        ident=db.insert_submitted_listing('<script>alert(1)</script>',1200,'E46','Wheels','Used','California','<b>Description</b>','seller@example.com','',[])
        client=app.app.test_client()
        self.assertEqual(client.get(f'/listing/submitted/{ident}').status_code,404)
        db.update_submitted_listing_status(ident,'approved')
        response=client.get(f'/listing/submitted/{ident}')
        self.assertEqual(response.status_code,200)
        html=response.get_data(as_text=True)
        self.assertIn('$1,200',html)
        self.assertIn('mailto:seller@example.com',html)
        self.assertNotIn('<script>alert(1)</script>',html)
        db.update_submitted_listing_status(ident,'rejected')
        self.assertEqual(client.get(f'/listing/submitted/{ident}').status_code,404)


class ParsingTests(unittest.TestCase):
    def test_money_cases(self):
        for title,price in [('$1200',1200),('$12000',12000),('$1,200',1200),('$450.50',450.5),('$1.2k',1200),('$78k',78000),('$0',0),('No price',None)]:
            with self.subTest(title=title):self.assertEqual(parse_price(title),price)
    def test_native_identities(self):
        for a,b in [('https://nam3forum.com/forums/e46/123-old','https://nam3forum.com/forums/e46/123-new?p=5'),('https://f80.bimmerpost.com/forums/showthread.php?t=55','https://bimmerpost.com/forums/showthread/55/new'),('https://reddit.com/r/E46/comments/abc/a','https://www.reddit.com/r/E46/comments/abc/b')]:
            self.assertEqual(native_identity('source',a),native_identity('source',b))
        self.assertNotEqual(native_identity('source','https://evilbimmerpost.com/?t=55'),native_identity('source','https://bimmerpost.com/?t=55'))
    def test_freshness_not_permanent_or_future(self):
        now=datetime.now(timezone.utc)
        self.assertTrue(is_recent(now.isoformat()))
        self.assertFalse(is_recent((now-timedelta(days=2)).isoformat()))
        self.assertFalse(is_recent((now+timedelta(days=2)).isoformat()))
        self.assertFalse(is_recent(None))
    def test_selling_tag_is_not_sold(self):
        self.assertTrue(is_available('[S] Wheels $1200'))
        self.assertTrue(is_available('FS wheels — price drop'))
        self.assertTrue(is_available('Unsold wheels'))
        self.assertFalse(is_available('[FS] [SOLD] Wheels'))
    def test_discussions_not_marketplace(self):
        self.assertFalse(is_marketplace_post('Is this F80 worth $38000?','F80'))
        self.assertTrue(is_marketplace_post('[FS] F80 wheels $1200','F80'))
    def test_website_numeric_price_and_images(self):
        row=app.enrich({'title':'[S] E46 wheels $1200','url':'https://example.com','source':'E46','image_url':'https://preview.redd.it/photo.jpg','is_new':1,'created_at':'2026-01-01'})
        self.assertEqual(row['price_value'],1200)
        self.assertEqual(row['price'],'$1,200')
        self.assertFalse(row['is_new'])
        self.assertIsNone(row['sold_stamp'])
        self.assertTrue(row['has_image'])
    def test_submission_rejects_bad_price_and_url(self):
        client=app.app.test_client()
        for data in ({'price':'NaN'},{'price':'-1'},{'external_url':'javascript:alert(1)'},{'external_url':'http://['}):
            response=client.post('/sell',data={'title':'Wheels','contact_email':'seller@example.com',**data})
            self.assertEqual(response.status_code,400)
    def test_nam3_vbulletin_attachment(self):
        soup=BeautifulSoup('<div class="js-post__content-text">First post<img src="/forums/filedata/fetch?id=123&type=thumb"></div>','html.parser')
        with patch.object(nam3forum,'fetch_page',return_value=soup),patch.object(nam3forum.time,'sleep'):
            self.assertEqual(nam3forum.scrape_thread_image('https://nam3forum.com/forums/e46/123-wheel'),'https://nam3forum.com/forums/filedata/fetch?id=123&type=thumb')
    def test_parser_error_remains_retryable(self):
        with patch.object(parser,'_get_client',side_effect=RuntimeError('API unavailable')):
            self.assertFalse(parser.parse_listing('Wheels')['_ok'])
        with patch.object(db,'get_unparsed_listings',return_value=[{'id':1,'title':'Wheels'}]),patch.object(parser,'parse_listing',return_value={'_ok':False}),patch.object(db,'mark_listing_parsed') as mark:
            with self.assertRaises(RuntimeError):runner.run_parser()
            mark.assert_not_called()


class ReportingTests(unittest.TestCase):
    def test_zero_results_not_success_and_duplicates_are_success(self):
        for result,status in [({'fetched':0,'inserted':0,'refreshed':0,'errors':1},'needs_attention'),({'fetched':80,'inserted':0,'refreshed':80,'errors':0},'ok')]:
            with patch.object(fetch,'main',return_value=result):self.assertEqual(runner.run_scraper('reddit')['status'],status)
    def test_cli_status_failure(self):
        with patch.object(runner,'run_scraper',return_value={'status':'needs_attention'}),patch.object(runner,'run_parser'):
            self.assertEqual(runner.main('reddit'),1)
    def test_reddit_denied_stops_at_first_request(self):
        import requests
        error=requests.HTTPError(response=Mock(status_code=403))
        with patch.object(fetch,'init_db'),patch.object(fetch,'fetch_new_posts',side_effect=error) as get:
            result=fetch.main()
        self.assertEqual(result['errors'],1)
        get.assert_called_once()
    def test_bimmerpost_denied_stops_without_image_backfill(self):
        import requests
        error=requests.HTTPError(response=Mock(status_code=403))
        with patch.object(bimmerpost,'init_db'),patch.object(bimmerpost,'scrape_classic',side_effect=error) as get,patch.object(bimmerpost,'image_backfill_candidates') as images:
            result=bimmerpost.main()
        self.assertEqual(result['errors'],1);get.assert_called_once();images.assert_not_called()
    def test_nam3_duplicate_run_still_refreshes_and_backfills(self):
        with patch.object(nam3forum,'init_db'),patch.object(nam3forum,'PAGES',[('https://nam3forum.com/forums/e46','part')]),patch.object(nam3forum,'scrape_page',return_value=[{'title':'[FS] Wheels','url':'https://nam3forum.com/forums/e46/1-wheel','posted_at':None}]),patch.object(nam3forum,'insert_listing',return_value=False) as insert,patch.object(nam3forum,'image_backfill_candidates',return_value=[{'id':1,'url':'https://nam3forum.com/forums/e46/1-wheel'}]),patch.object(nam3forum,'scrape_thread_image',return_value='https://nam3forum.com/photo.jpg'),patch.object(nam3forum,'record_image_check') as save:
            result=nam3forum.main()
        self.assertEqual(result['refreshed'],1);insert.assert_called_once();save.assert_called_once()

class WebFilterTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Requires Node')
    def test_rendered_numeric_values_and_actual_price_filter(self):
        records=[{'title':f'[FS] Wheels ${p}','price':p,'source':'NAM3Forum','url':f'https://nam3forum.com/forums/e46/{i+1}-wheel','listing_type':'part'} for i,p in enumerate([950,1200,2500])]
        with patch.object(app,'get_listings_by_type',side_effect=lambda t:records if t=='part' else []),patch.object(app,'get_approved_submitted_listings',return_value=[]):
            html=app.app.test_client().get('/').get_data(as_text=True)
        soup=BeautifulSoup(html,'html.parser')
        cards=[{'dataset':{'type':n['data-type'],'price':n['data-price'],'title':n['data-title']},'style':{}} for n in soup.select('.card')]
        self.assertEqual([c['dataset']['price'] for c in cards],['950.0','1200.0','2500.0'])
        script=next(s.get_text() for s in soup.find_all('script') if 'var PAGE_SIZE' in s.get_text())
        script=script[:script.index('function submitEmail')] if 'function submitEmail' in script else script
        setup='''const cards=CARDS;const elements={};
for(const id of ['search','count','prev-btn','next-btn','page-info','sort-select','min-price','max-price','part-type-row'])elements[id]={value:'',style:{},events:{},addEventListener(t,fn){this.events[t]=fn;}};
const document={querySelectorAll(s){return s==='.card'?cards:[];},getElementById(id){return elements[id];}};const window={scrollTo(){}};
function visible(){return cards.filter(c=>c.style.display!=='none').map(c=>Number(c.dataset.price));}
'''.replace('CARDS',json.dumps(cards))
        checks='''elements['min-price'].value='1000';elements['min-price'].events.input();if(JSON.stringify(visible())!=='[1200,2500]')throw Error('minimum price');
elements['max-price'].value='2000';elements['max-price'].events.input();if(JSON.stringify(visible())!=='[1200]')throw Error('maximum price');
elements['min-price'].value='';elements['max-price'].value='';elements['max-price'].events.input();elements['sort-select'].value='price-desc';elements['sort-select'].events.change();if(cards[2].style.order!==0)throw Error('numeric sort');'''
        subprocess.run(['node','-e',setup+script+checks],check=True,capture_output=True)

    def test_postgres_upsert_lock_and_image_preservation(self):
        conn=Mock()
        cur=conn.cursor.return_value
        cur.description=[('id',),('source',),('url',),('title',)]
        cur.fetchall.return_value=[(1,'NAM3Forum','https://nam3forum.com/forums/e46/123-old','Old wheels')]
        with patch.object(db,'_DATABASE_URL','test-configured'),patch.object(db,'get_connection',return_value=conn):
            self.assertFalse(db.insert_listing('NAM3Forum','New wheels','https://nam3forum.com/forums/e46/123-new',None,'',None))
        statements=[c.args[0] for c in cur.execute.call_args_list]
        self.assertTrue(any('pg_advisory_xact_lock' in q for q in statements))
        update=next(q for q in statements if 'UPDATE listings' in q)
        self.assertIn("COALESCE(NULLIF(%s,''), image_url)",update)
        self.assertNotIn('created_at=',update)
        conn.commit.assert_called_once();conn.close.assert_called_once()
