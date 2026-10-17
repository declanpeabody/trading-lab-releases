import tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from research import StrategyObservatory
from engine import Ledger
from app import saved_worker,persist_worker
from test_universe import LargeMarket
from test_scanner import NOW
from ranked_scanner import RankedScanner

class ObservatoryReleaseTests(unittest.TestCase):
 def test_import_atomic_dedup_and_no_lookahead(self):
  with tempfile.TemporaryDirectory() as root:
   o=StrategyObservatory(root);now=datetime(2026,10,15,15,tzinfo=timezone.utc)
   header=','.join(o.columns)+'\n';line='Owner,Trend,paper,AAPL,BUY,2026-10-15T14:00:00Z,100\n'
   self.assertEqual(o.import_csv(header+line,now),1)
   self.assertEqual(o.import_csv(header+line,now+timedelta(hours=1)),0)
   with self.assertRaises(ValueError):o.import_csv(header+line+line.replace(',100',',NaN'),now)
   self.assertEqual(o.state()['total'],1)
   o.observe({'last_success':(now-timedelta(seconds=1)).isoformat(),'marks':{'AAPL':120}},now)
   self.assertIsNone(o.state()['records'][0]['baseline'])
   at=now+timedelta(seconds=30);o.observe({'last_success':at.isoformat(),'marks':{'AAPL':120}},at)
   later=at+timedelta(hours=24);o.observe({'last_success':later.isoformat(),'marks':{'AAPL':126}},later)
   row=o.state()['records'][0];self.assertEqual(row['baseline']['price_usd'],120)
   self.assertAlmostEqual(row['outcomes']['24h']['price_return_pct'],5)
   self.assertEqual(row['first_seen'],now.isoformat())
   self.assertFalse((Path(root)/'paper.sqlite3').exists())
 def test_stale_error_and_invalid_csv_cannot_create_evidence(self):
  with tempfile.TemporaryDirectory() as root:
   o=StrategyObservatory(root);now=datetime.now(timezone.utc);header=','.join(o.columns)+'\n'
   for line in ('x,y,paper,AAPL,BUY,2026-10-15T14:00:00,100','x,y,paper,AAPL,BUY,2099-10-15T14:00:00Z,100'):
    with self.assertRaises(ValueError):o.import_csv(header+line,now)
   o.import_csv(header+'x,y,unknown,AAPL,SELL,2026-10-15T14:00:00Z,100',now)
   later=now+timedelta(hours=1)
   o.observe({'last_success':(now+timedelta(seconds=1)).isoformat(),'marks':{'AAPL':99}},later)
   o.observe({'last_success':later.isoformat(),'marks':{'AAPL':99},'error':'blocked'},later)
   self.assertIsNone(o.state()['records'][0]['baseline'])
 def test_unit_returns_survive_deposit_and_restart(self):
  with tempfile.TemporaryDirectory() as root:
   path=Path(root)/'paper.sqlite3';l=Ledger(path);l.control('start');bars={'AAPL':list(range(1,51))}
   l.cycle({'AAPL':100},bars,.75,'2026-10-15','2026-10-15T15:00:00Z',symbols=['AAPL'])
   nav=l.read()['performance']['nav'];l.db.close()
   l=Ledger(path,initial_cash=10000,max_positions=5,purchase_cap=1000);l.control('block')
   l.cycle({'AAPL':100},bars,.75,'2026-10-16','2026-10-16T15:00:00Z',symbols=['AAPL'])
   self.assertAlmostEqual(l.read()['performance']['nav'],nav)
   l.cycle({'AAPL':105},bars,.76,'2026-10-16','2026-10-16T16:00:00Z',symbols=['AAPL'])
   r=l.accounting()['open_attribution'][0];p=l.read()['positions']['AAPL']
   self.assertAlmostEqual(r['price_pnl_gbp']+r['fx_pnl_gbp']+r['entry_cost_residual_gbp'],p['qty']*105*.76-p['cost_gbp'])
   self.assertEqual(len(l.read()['performance']['daily']),2);l.db.close()
 def test_worker_version_survives_disk_snapshot(self):
  with tempfile.TemporaryDirectory() as root,patch.dict('os.environ',{'DATA_DIR':root}):
   persist_worker();s=saved_worker(root);self.assertEqual(s['version'],'2026.10.17.1');self.assertTrue(s['started_at'])
 def test_stale_held_quote_refresh_is_bounded(self):
  with tempfile.TemporaryDirectory() as root:
   l=Ledger(Path(root)/'paper.sqlite3');l.control('start');m=LargeMarket();scanner=RankedScanner(root,m)
   with patch('ranked_scanner.datetime') as clock:
    clock.now.return_value=NOW;scanner.cycle(l);scanner.cycle(l)
    symbol=next(iter(l.read()['positions']));original=m.get;count=[0]
    def fetch(endpoint,priority=False,**params):
     r=original(endpoint,priority=priority,**params)
     if endpoint=='quote' and params['symbol']==symbol:
      count[0]+=1
      if count[0]==1:r['timestamp']=NOW.timestamp()-130
     return r
    m.get=fetch;scanner.cycle(l)
    self.assertEqual(count[0],2);self.assertIn(symbol,scanner.status['held_quote_refreshes']);self.assertNotIn(symbol,scanner.status.get('quote_failures',{}))
    count[0]=0
    def stale(endpoint,priority=False,**params):
     r=original(endpoint,priority=priority,**params)
     if endpoint=='quote' and params['symbol']==symbol:r['timestamp']=NOW.timestamp()-130;count[0]+=1
     return r
    m.get=stale
    with self.assertRaises(ValueError):scanner.cycle(l)
    self.assertLessEqual(count[0],2)
   l.db.close()
