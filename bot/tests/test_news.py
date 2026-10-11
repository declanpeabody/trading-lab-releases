import base64
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from app import handler_factory
from engine import Ledger
from news import News, SecClient, analyse, events_from

def fixture():
    today=datetime.now(timezone.utc).date().isoformat()
    return {'cik':320193,'name':'Fixture company', 'filings':{'recent':{
        'accessionNumber':['0000320193-26-000001'], 'form':['8-K'],
        'filingDate':[today], 'primaryDocument':['fixture.htm'],
        'items':['2.02,3.02'],'acceptanceDateTime':[today+'T09:00:00Z']}}}

class FakeSEC:
    def __init__(self, contact): self.contact=contact
    def get(self,url):
        if url.endswith('company_tickers.json'):
            return {'0':{'ticker':'AAPL','cik_str':320193},'1':{'ticker':'MSFT','cik_str':789019}}
        if '0000789019' in url:
            p=fixture();p['cik']=789019;return p
        return fixture()

class NewsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.news=News(self.tmp.name,FakeSEC)
        self.account={'positions':{'AAPL':{'qty':1}},'signals':{'AAPL':'buy'},'last_success':None}
    def tearDown(self):
        self.news.db.close();self.tmp.cleanup()
    def enable(self): self.news.configure({'action':'enable','contact':'tester@example.com'})

    def test_disabled_by_default_and_no_data_fabricated(self):
        self.news.poll(self.account)
        self.assertFalse(self.news.state()['enabled'])
        self.assertEqual(self.news.state()['events'],[])
        self.assertIsNone(self.news.state()['last_attempt'])

    def test_event_labels_do_not_predict_direction(self):
        a=analyse('8-K','1.03,2.02')
        self.assertEqual(a['flag'],'Risk review')
        self.assertEqual(a['direction'],'Unknown')
        self.assertIn('Results / financial condition',a['categories'])
        self.assertEqual(analyse('10-Q','1.03')['flag'],'Information review')
        self.assertEqual(analyse('8-K/A','3.02')['categories'],['Unregistered equity sale'])

    def test_unclassified_events_and_amendments(self):
        self.assertEqual(analyse('8-K','9.01')['categories'],['Current report / unspecified event'])
        self.assertEqual(analyse('10-K/A','')['categories'],['Annual report'])

    def test_source_identity_and_unsafe_document_rejected(self):
        p=fixture();now=datetime.now(timezone.utc)
        with self.assertRaises(ValueError):events_from(p,'AAPL',1,now)
        p['filings']['recent']['primaryDocument']=['../../bad.htm']
        self.assertEqual(events_from(p,'AAPL',320193,now),[])
        p['filings']['recent']['accessionNumber']=['bad-accession']
        with self.assertRaises(ValueError):events_from(p,'AAPL',320193,now)

    def test_lookback_and_future_filing_dates_excluded(self):
        for days in (-8,1):
            p=fixture();p['filings']['recent']['filingDate']=[(datetime.now(timezone.utc)+timedelta(days=days)).date().isoformat()]
            self.assertEqual(events_from(p,'AAPL',320193,datetime.now(timezone.utc)),[])

    def test_first_seen_dedup_and_restart_persistence(self):
        self.enable();self.news.poll(self.account)
        original=self.news.state()['events'][0]
        self.news.info['last_attempt']=None
        self.news.poll(self.account)
        self.assertEqual(self.news.state()['events'],[original])
        self.news.db.close();self.news=News(self.tmp.name,FakeSEC)
        self.assertEqual(self.news.state()['events'],[original])
        self.assertTrue(self.news.state()['enabled'])
        self.assertNotIn('tester@example.com',json.dumps(self.news.state(all_events=True)))

    def test_captured_baseline_context_and_account_unchanged(self):
        before=json.dumps(self.account,sort_keys=True)
        self.enable();self.news.poll(self.account)
        self.assertEqual(json.dumps(self.account,sort_keys=True),before)
        e=self.news.state()['events'][0]
        self.assertEqual(e['context']['price_signal_at_poll_start'],'buy')
        self.assertEqual(e['context']['role'],'held')
        self.assertTrue(e['source_url'].startswith('https://www.sec.gov/Archives/'))

    def test_failure_never_changes_ledger(self):
        ledger=Ledger(Path(self.tmp.name)/'paper.sqlite3');before=ledger.read()
        class Broken(FakeSEC):
            def get(self,url):raise ValueError('SEC HTTP 403')
        self.news.client_factory=Broken;self.enable();self.news.poll(ledger.read())
        self.assertIn('403',self.news.state()['error'])
        self.assertEqual(ledger.read(),before)
        ledger.db.close()

    def test_disable_preserves_observations(self):
        self.enable();self.news.poll(self.account);before=self.news.state()['events']
        self.news.configure({'action':'disable'});self.news.poll(self.account)
        self.assertEqual(self.news.state()['events'],before)

    def test_contact_validation_and_private_storage(self):
        import stat
        for value in ('','bad','x@example.com\r\nX-Test: injected'):
            with self.assertRaises(ValueError):self.news.configure({'action':'enable','contact':value})
        self.enable()
        self.assertEqual(stat.S_IMODE(self.news.config_path.stat().st_mode),0o600)

    def test_refresh_cooldown(self):
        self.enable();self.news.poll(self.account)
        with self.assertRaisesRegex(ValueError,'five minutes'):
            self.news.configure({'action':'refresh'})

    def test_unmapped_symbols_are_visible_not_guessed(self):
        self.enable();self.news.poll({'positions':{'UNKNOWN':{}}})
        self.assertEqual(self.news.state()['events'],[])
        self.assertIn('skipped',self.news.state()['coverage'][0]['status'])

    def test_network_destination_allowlist(self):
        for url in ('http://www.sec.gov/files/company_tickers.json','https://evil.test/',
                    'https://data.sec.gov/submissions/CIK1.json'):
            with self.assertRaisesRegex(ValueError,'permitted'):SecClient('test@example.com').get(url)

class NewsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.news=News(self.tmp.name,FakeSEC)
        self.ledger=Ledger(Path(self.tmp.name)/'paper.sqlite3')
        self.auth='Basic '+base64.b64encode(b'declan:test-password-long-enough').decode()
        handler=handler_factory(self.ledger,'declan','test-password-long-enough',threading.current_thread(),news=self.news)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.news.db.close();self.ledger.db.close();self.tmp.cleanup()
    def request(self,path,data=None,csrf=None,auth=True):
        headers={'Authorization':self.auth} if auth else {}
        if csrf:headers['X-CSRF-Token']=csrf
        return urlopen(Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers))
    def test_news_and_export_require_auth(self):
        for path in ('/news','/api/news','/api/news/export'):
            with self.assertRaises(HTTPError) as e:self.request(path,auth=False)
            self.assertEqual(e.exception.code,401)
    def test_news_controls_require_csrf_and_do_not_change_trading(self):
        before=self.ledger.read()
        with self.assertRaises(HTTPError) as e:self.request('/api/news',{'action':'enable','contact':'test@example.com'})
        self.assertEqual(e.exception.code,403)
        with self.request('/api/state') as r:csrf=json.load(r)['csrf']
        with self.request('/api/news',{'action':'enable','contact':'test@example.com'},csrf):pass
        self.assertTrue(self.news.state()['enabled']);self.assertEqual(self.ledger.read(),before)
        with self.request('/api/news/export') as r:self.assertNotIn('test@example.com',r.read().decode())
    def test_news_page_csp_and_safe_text_rendering(self):
        with self.request('/news') as r:
            page=r.read().decode();self.assertIn('nonce-',r.headers['Content-Security-Policy'])
        self.assertIn('OBSERVATION ONLY',page)
        self.assertNotIn('innerHTML',page)
