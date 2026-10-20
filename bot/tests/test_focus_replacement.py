import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from engine import Ledger,new_paper_state
from research import FasterDailyLedger,ExperimentLedger,ShadowTrials
from ranked_scanner import RankedScanner
from market import BudgetDeferred
from test_universe import LargeMarket
from test_scanner import NOW

class FocusReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.ledger=Ledger(self.root/'paper.sqlite3',initial_cash=10000,max_positions=5,purchase_cap=1000)
        self.addCleanup(self.ledger.db.close);self.ledger.control('start')
        self.market=LargeMarket();self.scanner=RankedScanner(self.root,self.market)
    def scan(self):
        with patch('ranked_scanner.datetime') as clock:
            clock.now.return_value=NOW;self.scanner.cycle(self.ledger)
    def test_four_focus_one_wide_and_restart_stability(self):
        self.scan();focus=self.scanner.status['focus_symbols'];batch=self.scanner.status['history_batch']
        self.assertEqual(len(focus),50);self.assertEqual(len(batch),5)
        self.assertEqual(sum(s in focus for s in batch),4)
        self.assertEqual(self.ledger.read()['positions'],{})
        self.scanner=RankedScanner(self.root,self.market);self.scan()
        self.assertEqual(focus,self.scanner.status['focus_symbols'])
        self.assertLessEqual(len(self.ledger.read()['positions']),5)
    def test_liquidity_priority_is_frozen_for_session(self):
        names=['AAPL']+[f'X{i:03}' for i in range(60)]
        rows=self.market.get('time_series',symbol='X059')['values'][-50:]
        self.scanner.daily={'previous_bars':{'X059':rows},'bars':{}}
        focus=self.scanner.focus_symbols(names)
        self.assertEqual(focus[:2],['AAPL','X059'])
        self.scanner.daily['bars']['X058']=[r|{'volume':100000000} for r in rows]
        self.assertEqual(focus,self.scanner.focus_symbols(names))
    def test_history_deferral_keeps_focus_pending(self):
        original=self.market.get
        def fetch(endpoint,**kwargs):
            if endpoint=='time_series':raise BudgetDeferred('reserve')
            return original(endpoint,**kwargs)
        with patch.object(self.market,'get',side_effect=fetch):self.scan()
        self.assertEqual(self.scanner.daily['attempted'],[])
        self.assertEqual(self.scanner.state['screen_cursor'],0)
        self.assertEqual(self.scanner.status['history_batch'],[])
        self.scan();self.assertEqual(len(self.scanner.status['history_batch']),5)
    def test_sale_bypasses_cooldown_without_same_day_reentry(self):
        self.scan();self.scan();held=next(iter(self.ledger.read()['positions']))
        original=self.market.get;self.market.calls=[]
        def fetch(endpoint,**kwargs):
            result=original(endpoint,**kwargs)
            if endpoint=='quote' and kwargs.get('symbol')==held:result['close']=188
            return result
        with patch.object(self.market,'get',side_effect=fetch):self.scan()
        self.assertTrue(self.scanner.status['replacement_priority'])
        self.assertTrue(any(d['action']=='SELL' and d['symbol']==held for d in self.scanner.audit['decisions']))
        self.assertTrue(any(endpoint=='quote' and not priority for endpoint,s,priority in self.market.calls))
        self.assertNotIn(held,self.ledger.read()['positions'])
        self.assertLessEqual(len(self.ledger.read()['positions']),5)
        self.assertIsNone(self.ledger.read()['error'])
    def test_candidate_failure_does_not_undo_sale(self):
        self.scan();self.scan();held=next(iter(self.ledger.read()['positions']));original=self.market.get
        def fetch(endpoint,**kwargs):
            if endpoint=='quote' and not kwargs.get('priority'):raise BudgetDeferred('Normal reserve reached')
            result=original(endpoint,**kwargs)
            if endpoint=='quote' and kwargs.get('symbol')==held:result['close']=188
            return result
        with patch.object(self.market,'get',side_effect=fetch):self.scan()
        self.assertNotIn(held,self.ledger.read()['positions']);self.assertIsNone(self.ledger.read()['error'])
        self.assertEqual(self.ledger.accounting()['executions'][0]['side'],'SELL')
    def test_faster_exits_are_isolated(self):
        history=list(range(100,150));body=new_paper_state();body['paused']=False
        slow=ExperimentLedger(body);fast=FasterDailyLedger(body)
        for book in (slow,fast):
            book.cycle({'AAPL':150},{'AAPL':history},.75,'2026-10-15','2026-10-15T15:00:00Z',symbols=['AAPL'],purchase_limit=200)
            book.cycle({'AAPL':144},{'AAPL':history},.75,'2026-10-16','2026-10-16T15:00:00Z',symbols=['AAPL'],purchase_limit=200)
        self.assertIn('AAPL',slow.read()['positions']);self.assertNotIn('AAPL',fast.read()['positions'])
        self.assertEqual(self.ledger.read()['positions'],{})
        self.assertFalse(ShadowTrials(self.root).state()['review_gates']['faster_daily']['eligible_for_promotion'])

    def test_focus_completes_and_wide_rotation_continues(self):
        self.ledger.control('block')
        for _ in range(13):self.scan()
        focus=set(self.scanner.status['focus_symbols']);ready=set(self.scanner.daily['bars'])
        self.assertTrue(focus.issubset(ready));self.assertGreaterEqual(len(ready-focus),13)
        cursor=self.scanner.state['screen_cursor'];self.scan()
        self.assertTrue(all(s not in focus for s in self.scanner.status['history_batch']))
        self.assertGreater(self.scanner.state['screen_cursor'],cursor)
