import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import timedelta
from engine import Ledger
from market import ApiBudget
from strategy import WATCHLIST,screening_universe,LIVE_VERSION
from ranked_scanner import RankedScanner
from test_scanner import FakeMarket,NOW,listing

class LargeMarket(FakeMarket):
    def get(self,endpoint,priority=False,**params):
        if endpoint=='stocks':
            self.calls.append((endpoint,None,priority))
            return {'data':[listing(s) for s in (*WATCHLIST,*(f'S{i:03}' for i in range(450)))]}
        return super().get(endpoint,priority=priority,**params)

class UniverseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.market=LargeMarket();self.ledger=Ledger(self.root/'paper.sqlite3');self.ledger.control('start')
        self.scanner=RankedScanner(self.root,self.market)
    def tearDown(self):self.ledger.db.close();self.tmp.cleanup()
    def scan(self,day=NOW):
        with patch('ranked_scanner.datetime') as clock:
            clock.now.return_value=day
            self.scanner.cycle(self.ledger)
    def test_universe_is_stable_unique_and_retains_original_stocks(self):
        mapping={s:'NASDAQ' for s in (*WATCHLIST,*(f'S{i:03}' for i in range(450)))}
        selected=screening_universe(mapping)
        self.assertEqual(len(selected),300);self.assertEqual(len(set(selected)),300)
        self.assertEqual(selected[:30],list(WATCHLIST))
        self.assertEqual(selected,screening_universe(dict(reversed(list(mapping.items())))))
    def test_first_batch_is_bounded_and_never_trades_unprepared_stocks(self):
        self.scan();self.assertEqual(self.scanner.status['universe_count'],300)
        self.assertEqual(len(self.scanner.status['history_batch']),20)
        self.assertEqual(self.scanner.status['histories_pending'],280)
        self.assertEqual(self.ledger.read()['positions'],{})
        self.market.calls=[];self.scan()
        calls=self.market.calls
        self.assertLess(max(i for i,c in enumerate(calls) if c[0]=='quote'),min(i for i,c in enumerate(calls) if c[0]=='time_series'))
        self.assertEqual(len(self.ledger.read()['positions']),5)
        self.assertTrue(set(self.ledger.read()['positions']).issubset(WATCHLIST[:20]))
    def test_three_hundred_histories_over_batches_with_restart(self):
        self.ledger.control('block')
        for i in range(15):
            if i==7:self.scanner=RankedScanner(self.root,self.market)
            self.scan()
        self.assertEqual(self.scanner.status['histories_ready'],300)
        self.assertEqual(self.scanner.status['histories_pending'],0)
        self.assertEqual(len([c for c in self.market.calls if c[0]=='time_series']),300)
        self.scan();self.assertTrue(self.scanner.status['ranking_complete'])
        self.assertEqual(self.scanner.daily['version'],LIVE_VERSION)
    def test_failed_stock_is_not_retried_repeatedly_and_does_not_block_others(self):
        self.market.bad.add('AAPL');self.scan();self.scan();self.scan()
        self.assertEqual(sum(c[0]=='time_series' and c[1]=='AAPL' for c in self.market.calls),1)
        self.assertEqual(len(self.ledger.read()['positions']),5)
        self.assertIn('AAPL',self.scanner.status['history_failures'])
    def test_normal_budget_exhaustion_still_checks_holdings(self):
        self.scan();self.scan();held=set(self.ledger.read()['positions']);self.market.calls=[]
        self.market.reject_candidates=True
        with patch.object(ApiBudget,'used',return_value=500):self.scan()
        self.assertTrue(held.issubset(self.scanner.status['checked']))
        self.assertFalse(any(c[0]=='time_series' and not c[2] for c in self.market.calls))
        self.assertEqual(self.scanner.status['history_batch'],[])
    def test_scheduling_deferral_keeps_history_pending_and_cursor_retryable(self):
        from market import BudgetDeferred
        original=self.market.get
        def deferred(endpoint,**params):
            if endpoint=='time_series':raise BudgetDeferred('History preparation deferred for candidate checks.')
            return original(endpoint,**params)
        with patch.object(self.market,'get',side_effect=deferred):self.scan()
        self.assertEqual(self.scanner.daily['errors'],{})
        self.assertEqual(self.scanner.daily['attempted'],[])
        self.assertEqual(self.scanner.state['screen_cursor'],0)
        self.assertEqual(self.scanner.status['histories_pending'],300)
        self.assertEqual(self.scanner.status['history_batch'],[])
        self.scan();self.scan()
        self.assertTrue(any(d['action']=='BUY' for d in self.scanner.status['decisions']))

    def test_day_rollover_defers_ranking_then_reuses_provider_verified_unchanged_grid(self):
        self.ledger.control('block');self.scan();cursor=self.scanner.state['screen_cursor']
        self.market.calls=[];self.scan(NOW+timedelta(days=3))
        self.assertEqual(self.scanner.status['selected'],[])
        self.assertEqual(self.scanner.status['history_batch'][0],self.scanner.status['universe_symbols'][cursor])
        self.assertEqual(self.scanner.status['histories_ready'],40)
    def test_held_history_failure_blocks_candidates(self):
        self.scan();self.scan();held=next(iter(self.ledger.read()['positions']))
        self.scanner.daily['bars'].pop(held);self.market.bad.add(held);self.market.calls=[]
        before=self.ledger.read()['positions']
        with self.assertRaisesRegex(ValueError,'Held stock'):self.scan()
        self.assertEqual(self.ledger.read()['positions'],before)
        self.assertFalse(any(c[0]=='quote' for c in self.market.calls))
    def test_disjoint_history_grid_excludes_only_that_stock(self):
        self.ledger.control('block');self.scan()
        row=self.scanner.daily['bars']['AAPL'][0];row['datetime']='2020-01-01'
        self.scan()
        self.assertNotIn('AAPL',self.scanner.status['rankable_symbols'])
        self.assertTrue(self.scanner.status['selected'])
    def test_journal_covers_expanded_universe_without_inventing_features(self):
        self.scan();state=self.scanner.journal.state(limit=400)
        self.assertEqual(state['total'],300)
        self.assertTrue(all(r['features'] is None and r['action']=='SKIP' for r in state['records']))
        self.scan();state=self.scanner.journal.state(limit=300)
        self.assertEqual(sum(r['action']=='BUY' for r in state['records']),5)
        self.assertTrue(all(r['strategy_version']==LIVE_VERSION for r in state['records']))
    def test_shared_history_failure_stops_batch_and_can_recover_same_day(self):
        from market import MarketUnavailable
        original=self.market.get
        def broken(endpoint,**params):
            if endpoint=='time_series':raise MarketUnavailable('Market data: TLS certificate verification failed.')
            return original(endpoint,**params)
        with patch.object(self.market,'get',side_effect=broken) as get:
            with self.assertRaisesRegex(MarketUnavailable,'TLS'):self.scan()
            self.assertEqual(sum(c.args[0]=='time_series' for c in get.call_args_list),1)
        self.assertEqual(self.scanner.status['histories_pending'],300)
        self.assertEqual(self.scanner.daily['attempted'],[])
        self.assertEqual(self.scanner.daily['errors'],{})
        self.assertEqual(self.scanner.state['screen_cursor'],0)
        records=self.scanner.journal.state(300)['records']
        self.assertTrue(all(r['action']=='NOT_EVALUATED' and 'TLS' in r['reason'] for r in records))
        self.scanner=RankedScanner(self.root,self.market);self.scan()
        self.assertEqual(self.scanner.status['histories_ready'],20)
    def test_shared_candidate_failure_stops_before_decisions_and_history_batch(self):
        from market import MarketUnavailable
        self.scan();self.market.calls=[];original=self.market.get
        def broken(endpoint,**params):
            if endpoint=='quote':raise MarketUnavailable('Market data: authentication rejected (401).')
            return original(endpoint,**params)
        with patch.object(self.market,'get',side_effect=broken) as get:
            with self.assertRaisesRegex(MarketUnavailable,'401'):self.scan()
            self.assertEqual(get.call_count,1)
        self.assertEqual(self.ledger.read()['positions'],{})
    def test_shared_catalogue_failure_is_not_silently_swallowed(self):
        from market import MarketUnavailable
        with patch.object(self.market,'get',side_effect=MarketUnavailable('Market data: DNS lookup failed.')) as get:
            with self.assertRaisesRegex(MarketUnavailable,'DNS'):self.scan()
            self.assertEqual(get.call_count,1)
        self.assertTrue(any('DNS' in n for n in self.scanner.status['skipped']))
    def test_old_connection_failures_released_without_resetting_budget_or_valid_bars(self):
        import json
        self.scan();valid=dict(self.scanner.daily['bars'])
        self.scanner.daily['attempted']+=['FAKE1','FAKE2']
        self.scanner.daily['errors']={'FAKE1':'Market-data connection failed; no trades made.','FAKE2':'Daily history is stale'}
        self.scanner.save_daily();ApiBudget(self.root).reserve()
        restored=RankedScanner(self.root,self.market)
        self.assertNotIn('FAKE1',restored.daily['attempted']);self.assertNotIn('FAKE1',restored.daily['errors'])
        self.assertIn('FAKE2',restored.daily['attempted']);self.assertEqual(restored.daily['bars'],valid)
        self.assertEqual(ApiBudget(self.root).used(),1)
    def test_completed_histories_saved_when_later_request_fails(self):
        from market import MarketUnavailable
        original=self.market.get;count=0
        def broken(endpoint,**params):
            nonlocal count
            if endpoint=='time_series':
                count+=1
                if count==3:raise MarketUnavailable('Market data: connection timed out.')
            return original(endpoint,**params)
        with patch.object(self.market,'get',side_effect=broken):
            with self.assertRaises(MarketUnavailable):self.scan()
        restored=RankedScanner(self.root,self.market)
        self.assertEqual(len(restored.daily['bars']),2)
        self.assertEqual(len(restored.daily['attempted']),2)
        self.assertEqual(restored.state['screen_cursor'],2)
    def test_history_outage_does_not_erase_already_recorded_buys(self):
        from market import MarketUnavailable
        self.scan();original=self.market.get
        def broken(endpoint,**params):
            if endpoint=='time_series':raise MarketUnavailable('Market data: DNS lookup failed.')
            return original(endpoint,**params)
        with patch.object(self.market,'get',side_effect=broken):
            with self.assertRaises(MarketUnavailable):self.scan()
        self.assertEqual(len(self.ledger.read()['positions']),5)
        records=self.scanner.journal.state(300)['records']
        self.assertEqual(sum(r['action']=='BUY' for r in records),5)
