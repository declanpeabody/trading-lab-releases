import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from engine import Ledger
from research import ShadowTrials
from test_universe import LargeMarket
from test_scanner import NOW

class TradeReviewTests(unittest.TestCase):
 def ledger(self,root):
  x=Ledger(Path(root)/'paper.sqlite3');x.control('start');self.addCleanup(x.db.close);return x
 def test_atomic_execution_and_cash_cost_reconciliation(self):
  with tempfile.TemporaryDirectory() as root:
   x=self.ledger(root);bars={'AAPL':list(range(1,51))}
   x.cycle({'AAPL':100},bars,.75,'2026-10-15','2026-10-15T15:00:00+00:00',symbols=['AAPL'])
   buy=x.accounting()['executions'][0];self.assertEqual(buy['side'],'BUY')
   self.assertAlmostEqual(-buy['net_cash_gbp'],buy['gross_gbp']+buy['price_cost_gbp']+buy['fx_cost_gbp'])
   x.cycle({'AAPL':20},bars,.76,'2026-10-16','2026-10-16T15:00:00+00:00',symbols=['AAPL'])
   sell=x.accounting()['executions'][0];self.assertEqual(sell['side'],'SELL');self.assertEqual(sell['entry_execution_id'],buy['id'])
   self.assertAlmostEqual(sell['net_cash_gbp'],sell['gross_gbp']-sell['price_cost_gbp']-sell['fx_cost_gbp'])
   self.assertAlmostEqual(sell['realised_pnl_gbp'],sell['net_cash_gbp']+buy['net_cash_gbp'])
   self.assertAlmostEqual(x.read()['cash']-1000,sell['realised_pnl_gbp'])
 def test_failed_execution_insert_rolls_back_account(self):
  with tempfile.TemporaryDirectory() as root:
   x=self.ledger(root);before=x.read()
   x.db.execute("CREATE TRIGGER reject_fill BEFORE INSERT ON executions BEGIN SELECT RAISE(ABORT,'test'); END")
   with self.assertRaises(sqlite3.IntegrityError):x.cycle({'AAPL':100},{'AAPL':list(range(1,51))},.75,'2026-10-15','2026-10-15T15:00:00Z',symbols=['AAPL'])
   self.assertEqual(before,x.read());self.assertEqual(x.accounting()['recorded_executions'],0)
 def test_funding_once_and_benchmark_resets_without_deposit_profit(self):
  with tempfile.TemporaryDirectory() as root:
   x=self.ledger(root);s=x.read();s.update(last_success='2026-10-15T15:00:00+00:00',equity=980);x.write(s)
   x.record_benchmark(100,.75,'2026-10-15T15:00:10+00:00',s['last_success'],'2026-10-15')
   s=x.read();s.update(last_success='2026-10-16T15:00:00+00:00',equity=990);x.write(s)
   x.record_benchmark(101,.75,'2026-10-16T15:00:10+00:00',s['last_success'],'2026-10-16')
   b=x.read()['benchmark'];self.assertAlmostEqual(b['observations'][-1]['account_return_pct'],100*(990/980-1));self.assertAlmostEqual(b['observations'][-1]['spy_price_return_pct'],1)
   x.db.close()
   y=Ledger(Path(root)/'paper.sqlite3',initial_cash=10000,max_positions=5,purchase_cap=1000)
   self.addCleanup(y.db.close);s=y.read();s['last_success']='2026-10-17T15:00:00+00:00';y.write(s)
   y.record_benchmark(102,.75,'2026-10-17T15:00:10+00:00',s['last_success'],'2026-10-17')
   self.assertEqual(len(y.accounting()['funding']),1);self.assertEqual(y.read()['benchmark']['observations'][0]['account_return_pct'],0);self.assertEqual(len(y.read()['benchmark_history']),1)
   y.record_benchmark(105,.75,'2026-10-18T15:00:00+00:00',s['last_success'],'2026-10-18');self.assertEqual(len(y.read()['benchmark']['observations']),1)
 def test_confirmed_entry_waits_and_keeps_exits(self):
  with tempfile.TemporaryDirectory() as root:
   t=ShadowTrials(root);t.control('enabled',True)
   bars={s:LargeMarket().get('time_series',symbol=s)['values'][-50:] for s in ('AAPL','MSFT')}
   t.observe({'AAPL':200,'MSFT':200},bars,.75,'2026-10-09','2026-10-09T14:00:00+00:00',['AAPL','MSFT'])
   self.assertEqual(t.state()['books']['confirmed_entry']['positions'],{})
   t.observe({'AAPL':200,'MSFT':200},bars,.75,'2026-10-09','2026-10-09T14:30:00+00:00',['AAPL','MSFT'])
   self.assertEqual(len(t.state()['books']['confirmed_entry']['positions']),2)
   self.assertLessEqual(len(t.state()['books']['concentration']['positions']),1)
   t.observe({'AAPL':190,'MSFT':190},bars,.75,'2026-10-09','2026-10-09T15:00:00+00:00',[])
   self.assertFalse(t.state()['books']['confirmed_entry']['positions'])
 def test_old_comparison_evidence_preserved_and_paused(self):
  with tempfile.TemporaryDirectory() as root:
   t=ShadowTrials(root)
   def old(s):
    s['books'].pop('concentration');s['books'].pop('confirmed_entry');s.update(enabled=True,checks=10)
   t.change(old);t=ShadowTrials(root);state=t.state();self.assertFalse(state['enabled']);self.assertTrue(state['invalid']);self.assertEqual(state['checks'],10)

 def test_research_quote_has_capacity_reason(self):
  from ranked_scanner import Journal
  with tempfile.TemporaryDirectory() as root:
   j=Journal(root);bars=LargeMarket().get('time_series',symbol='AAPL')['values'][-50:]
   j.record('2026-10-09T15:00:00+00:00','2026-10-09',
    {'bars':{'AAPL':bars},'prices':{'AAPL':200},'decisions':[],'before':{'positions':{}}},
    {'universe_symbols':['AAPL'],'selected':['AAPL'],'ranking_complete':True,'quote_requested':['AAPL'],'active_entry_needed':False,'entry_skip_reason':'Position limit reached'})
   record=j.state()['records'][0];self.assertEqual(record['action'],'SKIP');self.assertIn('research only',record['reason'])
 def test_monitor_uses_atomic_fill_even_without_journal_trade(self):
  from app import monitoring_decisions
  from ranked_scanner import Journal
  with tempfile.TemporaryDirectory() as root:
   x=self.ledger(root);Journal(root)
   x.cycle({'AAPL':100},{'AAPL':list(range(1,51))},.75,'2026-10-15','2026-10-15T15:00:00Z',symbols=['AAPL'])
   result=monitoring_decisions(root,{'limit':100},True)
   self.assertEqual(len(result['records']),1);self.assertTrue(result['records'][0]['execution_accounting'])

   fill=x.accounting()['executions'][0]
   # Journal timestamp can be later than the mandatory execution stage.
   body=dict(at='2026-10-15T15:00:50Z',day='2026-10-15',symbol='AAPL',action='BUY',execution_id=fill['id'])
   with sqlite3.connect(Path(root)/'decisions.sqlite3') as db:
    db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?)',('fixture',body['at'],body['day'],'AAPL',json.dumps(body),'{}'))
   self.assertEqual(len(monitoring_decisions(root,{'limit':100},True)['records']),1)
