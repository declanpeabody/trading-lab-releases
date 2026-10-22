import copy,tempfile,unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
from engine import Ledger
from strategy import replacement_review

class PortfolioReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'paper.sqlite3'
        self.ledger=Ledger(self.path,initial_cash=10000,purchase_cap=1000)
        self.addCleanup(lambda:self.ledger.db.close())
        dates=[];d=date(2026,10,20)
        while len(dates)<50:
            if d.weekday()<5:dates.append(d.isoformat())
            d-=timedelta(days=1)
        dates.reverse()
        self.bars={s:[dict(datetime=d,close=100+i*slope,volume=1000000) for i,d in enumerate(dates)]
                   for s,slope in [('A',.1),('B',.2),('C',.3),('D',.4),('E',.5),('NEW',2)]}
        self.prices={s:r[-1]['close']*1.01 for s,r in self.bars.items()}
        s=self.ledger.read();s['paused']=False
        s['positions']={k:dict(qty=1,entry_usd=self.prices[k],cost_gbp=self.prices[k]*.75,
            opened='2026-10-01T15:00:00+00:00',basis_reference={'day':dates[-1],'close':self.bars[k][-1]['close']}) for k in 'ABCDE'}
        s['cash']=10000-sum(p['cost_gbp'] for p in s['positions'].values());self.ledger.write(s)
    def cycle(self,day='2026-10-21'):
        return self.ledger.cycle(self.prices,{s:[r['close'] for r in rows] for s,rows in self.bars.items()},
            .75,day,day+'T15:00:00+00:00',symbols=list(self.prices),history_rows=self.bars,purchase_limit=1000,
            replacement_candidates=['NEW'],replacement_coverage=45)
    def next_day(self):
        for s,rows in self.bars.items():
            self.bars[s]=rows[1:]+[dict(datetime='2026-10-21',close=rows[-1]['close']*1.01,volume=1000000)]
            self.prices[s]=self.bars[s][-1]['close']*1.01
    def ready(self):self.cycle();self.next_day()
    def test_two_sessions_atomic_costs_and_no_idle_cash_spend(self):
        before=self.ledger.read()['cash'];self.cycle()
        self.assertEqual(self.ledger.accounting()['recorded_executions'],0)
        self.assertIn('second session',self.ledger.read()['replacement_review']['reason'])
        self.next_day();actions=self.cycle('2026-10-22');s=self.ledger.read()
        self.assertEqual(set(s['positions']),{'B','C','D','E','NEW'})
        self.assertGreaterEqual(s['cash'],before-1e-8)
        fills=self.ledger.accounting()['executions'];self.assertEqual(len(fills),2)
        self.assertEqual({e['side'] for e in fills},{'BUY','SELL'})
        self.assertEqual(len({e['replacement_id'] for e in fills}),1)
        self.assertTrue(all(e['price_cost_gbp']>0 and e['fx_cost_gbp']>0 for e in fills))
        self.assertTrue(s['replacement_review']['executed'])
        self.assertEqual([a['symbol'] for a in actions if a['action']=='SELL'],['A'])
        self.assertAlmostEqual(s['equity'],s['cash']+sum(p['qty']*self.prices[k]*.75 for k,p in s['positions'].items()))
        self.assertEqual(s['contributed_capital'],10000)
    def test_intraday_repetition_cannot_confirm(self):
        for _ in range(5):self.cycle()
        self.assertEqual(self.ledger.accounting()['recorded_executions'],0)
    def test_confirmation_survives_restart(self):
        self.ready();self.ledger.db.close();self.ledger=Ledger(self.path,initial_cash=10000,purchase_cap=1000)
        self.cycle('2026-10-22');self.assertIn('NEW',self.ledger.read()['positions'])
    def test_controls_block_replacement(self):
        self.ready();original=self.ledger.read()
        for flag in ('paused','buy_block','risk_latched','day_block'):
            with self.subTest(flag=flag):
                s=copy.deepcopy(original);s[flag]=True;s['day']='2026-10-22';s['day_start']=10000
                self.ledger.write(s);self.cycle('2026-10-22')
                self.assertEqual(set(self.ledger.read()['positions']),set('ABCDE'))
                self.assertEqual(self.ledger.accounting()['recorded_executions'],0)
    def test_normal_stop_exit_wins(self):
        self.ready();self.prices['A']*=.9;actions=self.cycle('2026-10-22')
        self.assertEqual(next(a['reason'] for a in actions if a['symbol']=='A'),'Observed price loss exit')
        self.assertFalse(self.ledger.read()['replacement_review'].get('executed',False))
    def test_missing_price_rejects_before_sale(self):
        self.ready();del self.prices['B'];before=self.ledger.read()
        with self.assertRaises(ValueError):self.cycle('2026-10-22')
        self.assertEqual(self.ledger.read(),before)
    def test_missing_candidate_keeps_holdings(self):
        self.ready();del self.prices['NEW'];self.cycle('2026-10-22')
        self.assertEqual(set(self.ledger.read()['positions']),set('ABCDE'))
    def test_minimum_purchase_keeps_holdings(self):
        self.ready();self.ledger.research_caps={'NEW':.5};self.cycle('2026-10-22')
        self.assertEqual(self.ledger.accounting()['recorded_executions'],0)
    def test_persistence_failure_has_no_partial_fill(self):
        self.ready();before=self.ledger.read()
        with patch.object(self.ledger,'write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.cycle('2026-10-22')
        self.assertEqual(self.ledger.read(),before);self.assertEqual(self.ledger.accounting()['recorded_executions'],0)
    def test_sqlite_failure_rolls_back_both_execution_rows(self):
        self.ready();before=self.ledger.read()
        self.ledger.db.execute("CREATE TRIGGER reject_buy BEFORE INSERT ON executions WHEN json_extract(NEW.body,'$.side')='BUY' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):self.cycle('2026-10-22')
        self.assertEqual(self.ledger.read(),before)
        self.assertEqual(self.ledger.accounting()['recorded_executions'],0)
    def test_replaced_stock_cooldown_applies_to_normal_entries(self):
        s=self.ledger.read();del s['positions']['A'];s['replacement_exits']={'NEW':'2026-10-20'}
        self.ledger.write(s)
        self.ledger.cycle(self.prices,{k:[r['close'] for r in rows] for k,rows in self.bars.items()},
            .75,'2026-10-21','2026-10-21T15:00:00Z',symbols=list('BCDE')+['NEW'],history_rows=self.bars,purchase_limit=1000)
        self.assertNotIn('NEW',self.ledger.read()['positions'])
    def review(self,**kwargs):
        args=dict(state=self.ledger.read(),bars=self.bars,prices=self.prices,signals={s:'buy' for s in self.bars},
            day='2026-10-21',candidates=['NEW'],coverage=45,max_positions=5,price_cost=.0025,fx_cost=.0015)
        args.update(kwargs);return replacement_review(**args)
    def test_low_coverage_and_mismatched_or_future_history(self):
        self.assertIn('30',self.review(coverage=29)['reason']);self.bars['NEW'][-1]['datetime']='2026-10-21'
        self.assertIn('session dates',self.review()['reason'])
    def test_low_advantage_does_not_qualify(self):
        self.bars['NEW']=copy.deepcopy(self.bars['A']);self.assertIn('Advantage below',self.review()['reason'])
    def test_cost_buffer_scales_with_assumptions(self):
        r=self.review(price_cost=.02,fx_cost=.02);self.assertGreater(r['required_momentum_gap_pct'],15)
    def test_minimum_hold_and_cooldowns(self):
        s=self.ledger.read();s['positions']['A']['opened']='2026-10-20T15:00:00Z'
        self.assertIn('fewer than three',self.review(state=s)['reason'])
        s=self.ledger.read();s['last_replacement_day']='2026-10-20';self.assertIn('cooldown',self.review(state=s)['reason'])
        s=self.ledger.read();s['replacement_exits']={'NEW':'2026-10-20'};self.assertIn('re-entry cooldown',self.review(state=s)['reason'])
    def test_changed_pair_or_skipped_session_restarts_confirmation(self):
        self.ready();s=self.ledger.read();s['replacement_review']['incoming']='OTHER';self.ledger.write(s)
        self.cycle('2026-10-22');self.assertEqual(self.ledger.accounting()['recorded_executions'],0)
        s=self.ledger.read();s['replacement_review']['asof']='2026-10-15';self.ledger.write(s)
        self.cycle('2026-10-22');self.assertEqual(self.ledger.accounting()['recorded_executions'],0)

class ReplacementScannerTests(unittest.TestCase):
    setUp=PortfolioReplacementTests.setUp
    cycle=PortfolioReplacementTests.cycle
    next_day=PortfolioReplacementTests.next_day
    ready=PortfolioReplacementTests.ready
    # Exercise the real scanner/ledger path with a deterministic provider and clock.
    def scanner_setup(self,fail_candidate=False,stale_candidate=False):
        from ranked_scanner import RankedScanner
        from strategy import LIVE_VERSION
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from market import MarketUnavailable
        test=self
        class Clock:
            day='2026-10-21'
            def now(self,zone):return datetime.fromisoformat(self.day+'T15:00:00+00:00').astimezone(ZoneInfo(zone))
            def timestamp(self):return self.now('UTC').timestamp()
        clock=Clock();calls=[]
        class Provider:
            def get(self,endpoint,priority=False,**params):
                symbol=params.get('symbol');calls.append((endpoint,symbol,priority))
                if endpoint!='quote':raise AssertionError('Unexpected optional history request')
                if symbol=='NEW' and fail_candidate:raise MarketUnavailable('test candidate unavailable')
                return dict(symbol=symbol,currency='USD',exchange=params.get('exchange','NASDAQ'),
                    close=.75 if symbol=='USD/GBP' else test.prices.get(symbol,500),
                    timestamp=clock.timestamp()-(180 if symbol=='NEW' and stale_candidate else 0),is_market_open=True)
        scanner=RankedScanner(self.temp.name,Provider(),clock=clock)
        for i in range(30):self.bars['Q'+str(i)]=copy.deepcopy(self.bars['A'])
        mapping={s:'NASDAQ' for s in self.bars}
        scanner.state.update(universe=mapping,known=mapping,fetched=clock.timestamp(),catalogue_filter=2)
        scanner.daily=dict(day=clock.day,version=LIVE_VERSION,bars=copy.deepcopy(self.bars),attempted=[],errors={})
        return scanner,clock,calls
    def test_scanner_replaces_after_two_sessions_with_bounded_optional_quotes(self):
        scanner,clock,calls=self.scanner_setup();scanner.cycle(self.ledger)
        self.assertEqual(self.ledger.accounting()['recorded_executions'],0)
        self.assertTrue(scanner.status['replacement_check_due'])
        self.assertEqual(scanner.status['quote_requested'],['NEW'])
        self.next_day();clock.day='2026-10-22'
        scanner.daily.update(day=clock.day,bars=copy.deepcopy(self.bars));calls.clear()
        scanner.cycle(self.ledger)
        self.assertIn('NEW',self.ledger.read()['positions'])
        self.assertEqual(self.ledger.accounting()['recorded_executions'],2)
        self.assertLessEqual(len(calls),8)
        self.assertEqual(len([c for c in calls if c[2]]),6)
    def test_failed_optional_candidate_cannot_sell_holding(self):
        self.ready();scanner,clock,calls=self.scanner_setup(fail_candidate=True)
        clock.day='2026-10-22';scanner.daily.update(day=clock.day)
        scanner.cycle(self.ledger)
        self.assertEqual(set(self.ledger.read()['positions']),set('ABCDE'))
        self.assertIsNone(self.ledger.read()['error'])
        self.assertTrue(scanner.status['holdings_checked_at'])
        self.assertEqual(self.ledger.accounting()['recorded_executions'],0)
    def test_stale_candidate_cannot_trigger_replacement(self):
        self.ready();scanner,clock,calls=self.scanner_setup(stale_candidate=True)
        clock.day='2026-10-22';scanner.daily.update(day=clock.day)
        scanner.cycle(self.ledger)
        self.assertEqual(set(self.ledger.read()['positions']),set('ABCDE'))
        self.assertEqual(self.ledger.accounting()['recorded_executions'],0)
