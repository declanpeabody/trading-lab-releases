import copy
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
from unittest.mock import patch
from engine import Ledger, new_paper_state
from research import Experiments, experiment_gate, experiment_metrics
from ranked_scanner import RankedScanner
from test_scanner import FakeMarket, NOW


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.e=Experiments(self.root);self.live=Ledger(self.root/'paper.sqlite3');self.addCleanup(self.live.db.close)
        self.live.control('start')
        self.rows=[{'datetime':(date(2026,1,1)+timedelta(days=i)).isoformat(),'close':51+i,'volume':50000} for i in range(50)]
    def observe(self,price=101,stamp='2026-03-01T15:00:00+00:00',entries=('AAPL',),bars=None):
        self.e.observe({'AAPL':price},bars or {'AAPL':self.rows},.8,stamp[:10],stamp,list(entries))
    def test_separate_books_fixed_caps_and_restart_persistence(self):
        original=self.live.read();self.observe();s=self.e.state()
        self.assertEqual(self.live.read(),original)
        self.assertEqual(s['active'],200);self.assertTrue(s['enabled']);self.assertTrue(s['automatic'])
        for size,book in s['books'].items():
            self.assertLessEqual(book['positions']['AAPL']['cost_gbp'],int(size))
            self.assertEqual(book['contributed_capital'],1000)
        self.assertEqual(Experiments(self.root).state()['books'],s['books'])
        self.assertLess(s['books']['100']['positions']['AAPL']['qty'],s['books']['200']['positions']['AAPL']['qty'])
    def test_invalid_size_never_changes_live_account(self):
        before=self.live.read()
        for size in [0,250,float('inf'),'100',True]:
            with self.assertRaises(ValueError):self.live.cycle({'AAPL':101},{'AAPL':list(range(51,101))},.8,'2026-03-01','2026-03-01T15:00:00Z',symbols=['AAPL'],purchase_limit=size)
        self.assertEqual(self.live.read(),before)
    def test_duplicate_and_frequent_observations_do_not_count(self):
        self.observe();before=self.e.state();self.observe();self.observe(stamp='2026-03-01T15:05:00+00:00')
        self.assertEqual(self.e.state()['books'],before['books']);self.assertEqual(self.e.state()['day']['checks'],1)
    def test_missing_quotes_freeze_every_comparison_account(self):
        self.observe();before=self.e.state()['books']
        self.e.observe({}, {'AAPL':self.rows},.8,'2026-03-01','2026-03-01T16:00:00+00:00',['AAPL'])
        self.assertEqual(self.e.state()['books'],before);self.assertEqual(self.e.state()['attempted_misses'],1)
    def test_bad_snapshot_rolls_back_whole_cohort(self):
        self.observe();before=self.e.state()['books']
        with self.assertRaises(ValueError):self.observe(price=float('nan'),stamp='2026-03-01T16:00:00+00:00')
        self.assertEqual(self.e.state()['books'],before)
    def test_pause_and_buy_block_are_respected(self):
        self.e.control('enabled',False);self.observe();self.assertFalse(self.e.state()['books'])
        self.e.control('enabled',True)
        self.e.observe({'AAPL':101},{'AAPL':self.rows},.8,'2026-03-01','2026-03-01T15:00:00Z',['AAPL'],buy_block=True)
        self.assertTrue(all(not b['positions'] for b in self.e.state()['books'].values()))
    def test_split_stops_evaluation_without_touching_live_account(self):
        self.observe();before=self.live.read();rows=copy.deepcopy(self.rows)
        for row in rows:row['close']/=2
        self.observe(price=50.5,bars={'AAPL':rows},stamp='2026-03-02T15:00:00+00:00')
        s=self.e.state();self.assertTrue(s['restart_required']);self.assertIsNone(s['pending'])
        self.assertEqual(self.live.read(),before)
    def test_running_intraday_peak_is_not_lost(self):
        self.observe();self.observe(price=120,stamp='2026-03-01T16:00:00+00:00');self.observe(price=110,stamp='2026-03-01T17:00:00+00:00')
        s=self.e.state();b=s['books']['200'];m=s['metrics']['200']
        self.assertGreater(b['experiment_peak'],1000);self.assertGreater(m['drawdown_pct'],1)
    def seed_phase(self,phase,first,cash,misses=0):
        def seed(s):
            if not s['books']:self.e._new_round(s)
            s['phase']=phase;s['sessions']=[];s['attempted_misses']=misses;s['day']=None
            for i in range(29):s['sessions'].append({'date':(first+timedelta(days=i)).isoformat(),'checks':3,'missed':0,'first':0,'last':3600,'qualified':True})
            s['day']={'date':(first+timedelta(days=29)).isoformat(),'checks':3,'missed':0,'first':0,'last':3600}
            for key,value in cash.items():
                s['books'][key]=new_paper_state()|{'paused':False,'cash':value,'equity':value}
                s['curves'][key]=[1000,value];s['closes'][key]=10
            self.e._finish_day(s)
        self.e._change(seed)
    def test_development_choice_is_frozen_and_validation_cannot_pick_new_winner(self):
        self.seed_phase('development',date(2026,1,1),{'100':1140,'150':1120,'200':1100})
        s=self.e.state();self.assertEqual(s['phase'],'validation');self.assertEqual(s['candidate'],100)
        self.assertTrue(all(b['cash']==1000 for b in s['books'].values()))
        self.seed_phase('validation',date(2026,3,1),{'100':1080,'150':1250,'200':1100})
        s=self.e.state();self.assertIsNone(s['pending']);self.assertEqual(s['candidate'],100);self.assertEqual(s['active'],200)
    def test_incomplete_observation_coverage_blocks_selection(self):
        self.seed_phase('development',date(2026,1,1),{'100':1140,'150':1120,'200':1100},misses=6)
        s=self.e.state();self.assertEqual(s['phase'],'complete');self.assertIsNone(s['pending'])
        self.assertEqual(s['reports'][-1]['eligible'],[])

    def approved(self):
        self.seed_phase('development',date(2026,1,1),{'100':1140,'150':1120,'200':1100})
        self.seed_phase('validation',date(2026,3,1),{'100':1140,'150':1120,'200':1100})
        self.assertEqual(self.e.state()['pending'],100)
    def test_promotion_changes_only_purchase_profile_and_rollback_is_available(self):
        self.approved();before=self.live.read();self.e.promote(self.live,'2026-03-31')
        self.assertEqual(self.e.state()['active'],100);self.assertEqual(self.live.read(),before)
        self.e.control('rollback');s=self.e.state();self.assertEqual(s['active'],200);self.assertFalse(s['automatic'])
        self.assertEqual(self.live.read(),before)
    def test_promotion_cannot_bypass_pause_risk_or_holdings(self):
        self.approved()
        for field,value in [('paused',True),('buy_block',True),('risk_latched',True),('day_block',True),('error','bad data'),('positions',{'AAPL':{'qty':1}})]:
            before=self.live.read()
            with self.live.lock:
                modified=copy.deepcopy(before);modified[field]=value;self.live.write(modified)
            self.e.promote(self.live,'2026-03-31');self.assertEqual(self.e.state()['active'],200)
            with self.live.lock:self.live.write(before)
        self.e.control('automatic',False);self.e.promote(self.live,'2026-03-31');self.assertEqual(self.e.state()['active'],200)
    def test_expired_or_inconsistent_evidence_never_promotes(self):
        self.approved();self.e.promote(self.live,'2026-04-10');self.assertEqual(self.e.state()['active'],200);self.assertIsNone(self.e.state()['pending'])
        self.e._change(lambda s:s.update(pending=150))
        self.e.promote(self.live,'2026-03-31');self.assertEqual(self.e.state()['active'],200);self.assertTrue(self.e.state()['restart_required'])
    def test_net_return_drawdown_trade_count_and_coverage_gates(self):
        baseline={'return_pct':2,'drawdown_pct':3,'closed_trades':10}
        winner={'return_pct':4,'drawdown_pct':2,'closed_trades':10}
        self.assertTrue(experiment_gate(winner,baseline))
        for change in ({'return_pct':2.5},{'drawdown_pct':4},{'closed_trades':9}):self.assertFalse(experiment_gate(winner|change,baseline))
        self.observe()
        self.e.miss('2026-03-02');self.assertEqual(self.e.state()['attempted_misses'],1)


class ScannerExperimentTests(unittest.TestCase):
    def test_shared_budgeted_quote_slots_and_live_pause(self):
        with tempfile.TemporaryDirectory() as root:
            ledger=Ledger(Path(root)/'paper.sqlite3');ledger.control('start');experiment=Experiments(root);market=FakeMarket()
            scanner=RankedScanner(root,market,experiments=experiment)
            with patch('ranked_scanner.datetime') as clock:
                clock.now.return_value=NOW
                scanner.cycle(ledger);market.calls=[];scanner.cycle(ledger)
                self.assertTrue(experiment.state()['books'])
                self.assertLessEqual(sum(c[0]=='quote' and c[1]!='USD/GBP' for c in market.calls),6)
                before=experiment.state()['books'];ledger.control('pause');scanner.cycle(ledger)
                self.assertEqual(experiment.state()['books'],before)
            ledger.db.close()

    def test_promoted_cap_used_by_running_scanner_and_divergence_is_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            ledger=Ledger(Path(root)/'paper.sqlite3');ledger.control('start');experiment=Experiments(root);market=FakeMarket()
            experiment._change(lambda s:s.update(active=100))
            scanner=RankedScanner(root,market,experiments=experiment)
            with patch('ranked_scanner.datetime') as clock:
                clock.now.return_value=NOW
                scanner.cycle(ledger);scanner.cycle(ledger)
                live=ledger.read();self.assertTrue(live['positions'])
                self.assertTrue(all(p['cost_gbp']<=100 for p in live['positions'].values()))
                extras=[s for s in scanner.status['universe_symbols'] if s not in live['positions']][:7]
                def diverge(s):
                    for book in s['books'].values():book['positions']={}
                    for i,symbol in enumerate(extras):
                        s['books'][str((100,150,200)[i%3])]['positions'][symbol]={'qty':1,'cost_gbp':100,'entry_usd':200,'opened':'2026-10-09T14:00:00Z'}
                experiment._change(diverge);market.calls=[];scanner.cycle(ledger)
                self.assertTrue(experiment.state()['restart_required'])
                self.assertEqual(experiment.state()['phase'],'complete')
                self.assertLessEqual(sum(c[0]=='quote' and c[1]!='USD/GBP' for c in market.calls),len(live['positions'])+6)
            ledger.db.close()

    def test_observer_quote_below_one_dollar_cannot_enter_active_buy_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            ledger=Ledger(Path(root)/'paper.sqlite3');ledger.control('start');experiment=Experiments(root);market=FakeMarket()
            scanner=RankedScanner(root,market,experiments=experiment)
            with patch('ranked_scanner.datetime') as clock:
                clock.now.return_value=NOW;scanner.cycle(ledger)
                original=market.get
                def quote(endpoint,**params):
                    result=original(endpoint,**params)
                    if endpoint=='quote' and params.get('symbol')=='S00':result['close']=.5
                    return result
                with patch.object(experiment,'holdings',return_value=['S00']),patch.object(market,'get',side_effect=quote),patch.object(ledger,'cycle',wraps=ledger.cycle) as cycle:
                    scanner.cycle(ledger)
                    self.assertTrue(cycle.called)
                    self.assertNotIn('S00',cycle.call_args.args[0])
            ledger.db.close()
