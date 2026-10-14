import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from engine import Ledger
from market import MarketUnavailable
from ranked_scanner import RankedScanner
from research import ShadowTrials
from scanner import catalogue
from news import document_evidence, SecClient
from test_scanner import NOW, listing
from test_universe import LargeMarket

class QualityBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_catalogue_rejects_mislabelled_instruments_preserves_class_shares(self):
        rows=[listing('ABC.WT'),listing('ABC.PR.A'),listing('ABC.UN'),listing('EWAVR',name='Company subscription rights'),listing('BRK.B',name='Berkshire Hathaway Class B'),listing('GOOGL',name='Alphabet')]
        self.assertEqual(set(catalogue(rows)),{'BRK.B','GOOGL'})
    def test_optional_failure_does_not_undo_held_transition(self):
        ledger=Ledger(self.root/'paper.sqlite3');ledger.control('start')
        self.addCleanup(ledger.db.close)
        market=LargeMarket();scanner=RankedScanner(self.root,market)
        def scan():
            with patch('ranked_scanner.datetime') as clock:
                clock.now.return_value=NOW;scanner.cycle(ledger)
        scan();scan();held=set(ledger.read()['positions']);scanner.trials.control('enabled',True)
        original=market.get;market.calls=[]
        def broken(endpoint,**kw):
            if endpoint=='quote' and not kw.get('priority'):
                self.assertEqual(set(scanner.status['checked']),held)
                raise MarketUnavailable('Optional provider failure')
            return original(endpoint,**kw)
        with patch.object(market,'get',side_effect=broken):scan()
        self.assertIsNone(ledger.read()['error']);self.assertTrue(scanner.status['holdings_checked_at'])
        self.assertIn('Optional provider',scanner.status['optional_error'])
        self.assertEqual(set(ledger.read()['positions']),held)
        self.assertEqual(scanner.trials.state()['checks'],0)
        self.assertEqual(scanner.trials.state()['misses'],1)
    def test_full_account_avoids_optional_quotes_when_experiments_off(self):
        ledger=Ledger(self.root/'paper.sqlite3');ledger.control('start');self.addCleanup(ledger.db.close)
        market=LargeMarket();scanner=RankedScanner(self.root,market)
        with patch('ranked_scanner.datetime') as clock:
            clock.now.return_value=NOW;scanner.cycle(ledger);scanner.cycle(ledger);market.calls=[];scanner.cycle(ledger)
        quotes=[x for x in market.calls if x[0]=='quote']
        self.assertEqual(len(quotes),6);self.assertTrue(all(x[2] for x in quotes))
    def test_trials_separate_account_and_reserve_missing_news_blocks_only_guard(self):
        ledger=Ledger(self.root/'paper.sqlite3');self.addCleanup(ledger.db.close);before=ledger.read()
        trials=ShadowTrials(self.root);trials.control('enabled',True)
        bars={s:LargeMarket().get('time_series',symbol=s)['values'][-50:] for s in ('AAPL','MSFT')}
        trials.observe({'AAPL':200,'MSFT':200},bars,.75,'2026-10-09',NOW.isoformat(),['AAPL','MSFT'])
        state=trials.state();self.assertEqual(state['checks'],1)
        self.assertEqual(len(state['books']['baseline']['positions']),2)
        self.assertEqual(len(state['books']['allocation']['positions']),1)
        self.assertGreaterEqual(state['books']['allocation']['cash'],200)
        self.assertEqual(state['books']['filing_guard']['positions'],{})
        self.assertEqual(ledger.read(),before)
        books=state['books']
        trials.observe({'AAPL':200},bars,.75,'2026-10-09','2026-10-09T15:00:00+00:00',[])
        self.assertEqual(trials.state()['books'],books);self.assertEqual(trials.state()['misses'],1)
    def test_news_gate_rejects_future_coverage_and_ignores_future_events(self):
        trials=ShadowTrials(self.root);path=self.root/'news';path.mkdir()
        with sqlite3.connect(path/'events.sqlite3') as db:
            db.executescript('CREATE TABLE meta(id INTEGER,body TEXT); CREATE TABLE events(body TEXT);')
            db.execute('INSERT INTO meta VALUES(1,?)',(json.dumps({'coverage':[{'symbol':'AAPL','checked_at':'2026-10-09T14:00:00+00:00'}]}),))
            db.execute('INSERT INTO events VALUES(?)',(json.dumps({'symbol':'AAPL','first_seen':'2026-10-09T16:00:00+00:00','flag':'Risk review'}),))
        self.assertFalse(trials.news_gate('AAPL','2026-10-09T13:59:00+00:00')[0])
        self.assertTrue(trials.news_gate('AAPL','2026-10-09T14:01:00+00:00')[0])
        self.assertFalse(trials.news_gate('AAPL','2026-10-09T15:01:00+00:00')[0])
    def test_document_excerpts_strip_scripts_and_bound_text(self):
        evidence=document_evidence('<script>bankruptcy</script><p>Revenue increased.</p>'+'x'*5000,NOW.isoformat())
        self.assertEqual([x['term'] for x in evidence['evidence']],['revenue'])
        self.assertLess(len(evidence['evidence'][0]['excerpt']),400)
        self.assertEqual(len(evidence['sha256']),64)
        with self.assertRaises(ValueError):SecClient('name@example.com').document('https://evil.example/filing.htm')
    def test_saved_worker_is_explicitly_not_liveness(self):
        from app import saved_worker
        self.assertEqual(saved_worker(self.root,1000)['state'],'unknown')
        (self.root/'worker-status.json').write_text(json.dumps({'recorded_at':1000,'due':1900,'state':'waiting'}))
        self.assertFalse(saved_worker(self.root,2000)['overdue'])
        self.assertTrue(saved_worker(self.root,3000)['overdue'])
        self.assertIn('not a process',saved_worker(self.root,3000)['note'])
    def test_monitor_manifest_contains_reference_not_runtime_secret(self):
        from mac_setup import monitor_manifest
        manifest=monitor_manifest(self.root,self.root/'client',self.root/'private')
        self.assertTrue(manifest['KeepAlive']);self.assertEqual(manifest['Umask'],0o077)
        self.assertNotIn('EnvironmentVariables',manifest)
        self.assertNotIn('api-key',' '.join(manifest['ProgramArguments']))
    def test_monitor_validation_failure_removes_private_key(self):
        import mac_setup
        from types import SimpleNamespace
        base=self.root/'app';base.mkdir();(base/'config.json').write_text(json.dumps({'python':'/python'}))
        client=self.root/'client';client.write_text('fixture');client.chmod(0o700)
        with patch.object(mac_setup,'BASE',base),patch.object(mac_setup,'PLIST',self.root/'agents/app.plist'),patch('builtins.input',side_effect=[str(client),'tunnel_fixture']),patch.object(mac_setup.getpass,'getpass',return_value='sk-fixture-not-a-real-key'),patch.object(mac_setup.subprocess,'run',return_value=SimpleNamespace(returncode=1)) as run,patch('socket.socket'),patch('builtins.print'):
            with self.assertRaisesRegex(ValueError,'validation failed'):mac_setup.configure_monitor()
        self.assertFalse((base/'private-monitor/runtime-key').exists())
        self.assertNotIn('sk-fixture-not-a-real-key',str(run.call_args.args[0]))
    def test_stale_optional_stage_preserves_holdings_check_but_freezes_trials(self):
        ledger=Ledger(self.root/'paper.sqlite3');ledger.control('start');self.addCleanup(ledger.db.close)
        market=LargeMarket();scanner=RankedScanner(self.root,market)
        def scan():
            with patch('ranked_scanner.datetime') as clock:
                clock.now.return_value=NOW;scanner.cycle(ledger)
        scan();scan();scanner.trials.control('enabled',True)
        original=market.get;stamp=[1000000.0]
        def slow(endpoint,**kw):
            result=original(endpoint,**kw)
            if endpoint=='quote':
                if not kw.get('priority'):stamp[0]+=50
                result['timestamp']=stamp[0]
            return result
        with patch.object(market,'get',side_effect=slow),patch.object(scanner,'timestamp',side_effect=lambda:stamp[0]):scan()
        self.assertTrue(scanner.status['holdings_checked_at'])
        self.assertIsNone(ledger.read()['error'])
        self.assertEqual(scanner.trials.state()['checks'],0)
        self.assertTrue(scanner.status['quote_failures'])
    def test_corrupt_optional_trial_database_cannot_block_holdings(self):
        ledger=Ledger(self.root/'paper.sqlite3');ledger.control('start');self.addCleanup(ledger.db.close)
        market=LargeMarket();scanner=RankedScanner(self.root,market)
        with patch('ranked_scanner.datetime') as clock:
            clock.now.return_value=NOW;scanner.cycle(ledger);scanner.cycle(ledger)
            (self.root/'shadow-trials.sqlite3').write_text('broken optional store')
            scanner=RankedScanner(self.root,market);scanner.cycle(ledger)
        self.assertTrue(scanner.status['holdings_checked_at'])
        self.assertIsNone(ledger.read()['error'])
        self.assertTrue(any('comparison unavailable' in x for x in scanner.status['skipped']))
