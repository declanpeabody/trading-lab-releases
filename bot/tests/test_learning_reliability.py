import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from engine import Ledger
from research import DailyLearning,ResearchSignals
from portfolio_comparison import PortfolioComparison
from app import monitoring_learning,monitoring_rpc,set_monitoring

UTC=timezone.utc

class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.ledger=Ledger(self.root/'paper.sqlite3',initial_cash=10000,purchase_cap=1000)
        self.addCleanup(self.ledger.db.close)
        self.now=datetime(2026,11,2,22,tzinfo=UTC)
        self.signals=ResearchSignals(self.root)
        self.learning=DailyLearning(self.root)

    def row(self,ident='A',age=25,base=True,outcomes=None):
        at=(self.now-timedelta(hours=age)).isoformat()
        return dict(id=ident,symbol=ident,module='trend_20_50',source='scanner',direction='BUY',first_seen=at,
            baseline={'at':at,'price_usd':100} if base else None,outcomes=outcomes or {})

    def store(self,*rows):
        with self.signals.connect() as db:
            db.executemany('INSERT INTO signals VALUES(?,?)',[(r['id'],json.dumps(r)) for r in rows])

    def test_archive_keeps_pending_and_all_exported_evidence(self):
        self.store(self.row('pending'),self.row('expired',193),self.row('no_base',25,False),
            self.row('complete',121,outcomes={'24':{'hypothetical_long_after_cost_pct':1},'120':{}}))
        self.signals.observe({}, {},self.now.isoformat(),now=self.now)
        state=self.signals.state(export=True,now=self.now)
        self.assertEqual((state['active'],state['archived'],state['total']),(1,3,4))
        self.assertEqual({r['id'] for r in state['records']},{'pending','expired','no_base','complete'})
        self.assertEqual(ResearchSignals(self.root).state()['total'],4)

    def test_full_pending_store_not_deleted_to_make_space(self):
        rows=[self.row(str(i)) for i in range(10000)];self.store(*rows)
        self.signals.observe({}, {},self.now.isoformat(),now=self.now)
        self.assertTrue(self.signals.state()['full'])
        self.assertEqual(self.signals.state()['active'],10000)

    def test_archive_releases_capacity_and_preserves_dedup(self):
        rows=[self.row(str(i),193) for i in range(10000)];self.store(*rows)
        self.signals.observe({}, {},self.now.isoformat(),now=self.now)
        self.assertFalse(self.signals.state()['full'])
        self.assertEqual(self.signals.state()['total'],10000)
        # Terminal source IDs must not be inserted again by subsequent feed discovery.
        item=dict(id='story',symbol='A',first_seen=self.now.isoformat(),_kind='feed')
        with patch.object(self.signals,'sources',return_value=([item],[])):
            self.signals.observe({}, {},self.now.isoformat(),now=self.now)
            later=self.now+timedelta(hours=25)
            self.signals.observe({}, {},later.isoformat(),now=later)
            self.signals.observe({}, {},self.now.isoformat(),now=self.now)
        self.assertEqual(self.signals.state()['total'],10001)

    def test_followup_deadlines_and_resolution_without_new_signals(self):
        self.store(self.row('late',95),self.row('early',25),self.row('expired',193),self.row('future',-1))
        self.assertEqual(self.signals.followups(self.now,1),['late'])
        self.signals.observe({'late':102},{},self.now.isoformat(),now=self.now,resolve_only=True)
        self.assertEqual(self.signals.followups(self.now,10),['early'])
        row=next(r for r in self.signals.state(export=True)['records'] if r['id']=='late')
        self.assertIn('24',row['outcomes']);self.assertNotIn('120',row['outcomes'])

    def test_review_runs_after_close_and_dst_is_local(self):
        for at in (datetime(2026,10,30,20,14,tzinfo=UTC),datetime(2026,11,2,21,14,tzinfo=UTC)):
            self.assertFalse(self.learning.review(self.ledger,at))
            self.assertTrue(self.learning.review(self.ledger,at+timedelta(minutes=1)))
        self.assertFalse(self.learning.review(self.ledger,datetime(2026,11,1,22,tzinfo=UTC)))

    def test_recovery_no_hindsight_or_model_selection_and_idempotent(self):
        row=self.row('A',age=24*6)
        row['outcomes']={'120':{'at':self.now.isoformat(),'hypothetical_long_after_cost_pct':10}}
        self.store(row)
        before=self.ledger.read()
        with patch('research.passes',return_value=True):self.assertEqual(self.learning.recover(self.ledger,self.now),1)
        state=self.learning.state();report=state['last_review']
        self.assertTrue(report['recovered']);self.assertEqual(report['usable_observations'],0)
        self.assertEqual(report['completed_at'],self.now.isoformat())
        self.assertEqual(state['model']['phase'],'development')
        self.assertFalse((self.root/'learning-candidates').exists())
        self.assertEqual(self.learning.recover(self.ledger,self.now),0)
        self.assertEqual(self.ledger.read(),before)

    def test_recovery_does_not_invent_unobserved_days(self):
        self.assertEqual(self.learning.recover(self.ledger,self.now),0)
        self.assertIsNone(self.learning.state()['last_review'])

    def test_health_error_survives_restart(self):
        class Stop:
            stopped=False
            def is_set(self):return self.stopped
            def wait(self,n):self.stopped=True
        with patch.object(self.learning,'review',side_effect=OSError()):self.learning.worker(self.ledger,Stop())
        state=DailyLearning(self.root).state()
        self.assertIsNotNone(state['error']);self.assertIsNotNone(state['health']['last_attempt'])
        self.assertNotIn('last_success',state['health'])

    def test_monitor_missing_is_unknown_and_never_creates_stores(self):
        empty=self.root/'empty';empty.mkdir()
        with patch('app.Market.get',side_effect=AssertionError()):state=monitoring_learning(empty)
        self.assertIsNone(state['last_review']);self.assertIsNone(state['evidence'])
        self.assertEqual(list(empty.iterdir()),[])

    def test_monitor_learning_is_read_only_and_version_separate(self):
        self.store(self.row());self.learning.review(self.ledger,self.now)
        PortfolioComparison(self.root)
        set_monitoring(self.root,True)
        before={p.name:p.read_bytes() for p in self.root.glob('*.sqlite3')}
        response=monitoring_rpc(dict(jsonrpc='2.0',id=1,method='tools/call',params={'name':'get_learning_status'}),self.root)
        output=json.loads(response['result']['content'][0]['text'])
        self.assertEqual(output['evidence']['total'],1)
        self.assertIsNone(output['worker_version']);self.assertIsNotNone(output['monitor_version'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.glob('*.sqlite3')})


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        from test_portfolio_replacement import PortfolioReplacementTests
        self.fixture=PortfolioReplacementTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.path.parent
        self.comparison=PortfolioComparison(self.root)
        self.bars=self.fixture.bars;self.prices=self.fixture.prices

    def observe(self,day='2026-10-21',entries=None,benchmark=None,buy_block=False):
        self.comparison.observe(self.prices,self.bars,.75,day,day+'T15:00:00+00:00',
            list('ABCDE') if entries is None else entries,45,buy_block=buy_block,benchmark=benchmark)

    def seed(self):
        original=self.fixture.ledger.read()
        self.comparison.change(lambda s,db:s.update(books={k:copy.deepcopy(original) for k in s['books']}))

    def test_equal_initial_funds_costs_and_no_active_writes(self):
        before=self.fixture.ledger.read();self.observe();s=self.comparison.state()
        a,b=s['metrics'].values()
        self.assertEqual(a,b);self.assertLess(a['liquidation_gbp'],10000)
        self.assertEqual(len(a['positions']),5);self.assertAlmostEqual(a['cash_gbp'],5000,places=2)
        self.assertEqual(before,self.fixture.ledger.read())
        self.assertFalse(s['automatic_deployment'])

    def test_full_portfolio_replacement_vs_unchanged_baseline(self):
        self.seed();self.observe(entries=['NEW'])
        self.fixture.next_day();self.bars=self.fixture.bars;self.prices=self.fixture.prices
        self.observe('2026-10-22',entries=['NEW'])
        s=self.comparison.state()
        self.assertEqual(set(s['books']['without_replacement']['positions']),set('ABCDE'))
        self.assertEqual(set(s['books']['with_replacement']['positions']),set('BCDE')|{'NEW'})
        self.assertEqual(s['closed']['with_replacement'],1);self.assertEqual(s['closed']['without_replacement'],0)

    def test_missing_divergent_holding_freezes_both_and_counts_gap(self):
        self.observe();before=self.comparison.state()['books'];del self.prices['A']
        self.observe('2026-10-22');state=self.comparison.state()
        self.assertEqual(state['books'],before);self.assertEqual(state['misses'],1);self.assertEqual(state['coverage'],.5)

    def test_restart_and_duplicate_observation_idempotence(self):
        self.observe();before=self.comparison.state()
        self.comparison=PortfolioComparison(self.root);self.observe()
        self.assertEqual(self.comparison.state(),before)

    def test_snapshot_failure_rolls_back_both_books(self):
        before=self.comparison.state()
        with sqlite3.connect(self.comparison.path) as db:
            db.execute("CREATE TRIGGER reject_snapshot BEFORE INSERT ON snapshots BEGIN SELECT RAISE(ABORT,'failed'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.observe()
        self.assertEqual(self.comparison.state(),before)

    def test_buy_block_and_cash_aware_benchmark(self):
        self.observe(benchmark=(100,.75,'2026-10-21T15:00:00+00:00'),buy_block=True)
        self.observe('2026-10-22',benchmark=(110,.75,'2026-10-22T15:00:00+00:00'),buy_block=False)
        benchmark=self.comparison.state()['benchmark']
        self.assertEqual(benchmark['return_pct'],0) # previous interval held cash
        self.assertGreater(benchmark['weight'],.49)
        self.observe('2026-10-23',benchmark=(121,.75,'2026-10-23T15:00:00+00:00'))
        self.assertAlmostEqual(self.comparison.state()['benchmark']['return_pct'],benchmark['weight']*10)

    def test_stale_benchmark_cannot_commit_partial_comparison(self):
        before=self.comparison.state()
        with self.assertRaises(ValueError):self.observe(benchmark=(100,.75,'2026-10-20T15:00:00+00:00'))
        self.assertEqual(self.comparison.state(),before)

class FollowupScannerTests(unittest.TestCase):
    def setUp(self):
        from test_portfolio_replacement import ReplacementScannerTests
        self.fixture=ReplacementScannerTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.scanner,self.clock,self.calls=self.fixture.scanner_setup()
        self.signals=ResearchSignals(self.fixture.path.parent)
        at=(self.clock.now('UTC')-timedelta(hours=25)).isoformat()
        row=dict(id='due',symbol='Q0',module='trend_20_50',source='scanner',direction='BUY',first_seen=at,
            baseline={'at':at,'price_usd':100},outcomes={})
        with self.signals.connect() as db:db.execute('INSERT INTO signals VALUES(?,?)',('due',json.dumps(row)))

    def test_due_quote_resolves_only_one_symbol_after_holdings(self):
        self.scanner.cycle(self.fixture.ledger)
        self.assertEqual(self.scanner.status['signal_followups'],['Q0'])
        self.assertLessEqual(len(self.scanner.status['quote_requested']),6)
        quotes=[c for c in self.calls if c[0]=='quote']
        self.assertTrue(all(c[2] for c in quotes[:6]))
        self.assertEqual(sum(c[1]=='Q0' for c in quotes),1)
        self.assertFalse(next(c[2] for c in quotes if c[1]=='Q0'))
        self.assertEqual(set(self.fixture.ledger.read()['positions']),set('ABCDE'))
        rows=self.signals.state(export=True,now=self.clock.now('UTC'))['records']
        self.assertIn('24',next(r['outcomes'] for r in rows if r['id']=='due'))
        self.assertEqual(sum(r['symbol']=='Q0' for r in rows),1) # no new signal seed

    def test_followup_failure_keeps_successful_holding_check(self):
        from market import BudgetDeferred
        original=self.scanner.market.get
        def get(endpoint,**kwargs):
            if kwargs.get('symbol')=='Q0':raise BudgetDeferred('Reserved credits only')
            return original(endpoint,**kwargs)
        self.scanner.market.get=get
        self.scanner.cycle(self.fixture.ledger)
        self.assertTrue(self.scanner.status['holdings_checked_at'])
        self.assertIsNone(self.fixture.ledger.read()['error'])
        row=next(r for r in self.signals.state(export=True)['records'] if r['id']=='due')
        self.assertNotIn('24',row['outcomes'])

    def test_no_followup_between_optional_stages(self):
        self.scanner.state['optional_checked_at']=self.clock.timestamp()-300
        self.scanner.cycle(self.fixture.ledger)
        self.assertTrue(all(c[2] for c in self.calls))
        self.assertEqual(self.scanner.status['quote_requested'],[])
