import copy
import json
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from engine import Ledger
from ranked_scanner import RankedScanner, Journal
from test_scanner import FakeMarket, NOW
import app
import mac_update
import update_worker


class BasisTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ledger=Ledger(Path(self.tmp.name)/'paper.db');self.addCleanup(self.ledger.db.close)
        self.ledger.control('start')
        self.rows=[{'datetime':(date(2026,1,1)+timedelta(days=i)).isoformat(),'close':51+i,'volume':50000} for i in range(50)]
        self.cycle(101,self.rows,'2026-03-01')
    def cycle(self,price,rows,day):
        return self.ledger.cycle({'AAPL':price},{'AAPL':[r['close'] for r in rows]},.8,day,day+'T15:00:00Z',symbols=['AAPL'],history_rows={'AAPL':rows})
    def test_split_cannot_trigger_false_loss_or_change_equity(self):
        before=self.ledger.read();rows=copy.deepcopy(self.rows)
        for row in rows:row['close']/=2
        actions=self.cycle(50.5,rows,'2026-03-02');after=self.ledger.read()
        self.assertEqual(actions[0]['action'],'NOT_EVALUATED')
        self.assertEqual(after['equity'],before['equity']);self.assertFalse(after['risk_latched'])
        pending=after['positions']['AAPL']['reconciliation'];qty=before['positions']['AAPL']['qty']
        self.ledger.reconcile('AAPL',pending['id'],2,'Issuer split notice effective 2026-03-02')
        adjusted=self.ledger.read()['positions']['AAPL']
        self.assertEqual(adjusted['qty'],qty*2)
        self.assertEqual(adjusted['cost_gbp'],before['positions']['AAPL']['cost_gbp'])
        with self.assertRaises(ValueError):self.ledger.reconcile('AAPL',pending['id'],2,'Same issuer notice')
        self.assertEqual(self.cycle(50.5,rows,'2026-03-02')[0]['action'],'HOLD')
        self.assertAlmostEqual(self.ledger.read()['equity'],before['equity'])
    def test_reverse_split_and_invalid_ratio(self):
        rows=copy.deepcopy(self.rows)
        for row in rows:row['close']*=10
        self.cycle(1010,rows,'2026-03-02');pending=self.ledger.read()['positions']['AAPL']['reconciliation']
        for value in [0,-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):self.ledger.reconcile('AAPL',pending['id'],value,'Issuer notice checked')
        qty=self.ledger.read()['positions']['AAPL']['qty']
        self.ledger.reconcile('AAPL',pending['id'],.1,'Issuer reverse split notice')
        self.assertAlmostEqual(self.ledger.read()['positions']['AAPL']['qty'],qty/10)
    def test_legacy_holding_requires_verification_and_real_loss_can_resume(self):
        with self.ledger.lock:
            s=self.ledger.read();s['positions']['AAPL'].pop('basis_reference');self.ledger.write(s)
        self.cycle(60,self.rows,'2026-03-02');p=self.ledger.read()['positions']['AAPL']['reconciliation']
        self.ledger.reconcile('AAPL',p['id'],1,'Issuer confirms no corporate action')
        self.assertEqual(self.cycle(60,self.rows,'2026-03-02')[0]['action'],'SELL')


class WorkerTests(unittest.TestCase):
    def test_status_write_and_account_disk_errors_retry_without_worker_exit(self):
        original=dict(app.WORKER);self.addCleanup(lambda:setattr(app,'WORKER',original))
        stop=Mock();stop.is_set.side_effect=[False,False,True]
        ledger=Mock();ledger.read.return_value={'paused':False};ledger.fail.side_effect=OSError('full')
        scanner=Mock();scanner.record_status.side_effect=[OSError('full'),None]
        seen=[];stop.wait.side_effect=lambda delay:seen.append((delay,dict(app.WORKER)))
        with patch.object(app,'STOP',stop),patch.object(app,'RankedScanner',return_value=scanner),patch.object(app,'session_window',return_value=True),patch.dict('os.environ',{'DATA_DIR':'/tmp/nonexistent-trading-test'}):
            app.worker(ledger)
        self.assertEqual([x[0] for x in seen],[60,900]);self.assertEqual(seen[0][1]['state'],'failed')
        self.assertEqual(seen[1][1]['state'],'waiting');self.assertEqual(scanner.cycle.call_count,2)
    def test_construction_failure_is_visible_and_retried(self):
        original=dict(app.WORKER);self.addCleanup(lambda:setattr(app,'WORKER',original))
        stop=Mock();stop.is_set.side_effect=[False,True]
        with patch.object(app,'STOP',stop),patch.object(app,'RankedScanner',side_effect=OSError('full')):
            app.worker(Mock())
        self.assertFalse(app.worker_health(Mock(is_alive=lambda:True))['healthy'])
        stop.wait.assert_called_once_with(60)


class ScannerReliability(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.ledger=Ledger(self.root/'paper.db');self.addCleanup(self.ledger.db.close);self.ledger.control('start')
        self.market=FakeMarket();self.scanner=RankedScanner(self.root,self.market)
    def scan(self,day=NOW):
        with patch('ranked_scanner.datetime') as clock:
            clock.now.return_value=day;self.scanner.cycle(self.ledger)
    def test_rejected_quotes_surface_error_but_preparation_continues(self):
        self.scan();original=self.market.get
        def get(endpoint,**kw):
            result=original(endpoint,**kw)
            if endpoint=='quote' and kw['symbol']!='USD/GBP':result['timestamp']=1;result.pop('last_quote_at',None)
            return result
        with patch.object(self.market,'get',side_effect=get):self.scan()
        self.assertIn('No valid ranked',self.ledger.read()['error'])
        self.assertIsNone(self.ledger.read()['last_success']);self.assertEqual(self.scanner.status['decision_state'],'data_blocked')
    def test_unchanged_session_cache_reuses_bars_only_after_fresh_response(self):
        self.ledger.control('block');self.scan();old=copy.deepcopy(self.scanner.daily['bars'])
        self.scan(NOW+timedelta(days=3))
        self.assertEqual(self.scanner.status['selected'],[])
        self.assertTrue(all(s in self.scanner.daily['bars'] for s in old))
        self.assertIn('verified_grid',self.scanner.daily)
    def test_new_session_grid_does_not_promote_old_bars(self):
        self.ledger.control('block');self.scan();original=self.market.get
        def get(endpoint,**kw):
            raw=original(endpoint,**kw)
            if endpoint=='time_series':
                for r in raw['values']:r['datetime']=(date.fromisoformat(r['datetime'][:10])+timedelta(days=3)).isoformat()
            return raw
        with patch.object(self.market,'get',side_effect=get):self.scan(NOW+timedelta(days=3))
        self.assertEqual(self.scanner.status['selected'],[])
        self.assertTrue(all(r['datetime']< (NOW+timedelta(days=3)).date().isoformat() for rows in self.scanner.daily['bars'].values() for r in rows))


class StorageAndRecovery(unittest.TestCase):
    def test_low_space_refuses_before_stopping_or_replacing(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);stage=base/'updates'/'stage';stage.mkdir(parents=True)
            with patch.object(mac_update.shutil,'disk_usage',return_value=Mock(free=1)),patch.object(update_worker.mac_setup,'stop') as stop,patch.object(update_worker,'apply_update') as apply:
                self.assertFalse(update_worker.transaction(base,stage,stage,'2099.1.1.1'))
                stop.assert_not_called();apply.assert_not_called()
    def test_cleanup_preserves_unresolved_and_selected_stages(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);root=base/'updates';root.mkdir()
            for n in range(8):
                p=root/f'{n:032x}';p.mkdir();(p/'RESOLVED').touch()
            pending=root/('f'*32);pending.mkdir();(pending/'rollback').mkdir()
            protected=f'{0:032x}'
            mac_update.cleanup_updates(base,[protected])
            self.assertTrue(pending.exists());self.assertTrue((root/protected).exists())
            self.assertEqual(len(list(root.iterdir())),5)
    def test_manual_restart_failure_restores_full_account(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)/'installed';source=Path(folder)/'source';base.mkdir();source.mkdir();(base/'data').mkdir()
            for name in mac_update.FILES:
                (base/name).write_text('old '+name);(source/name).write_text('new '+name)
            (source/'version.py').write_text("VERSION='2099.1.1.1'\n")
            (base/'data'/'account.json').write_text('original')
            def apply(*args):
                (base/'app.py').write_text('broken');(base/'data'/'account.json').write_text('mutated')
            with patch.object(update_worker.mac_setup,'stop'),patch.object(update_worker.mac_setup,'start',side_effect=[SystemExit('startup failed'),None]),patch.object(update_worker,'apply_update',side_effect=apply):
                with self.assertRaisesRegex(ValueError,'restored'):update_worker.manual_update(base,source)
            self.assertEqual((base/'data'/'account.json').read_text(),'original')
            self.assertEqual((base/'app.py').read_text(),'old app.py')
    def test_journal_retention_keeps_recent_data_and_reuses_db_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=Journal(folder);audit={'bars':{},'prices':{},'decisions':[],'before':{'positions':{}}};status={'universe_symbols':['AAPL']}
            journal.record('2020-01-01T12:00:00Z','2020-01-01',audit,status)
            journal.record('2026-10-13T12:00:00Z','2026-10-13',audit,status)
            self.assertEqual(journal.state()['total'],1)
            self.assertEqual(journal.state()['records'][0]['day'],'2026-10-13')
