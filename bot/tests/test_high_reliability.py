import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from engine import Ledger
from research import ReliableExperiments
from ranked_scanner import RankedScanner
from news import optional_news
from test_universe import LargeMarket
from test_scanner import NOW
import app
import update_worker as u
from mac_update import FILES

class ReliabilityFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_experiment_corruption_keeps_exit_checks_and_cached_setting(self):
        ledger=Ledger(self.root/'paper.sqlite3');self.addCleanup(ledger.db.close);ledger.control('start')
        exp=ReliableExperiments(self.root);market=LargeMarket();scanner=RankedScanner(self.root,market,experiments=exp)
        def scan():
            with patch('ranked_scanner.datetime') as clock:clock.now.return_value=NOW;scanner.cycle(ledger)
        scan();scan();held=set(ledger.read()['positions']);self.assertEqual(len(held),5)
        self.assertEqual(json.loads((self.root/'active-purchase.json').read_text())['active'],200)
        (self.root/'experiments.sqlite3').write_text('broken');market.calls=[]
        # Exit signal without an unverified corporate-action discontinuity.
        original=market.get
        def declining(endpoint,**kw):
            result=original(endpoint,**kw)
            if endpoint=='quote' and kw['symbol'] in held:result['close']=145
            return result
        with patch.object(market,'get',side_effect=declining):scan()
        self.assertTrue(scanner.status['holdings_checked_at']);self.assertIsNone(ledger.read()['error'])
        self.assertEqual(ledger.read()['positions'],{})
        self.assertEqual(sum(d['action']=='SELL' for d in scanner.audit['decisions']),5)
        self.assertIn('storage unavailable',scanner.status['optional_error'])
        self.assertEqual(ReliableExperiments(self.root).active_limit(),200)
        self.assertFalse(any(c[0]=='quote' and not c[2] for c in market.calls))
    def test_missing_validated_setting_never_enables_entries(self):
        (self.root/'experiments.sqlite3').write_text('broken')
        exp=ReliableExperiments(self.root)
        self.assertFalse(exp.state()['enabled']);self.assertIsNotNone(exp.error)
        self.assertIn(exp.active_limit(),(100,150,200))
        with self.assertRaises(ValueError):exp.control('restart')
        self.assertEqual((self.root/'experiments.sqlite3').read_text(),'broken')
    def test_news_corruption_is_inert_and_preserved(self):
        folder=self.root/'news';folder.mkdir();config=folder/'config.json';config.write_text('{broken')
        n=optional_news(self.root);self.assertFalse(n.state()['enabled']);self.assertIn('storage',n.state()['error'])
        n.worker(Mock(),threading.Event());self.assertEqual(config.read_text(),'{broken')
        with self.assertRaises(ValueError):n.configure({'action':'enable'})
    def test_service_starts_with_corrupt_news_and_experiments(self):
        (self.root/'news').mkdir();(self.root/'news/config.json').write_text('{broken')
        (self.root/'experiments.sqlite3').write_text('broken')
        server=Mock()
        with patch.dict('os.environ',{'DATA_DIR':str(self.root),'DASHBOARD_PASSWORD':'x'*24}),patch.object(app,'STOP',threading.Event()),patch.object(app.threading,'Thread'),patch.object(app,'ThreadingHTTPServer',return_value=server),patch('builtins.print'):
            app.main()
        server.serve_forever.assert_called_once();self.assertTrue((self.root/'paper.sqlite3').exists())
    def test_orphan_hold_reports_not_ready_and_does_not_run_scanner(self):
        (self.root/'update_hold').touch();stop=Mock();stop.is_set.side_effect=[False,True]
        with patch.dict('os.environ',{'DATA_DIR':str(self.root)}),patch.object(app,'STOP',stop),patch.object(app,'RankedScanner') as scanner:
            app.worker(Mock());scanner.assert_not_called()
        health=app.worker_health(Mock(is_alive=lambda:True))
        self.assertFalse(health['healthy']);self.assertEqual(health['state'],'update_hold');self.assertIn('option 11',health['message'])
    def prepare_recovery(self):
        base=self.root;root=base/'updates';stage=root/'fixture';backup=stage/'rollback';backup.mkdir(parents=True)
        for name in FILES:(backup/name).write_text('original '+name);(base/name).write_text('new '+name)
        (backup/'data').mkdir();ledger=Ledger(backup/'data/paper.sqlite3');ledger.db.close()
        (backup/'COMPLETE').touch();(base/'data').mkdir();(base/'data/update_hold').touch()
        (base/'data/keep.txt').write_text('interrupted data')
        u.durable_record(root/'transaction.json',{'stage':stage.name,'phase':'replacing','backup_hashes':u.snapshot_hashes(backup)})
        return base,stage,backup
    def test_verified_recovery_restores_and_preserves_failed_data(self):
        base,stage,backup=self.prepare_recovery()
        with patch.object(u.mac_setup,'stop'),patch.object(u.mac_setup,'start'):
            self.assertTrue(u.recover_interrupted(base))
        self.assertEqual((base/'app.py').read_text(),'original app.py');self.assertFalse((base/'data/update_hold').exists())
        self.assertEqual(len(list(backup.glob('failed-data-*/keep.txt'))),1)
        self.assertTrue((stage/'RESOLVED').exists())
        with self.assertRaises(ValueError):u.recover_interrupted(base)
    def test_tampered_recovery_refused_before_stopping_or_mutating(self):
        base,stage,backup=self.prepare_recovery();(backup/'engine.py').write_text('altered')
        with patch.object(u.mac_setup,'stop') as stop:
            with self.assertRaisesRegex(ValueError,'integrity'):u.recover_interrupted(base)
            stop.assert_not_called()
        self.assertEqual((base/'app.py').read_text(),'new app.py');self.assertTrue((base/'data/update_hold').exists())
    def test_missing_snapshot_cannot_clear_hold(self):
        base,stage,backup=self.prepare_recovery();(backup/'COMPLETE').unlink()
        with self.assertRaisesRegex(ValueError,'snapshot unavailable'):u.recover_interrupted(base)
        self.assertTrue((base/'data/update_hold').exists())
    def test_recovery_can_resume_after_data_move_interruption(self):
        base,stage,backup=self.prepare_recovery()
        with patch.object(u.mac_setup,'stop'),patch.object(u.mac_setup,'start'),patch.object(u.shutil,'copytree',side_effect=OSError('interrupted restore')):
            with self.assertRaises(OSError):u.recover_interrupted(base)
        self.assertFalse((base/'data').exists())
        with patch.object(u.mac_setup,'stop'),patch.object(u.mac_setup,'start'):
            self.assertTrue(u.recover_interrupted(base))
        self.assertTrue((base/'data/paper.sqlite3').exists())
    def test_hard_process_exit_leaves_recoverable_durable_snapshot(self):
        import subprocess,sys
        base=self.root;(base/'data').mkdir();(base/'updates/stage').mkdir(parents=True)
        ledger=Ledger(base/'data/paper.sqlite3');ledger.db.close()
        for name in FILES:(base/name).write_text('original '+name)
        script="""import os,sys
from pathlib import Path
from unittest.mock import patch
import update_worker as u
base=Path(sys.argv[1])
with patch.object(u.mac_setup,'stop'),patch.object(u,'apply_update',side_effect=lambda *a:os._exit(91)):
 u.transaction(base,base/'updates/stage',base/'updates/stage','2099.1.1.1')
"""
        result=subprocess.run([sys.executable,'-c',script,str(base)],capture_output=True,text=True)
        self.assertEqual(result.returncode,91,result.stderr)
        self.assertTrue((base/'data/update_hold').exists())
        self.assertEqual(json.loads((base/'updates/transaction.json').read_text())['phase'],'replacing')
        with patch.object(u.mac_setup,'stop'),patch.object(u.mac_setup,'start'):self.assertTrue(u.recover_interrupted(base))
