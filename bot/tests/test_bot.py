import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from engine import Ledger, SYMBOLS, quote, history, signal
from market import session_window


class TradingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'ledger.db'
        self.ledger = Ledger(self.path, initial_cash=1000)
        self.prices = {s:170 for s in SYMBOLS}
        self.histories = {s:list(range(100,150)) for s in SYMBOLS}

    def tearDown(self):
        self.ledger.db.close()
        self.tmp.cleanup()

    def tick(self, day='2026-10-02', prices=None):
        self.ledger.cycle(prices or self.prices,self.histories,.75,day,day+'T15:00:00Z')

    def test_starts_paused_and_never_buys(self):
        self.tick()
        self.assertEqual(self.ledger.read()['cash'],1000)
        self.assertEqual(self.ledger.read()['positions'],{})

    def test_caps_cash_and_positions_and_charges_costs(self):
        self.ledger.control('start')
        self.tick()
        s = self.ledger.read()
        self.assertEqual(len(s['positions']),3)
        self.assertGreaterEqual(s['cash'],400)
        for p in s['positions'].values(): self.assertLessEqual(p['cost_gbp'],200)
        self.assertLess(s['equity'],1000)
        self.assertAlmostEqual(s['cash']+sum(p['cost_gbp'] for p in s['positions'].values()),1000)

    def test_repeated_checks_do_not_duplicate_positions(self):
        self.ledger.control('start')
        self.tick()
        before = self.ledger.read()
        self.tick()
        after = self.ledger.read()
        self.assertEqual(before['cash'],after['cash'])
        self.assertEqual(before['positions'],after['positions'])

    def test_restart_retains_pause_and_holdings(self):
        self.ledger.control('start')
        self.tick()
        self.ledger.control('pause')
        self.ledger.db.close()
        self.ledger = Ledger(self.path, initial_cash=1000)
        self.assertTrue(self.ledger.read()['paused'])
        self.assertEqual(len(self.ledger.read()['positions']),3)

    def test_block_buys_allows_exits(self):
        self.ledger.control('start')
        self.tick()
        self.ledger.control('block')
        self.tick(prices={s:100 for s in SYMBOLS})
        self.assertFalse(self.ledger.read()['positions'])
        self.assertTrue(self.ledger.read()['risk_latched'])

    def test_manual_allow_cannot_override_drawdown_lock(self):
        self.ledger.control('start')
        self.tick()
        self.tick(prices={s:100 for s in SYMBOLS})
        self.ledger.control('allow')
        self.tick(day='2026-10-05')
        self.assertEqual(self.ledger.read()['positions'],{})
        self.assertTrue(self.ledger.read()['risk_latched'])

    def test_pausing_during_data_fetch_prevents_trade(self):
        self.ledger.control('start')
        self.ledger.control('pause')
        self.tick()
        self.assertFalse(self.ledger.read()['positions'])

    def test_incomplete_snapshot_has_no_partial_trade(self):
        self.ledger.control('start')
        before=self.ledger.read()
        with self.assertRaises(KeyError): self.tick(prices={'AAPL':170})
        self.assertEqual(self.ledger.read(),before)

    def test_bad_data_has_no_partial_trade(self):
        self.ledger.control('start')
        before=self.ledger.read()
        with self.assertRaises(ValueError): self.tick(prices={s:math.nan for s in SYMBOLS})
        self.assertEqual(self.ledger.read(),before)

    def test_exit_cannot_reenter_same_day(self):
        self.ledger.control('start')
        self.tick()
        self.tick(prices={s:160 for s in SYMBOLS})
        self.assertEqual(self.ledger.read()['positions'],{})
        self.tick()
        self.assertEqual(self.ledger.read()['positions'],{})


class ValidationTests(unittest.TestCase):
    def test_quote_freshness_currency_symbol_and_closed_market(self):
        q=dict(symbol='AAPL',currency='USD',close='100',last_quote_at=1000,is_market_open=True)
        self.assertEqual(quote(q,'AAPL',1050,'USD'),100)
        for changes in [dict(last_quote_at=500),dict(last_quote_at=1500),dict(currency='GBP'),
                        dict(symbol='MSFT'),dict(is_market_open=False),dict(close='NaN')]:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError): quote(q|changes,'AAPL',1050,'USD')

    def test_daily_history_excludes_current_candle(self):
        today=datetime(2026,10,2)
        rows=[dict(datetime=(today-timedelta(days=d)).date().isoformat(),close=str(100+d)) for d in range(60)]
        rows[0]['close']='9999999'
        data=dict(meta=dict(symbol='AAPL',currency='USD'),values=rows)
        values=history(data,'AAPL','2026-10-02')
        self.assertEqual(len(values),50)
        self.assertEqual(values[-1],101)

    def test_duplicate_candles_rejected(self):
        row=dict(datetime='2026-10-01',close='100')
        data=dict(meta=dict(symbol='AAPL',currency='USD'),values=[row,row])
        with self.assertRaises(ValueError): history(data,'AAPL','2026-10-02')

    def test_signal_and_session_window(self):
        self.assertEqual(signal(list(range(100,150)),170),'buy')
        self.assertEqual(signal(list(range(100,150)),100),'sell')
        self.assertTrue(session_window(datetime(2026,10,2,10,0)))
        self.assertFalse(session_window(datetime(2026,10,3,10,0)))
        self.assertFalse(session_window(datetime(2026,10,2,16,0)))


if __name__ == '__main__': unittest.main()
