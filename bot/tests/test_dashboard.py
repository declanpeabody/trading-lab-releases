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

    def test_navigation_and_external_feed_keep_account_isolated(self):
        for path in ('/','/research','/news','/updates'):
            with self.get(path) as response:
                body=response.read().decode();policy=response.headers['Content-Security-Policy']
                self.assertNotIn('__TOP_NAV__',body)
                for href in ('/#positions','/#scanner','/#markets','/#rules','/research','/news','/updates'):
                    self.assertIn('href="'+href+'"',body)
                self.assertIn("connect-src 'self'",policy)
                self.assertIn('frame-src https://www.tradingview-widget.com;',policy)
                self.assertNotIn("script-src https:",policy)
                self.assertNotIn("'unsafe-inline'",policy)
                if path=='/':
                    self.assertIn("frame.referrerPolicy='no-referrer'",body)
                    self.assertIn('ETF trackers',body)
                    self.assertIn('not index levels',body)
                    self.assertNotIn('51,850.31',body)
                    self.assertNotIn('src="https://s3.tradingview.com',body)

if __name__ == '__main__': unittest.main()
