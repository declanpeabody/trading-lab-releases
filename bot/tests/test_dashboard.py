import base64
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from app import handler_factory
from engine import Ledger
from research import Research, Experiments
from updater import Updates
from ranked_scanner import Journal
from unittest.mock import patch


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(self.temp.name+'/test.db')
        self.research = Research(self.temp.name)
        self.updates = Updates(self.temp.name)
        self.experiments = Experiments(self.temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1',0), handler_factory(
            self.ledger,'tester','a-test-password-with-entropy',threading.current_thread(),self.research,updates=self.updates,journal=Journal(self.temp.name),experiments=self.experiments))
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:'+str(self.server.server_port)
        self.auth = 'Basic '+base64.b64encode(b'tester:a-test-password-with-entropy').decode()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.ledger.db.close()
        self.temp.cleanup()

    def get(self,path,auth=True):
        return urlopen(Request(self.base+path,headers={'Authorization':self.auth} if auth else {}),timeout=2)

    def test_private_state_and_public_health(self):
        with self.assertRaises(HTTPError) as err: self.get('/api/state',False)
        self.assertEqual(err.exception.code,401)
        with self.get('/healthz',False) as response: self.assertEqual(response.status,200)
        with self.get('/api/state') as response:
            state=json.load(response)
        self.assertEqual(state['mode'],'paper')
        self.assertTrue(state['paused'])
        self.assertNotIn('API_KEY',json.dumps(state))

    def test_disk_failure_returns_visible_status_and_worker_health(self):
        with patch.object(self.ledger,'read',side_effect=OSError('disk failure')):
            with self.assertRaises(HTTPError) as error:self.get('/api/state')
            self.assertEqual(error.exception.code,503)
            result=json.loads(error.exception.read())
            self.assertEqual(result['error'],'Account storage unavailable')
            self.assertIn('worker',result)

    def test_invalid_saved_key_visible_without_network_or_secret_exposure(self):
        with patch.dict('os.environ',{'TWELVE_DATA_API_KEY':'PRIVATE\nSECRET'}),patch('market.urlopen') as request:
            with self.get('/api/state') as response:state=json.load(response)
            self.assertIn('saved API key contains',state['error'])
            self.assertNotIn('PRIVATE',json.dumps(state));request.assert_not_called()

    def test_experiment_controls_require_auth_and_csrf(self):
        with self.assertRaises(HTTPError):self.get('/api/experiments',False)
        with self.get('/api/state') as r:token=json.load(r)['csrf']
        headers={'Authorization':self.auth,'Content-Type':'application/json'}
        req=lambda:Request(self.base+'/api/experiments',data=b'{"action":"automatic","enabled":false}',headers=headers)
        with self.assertRaises(HTTPError) as error:urlopen(req(),timeout=2)
        self.assertEqual(error.exception.code,403);self.assertTrue(self.experiments.state()['automatic'])
        headers['X-CSRF-Token']=token
        with urlopen(req(),timeout=2) as r:self.assertEqual(r.status,200)
        self.assertFalse(self.experiments.state()['automatic'])
        with self.get('/api/experiments/export') as r:
            self.assertIn('attachment',r.headers['Content-Disposition'])
            self.assertEqual(json.load(r)['active'],200)
        self.assertTrue(self.ledger.read()['paused'])

    def test_connection_test_is_authenticated_csrf_protected_and_separate(self):
        before=self.ledger.read()
        with patch('app.ConnectionTest.start') as start:
            with self.get('/api/state') as r:state=json.load(r)
            self.assertIn('request_budget',state);self.assertIn('no_trade',state)
            start.assert_not_called()
            headers={'Content-Type':'application/json'}
            req=lambda:Request(self.base+'/api/connection-test',data=b'{}',headers=headers)
            with self.assertRaises(HTTPError) as error:urlopen(req(),timeout=2)
            self.assertEqual(error.exception.code,401)
            headers['Authorization']=self.auth
            with self.assertRaises(HTTPError) as error:urlopen(req(),timeout=2)
            self.assertEqual(error.exception.code,403);start.assert_not_called()
            headers['X-CSRF-Token']=state['csrf']
            with urlopen(req(),timeout=2) as r:self.assertEqual(r.status,200)
            start.assert_called_once()
        self.assertEqual(self.ledger.read(),before)

    def test_monitoring_toggle_is_local_authenticated_and_csrf_protected(self):
        with patch.dict('os.environ',{'DATA_DIR':self.temp.name}):
            with self.get('/api/state') as r:state=json.load(r)
            self.assertFalse(state['monitoring_enabled'])
            headers={'Authorization':self.auth,'Content-Type':'application/json'}
            request=lambda:Request(self.base+'/api/monitoring',data=b'{"enabled":true}',headers=headers)
            with self.assertRaises(HTTPError) as error:urlopen(request(),timeout=2)
            self.assertEqual(error.exception.code,403)
            headers['X-CSRF-Token']=state['csrf']
            with urlopen(request(),timeout=2) as r:self.assertEqual(r.status,200)
            with self.get('/api/state') as r:self.assertTrue(json.load(r)['monitoring_enabled'])
            self.assertTrue(self.ledger.read()['paused'])

    def test_control_requires_csrf_and_persists(self):
        with self.get('/api/state') as response: token=json.load(response)['csrf']
        headers={'Authorization':self.auth,'Content-Type':'application/json'}
        request=Request(self.base+'/api/control',data=b'{"action":"start"}',headers=headers)
        with self.assertRaises(HTTPError) as err: urlopen(request,timeout=2)
        self.assertEqual(err.exception.code,403)
        headers['X-CSRF-Token']=token
        with urlopen(Request(self.base+'/api/control',data=b'{"action":"start"}',headers=headers),timeout=2) as response:
            self.assertEqual(response.status,200)
        self.assertFalse(self.ledger.read()['paused'])

    def test_dashboard_has_csp_and_clear_paper_status(self):
        with self.get('/') as response:
            body=response.read().decode()
            self.assertIn('PAPER ONLY',body)
            self.assertNotIn('__NONCE__',body)
            self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])


    def test_research_is_authenticated_and_nonce_protected(self):
        with self.assertRaises(HTTPError) as err: self.get('/api/research',False)
        self.assertEqual(err.exception.code,401)
        with self.get('/research') as response:
            self.assertNotIn('__NONCE__',response.read().decode())
            self.assertIn("script-src 'nonce-",response.headers['Content-Security-Policy'])
        with self.get('/api/state') as response: token=json.load(response)['csrf']
        headers={'Authorization':self.auth,'Content-Type':'application/json'}
        with patch.object(self.research,'start') as start:
            with self.assertRaises(HTTPError) as err:
                urlopen(Request(self.base+'/api/research',data=b'{}',headers=headers),timeout=2)
            self.assertEqual(err.exception.code,403);start.assert_not_called()
            headers['X-CSRF-Token']=token
            with urlopen(Request(self.base+'/api/research',data=b'{}',headers=headers),timeout=2) as response:
                self.assertEqual(response.status,200)
            start.assert_called_once_with({})
        self.assertTrue(self.ledger.read()['paused'])

    def test_report_download_requires_login(self):
        self.research.latest={'fixture':'report'}
        with self.assertRaises(HTTPError) as err:self.get('/api/export/json',False)
        self.assertEqual(err.exception.code,401)
        with self.get('/api/export/json') as response:
            self.assertIn('attachment',response.headers['Content-Disposition'])
            self.assertEqual(json.load(response),{'fixture':'report'})

    def test_update_install_requires_auth_csrf_and_exact_reviewed_version(self):
        with self.assertRaises(HTTPError) as err:self.get('/api/updates',False)
        self.assertEqual(err.exception.code,401)
        with self.get('/updates') as response:
            self.assertNotIn('__NONCE__',response.read().decode())
        with self.get('/api/state') as response:token=json.load(response)['csrf']
        headers={'Authorization':self.auth,'Content-Type':'application/json'}
        with patch.object(self.updates,'install') as install:
            with self.assertRaises(HTTPError) as err:
                urlopen(Request(self.base+'/api/updates',data=b'{"action":"install","version":"2099.1.1.1"}',headers=headers),timeout=2)
            self.assertEqual(err.exception.code,403);install.assert_not_called()
            headers['X-CSRF-Token']=token
            with urlopen(Request(self.base+'/api/updates',data=b'{"action":"install","version":"2099.1.1.1"}',headers=headers),timeout=2) as response:self.assertEqual(response.status,200)
            install.assert_called_once_with('2099.1.1.1')

    def test_journal_and_export_require_login(self):
        for path in ('/api/journal','/api/journal/export'):
            with self.assertRaises(HTTPError) as err:self.get(path,False)
            self.assertEqual(err.exception.code,401)
            with self.get(path) as response:
                data=json.load(response)
                self.assertEqual(data['records'],[])
                if path.endswith('export'):self.assertIn('attachment',response.headers['Content-Disposition'])
        with self.get('/research') as response:
            body=response.read().decode()
            self.assertIn('Walk-forward evaluation',body)
            self.assertIn('Decision journal',body)
            self.assertNotIn('__NONCE__',body)

    def test_navigation_and_market_links_without_external_feed(self):
        for path in ('/','/research','/news','/updates'):
            with self.get(path) as response:
                body=response.read().decode();policy=response.headers['Content-Security-Policy']
                self.assertNotIn('__TOP_NAV__',body)
                for href in ('/#positions','/#scanner','/#markets','/#rules','/research','/news','/updates'):
                    self.assertIn('href="'+href+'"',body)
                self.assertIn("connect-src 'self'",policy)
                self.assertIn("frame-src 'none';",policy)
                self.assertNotIn("script-src https:",policy)
                self.assertNotIn("'unsafe-inline'",policy)
                if path=='/':
                    self.assertNotIn('market-host',body)
                    self.assertNotIn('mountFeed',body)
                    self.assertNotIn('tradingview',body.lower())
                    self.assertIn('https://www.cnbc.com/quotes/.DJI',body)
                    self.assertNotIn('51,850.31',body)
                    self.assertNotIn('src="https://s3.tradingview.com',body)


    def test_observatory_requires_auth_csrf_and_preserves_account(self):
        with patch.dict('os.environ',{'DATA_DIR':self.temp.name}):
            with self.assertRaises(HTTPError) as err:self.get('/api/observatory',False)
            self.assertEqual(err.exception.code,401)
            with self.get('/api/state') as r:token=json.load(r)['csrf']
            before=self.ledger.read()
            csv='source,strategy,kind,symbol,action,published_at,reported_price_usd\nOwner,Trend,paper,AAPL,BUY,2026-10-01T14:00:00Z,100'
            headers={'Authorization':self.auth,'Content-Type':'application/json'}
            request=lambda:Request(self.base+'/api/observatory',data=json.dumps({'csv':csv}).encode(),headers=headers)
            with self.assertRaises(HTTPError) as err:urlopen(request(),timeout=2)
            self.assertEqual(err.exception.code,403)
            headers['X-CSRF-Token']=token
            with urlopen(request(),timeout=2) as r:self.assertEqual(r.status,200)
            with self.get('/api/observatory') as r:self.assertEqual(json.load(r)['total'],1)
            self.assertEqual(self.ledger.read(),before)

    def test_daily_learning_is_private_and_read_only(self):
        with patch.dict('os.environ', {'DATA_DIR': self.temp.name}), patch('app.LEARNING', None):
            before=self.ledger.read()
            with self.assertRaises(HTTPError) as err: self.get('/api/learning', False)
            self.assertEqual(err.exception.code,401)
            with self.get('/api/learning') as response: state=json.load(response)
            self.assertIsNone(state['last_review'])
            self.assertFalse(state['automatic_deployment'])
            self.assertEqual(self.ledger.read(),before)


    def test_stock_desk_authentication_csrf_and_event_revision(self):
        with patch.dict('os.environ',{'DATA_DIR':self.temp.name}):
            with self.assertRaises(HTTPError) as error:self.get('/api/stock-desk',False)
            self.assertEqual(error.exception.code,401)
            with self.get('/api/stock-desk') as r:self.assertEqual(json.load(r)['cards'],[])
            from datetime import datetime,timedelta,timezone
            body=json.dumps(dict(symbol='TEST',at=(datetime.now(timezone.utc)+timedelta(days=5)).isoformat(),source='https://example.com/ir',verified=True)).encode()
            headers={'Authorization':self.auth,'Content-Type':'application/json'}
            with self.assertRaises(HTTPError) as error:urlopen(Request(self.base+'/api/stock-desk',data=body,headers=headers),timeout=2)
            self.assertEqual(error.exception.code,403)
            with self.get('/api/state') as r:headers['X-CSRF-Token']=json.load(r)['csrf']
            before=self.ledger.read()
            with urlopen(Request(self.base+'/api/stock-desk',data=body,headers=headers),timeout=2) as r:self.assertEqual(r.status,200)
            with self.get('/api/stock-desk') as r:self.assertEqual(json.load(r)['events'][0]['verification'],'User verified against source')
            self.assertEqual(self.ledger.read(),before)

if __name__ == '__main__': unittest.main()
