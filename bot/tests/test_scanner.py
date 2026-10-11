import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from engine import Ledger
from market import ApiBudget, session_window
from scanner import Scanner, catalogue, select_batch, liquid_history

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=ZoneInfo('America/New_York'))

def listing(s, **changes):
    return dict(symbol=s, exchange='NASDAQ', currency='USD',
                type='Common Stock', country='United States') | changes

class FakeMarket:
    def __init__(self):
        self.calls = []
        self.bad = set()
        self.extended = set()
        self.reject_candidates = False

    def get(self, endpoint, priority=False, **params):
        s = params.get('symbol')
        self.calls.append((endpoint, s, priority))
        if self.reject_candidates and not priority:
            raise ValueError('Non-priority quota exhausted')
        if s in self.bad: raise ValueError('Unavailable fixture')
        if endpoint == 'stocks':
            return {'data': [listing(f'S{i:02}') for i in range(18)]}
        if endpoint == 'time_series':
            return {'meta': {'symbol': s, 'currency': 'USD', 'exchange': 'NASDAQ'},
                    'values': [{'datetime': (NOW-timedelta(days=60-i)).date().isoformat(),
                                'close': 100+i, 'volume': 20000} for i in range(60)]}
        return dict(symbol=s, currency='USD', exchange='NASDAQ', close=.75 if s=='USD/GBP' else 200,
                    timestamp=time.time(), is_market_open=True, is_extended_hours=s in self.extended)

class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = Ledger(self.root/'paper.sqlite3')
        self.ledger.control('start')
        self.market = FakeMarket()
        self.scanner = Scanner(self.root, self.market)

    def tearDown(self):
        self.ledger.db.close()
        self.tmp.cleanup()

    def run_scan(self, now=NOW):
        with patch('scanner.datetime') as clock:
            clock.now.return_value = now
            self.scanner.cycle(self.ledger)
            self.scanner.record_status(self.ledger)

    def test_universe_filters_and_symbol_collisions(self):
        rows = [listing('A'), listing('B', exchange='NYSE'), listing('A',exchange='NYSE'),
                listing('C',currency='GBP'),listing('D',type='ETF'),listing('E',exchange='OTC'),
                listing('F',country='Canada')]
        self.assertEqual(catalogue(rows), {'B':'NYSE'})

    def test_batch_skips_held_wraps_and_is_bounded(self):
        u = {str(i):'NASDAQ' for i in range(10)}
        picks, cursor = select_batch(u, ['8','9','0'], 8)
        self.assertEqual(picks, ['1','2','3','4','5','6'])
        self.assertEqual(cursor, 7)

    def test_scan_opens_at_most_five_and_preserves_budget(self):
        self.run_scan()
        s = self.ledger.read()
        self.assertEqual(len(s['positions']), 5)
        self.assertGreaterEqual(s['cash'], 0)
        self.assertAlmostEqual(s['cash']+sum(p['cost_gbp'] for p in s['positions'].values()),1000)
        self.assertEqual(s['scanner']['universe_count'], 18)
        self.assertEqual(len(s['scanner']['checked']), 6)

    def test_rotation_and_holdings_survive_restart(self):
        self.run_scan()
        before = self.ledger.read()['positions']
        self.scanner = Scanner(self.root, self.market)
        self.run_scan()
        self.assertEqual(self.ledger.read()['positions'], before)
        self.assertEqual(self.scanner.status['selected'], [f'S{i:02}' for i in range(6,12)])
        self.assertTrue(set(before).issubset(self.scanner.status['checked']))
        quotes = [s for endpoint,s,p in self.market.calls if endpoint == 'quote']
        self.assertIn('USD/GBP', quotes)

    def test_failed_candidate_does_not_block_other_candidates(self):
        self.market.bad.add('S00')
        self.run_scan()
        self.assertNotIn('S00', self.ledger.read()['positions'])
        self.assertTrue(self.ledger.read()['positions'])
        self.assertIn('S00', self.scanner.status['skipped'][0])

    def test_bad_held_quote_makes_no_partial_trades(self):
        self.run_scan()
        before = self.ledger.read()
        self.market.bad.add('S00')
        with self.assertRaisesRegex(ValueError,'Held stock'):
            self.run_scan()
        self.assertEqual(self.ledger.read(), before)

    def test_candidate_budget_exhaustion_still_checks_holdings(self):
        self.run_scan()
        before = self.ledger.read()['positions']
        self.market.reject_candidates = True
        self.run_scan()
        self.assertEqual(set(self.scanner.status['checked']),set(before))
        self.assertEqual(self.ledger.read()['positions'],before)

    def test_extended_hours_quote_is_never_used(self):
        self.market.extended.add('S00')
        self.run_scan()
        self.assertNotIn('S00',self.ledger.read()['positions'])

    def test_outside_regular_hours_makes_no_requests(self):
        self.run_scan(NOW.replace(hour=8))
        self.assertEqual(self.market.calls, [])
        self.assertEqual(self.ledger.read()['positions'], {})

    def test_pause_during_download_prevents_orders(self):
        original = self.market.get
        def get(endpoint, **kwargs):
            self.ledger.control('pause')
            return original(endpoint, **kwargs)
        self.market.get = get
        self.run_scan()
        self.assertEqual(self.ledger.read()['positions'], {})

    def test_low_or_missing_volume_is_ineligible(self):
        data = self.market.get('time_series',symbol='S00')
        self.assertTrue(liquid_history(data,NOW.date().isoformat()))
        for r in data['values']: r['volume'] = 1
        self.assertFalse(liquid_history(data,NOW.date().isoformat()))
        for r in data['values']: r.pop('volume')
        self.assertFalse(liquid_history(data,NOW.date().isoformat()))

    def test_dynamic_snapshot_requires_all_holdings(self):
        self.run_scan()
        before = self.ledger.read()
        with self.assertRaisesRegex(ValueError, 'Missing held-stock'):
            self.ledger.cycle({'NEW':200},{'NEW':list(range(100,150))},.75,
                              '2026-10-09','2026-10-09T15:00:00Z',symbols=['NEW'])
        self.assertEqual(self.ledger.read(),before)

    def test_regular_session_boundaries(self):
        self.assertTrue(session_window(NOW.replace(hour=9,minute=30)))
        self.assertFalse(session_window(NOW.replace(hour=9,minute=29)))
        self.assertTrue(session_window(NOW.replace(hour=15,minute=59)))
        self.assertFalse(session_window(NOW.replace(hour=16,minute=0)))

class BudgetTests(unittest.TestCase):
    def test_limits_priority_reserve_and_restart(self):
        with tempfile.TemporaryDirectory() as root:
            b = ApiBudget(root)
            for _ in range(500): b.reserve()
            with self.assertRaises(ValueError): ApiBudget(root).reserve()
            for _ in range(200): ApiBudget(root).reserve(priority=True)
            self.assertEqual(ApiBudget(root).used(),700)
            with self.assertRaises(ValueError): b.reserve(priority=True)

    def test_new_utc_date_resets_count(self):
        with tempfile.TemporaryDirectory() as root:
            b = ApiBudget(root)
            with patch('market.datetime') as clock:
                clock.now.return_value = NOW
                b.reserve()
                self.assertEqual(b.used(),1)
                clock.now.return_value = NOW + timedelta(days=1)
                self.assertEqual(b.used(),0)
                b.reserve()
                self.assertEqual(b.used(),1)
