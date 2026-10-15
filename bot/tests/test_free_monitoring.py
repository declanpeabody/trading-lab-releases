import tempfile,unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from engine import Ledger
from market import ApiBudget, BudgetDeferred
from ranked_scanner import RankedScanner
from test_scanner import NOW
from test_universe import LargeMarket

class FreeMonitoringTests(unittest.TestCase):
    def test_optional_work_cannot_consume_full_session_holdings_reserve(self):
        with tempfile.TemporaryDirectory() as root,patch('market.datetime') as clock:
            now=datetime(2026,10,15,8,tzinfo=ZoneInfo('America/New_York'))
            clock.now.side_effect=lambda zone:now.astimezone(zone)
            budget=ApiBudget(root)
            self.assertEqual(budget.monitoring_headroom(),498)
            for _ in range(202):budget.reserve(purpose='history')
            for purpose in ('trading','history','research','diagnostic'):
                with self.assertRaises(BudgetDeferred):ApiBudget(root).reserve(purpose=purpose)
            for _ in range(498):budget.reserve(priority=True)
            self.assertEqual(budget.used(),700)
            with self.assertRaises(ValueError):budget.reserve(priority=True)
            self.assertEqual(budget.monitoring_headroom(now.replace(hour=16)),0)

    def test_frequent_cycles_and_restart_only_fetch_holdings_until_optional_due(self):
        with tempfile.TemporaryDirectory() as root:
            ledger=Ledger(Path(root)/'paper.sqlite3',initial_cash=10000,max_positions=5,purchase_cap=1000)
            self.addCleanup(ledger.db.close);ledger.control('start')
            market=LargeMarket();scanner=RankedScanner(root,market)
            with patch('ranked_scanner.datetime') as clock:
                clock.now.return_value=NOW
                scanner.cycle(ledger);scanner.cycle(ledger)
                self.assertTrue(ledger.read()['positions'])
                stamp=scanner.state['optional_checked_at']
                market.calls=[];scanner=RankedScanner(root,market)
                with patch('time.time',return_value=stamp+300):scanner.cycle(ledger)
                self.assertTrue(scanner.status['holdings_checked_at'])
                self.assertTrue(all(call[0]=='quote' and call[2] for call in market.calls))
                self.assertEqual(len(market.calls),len(ledger.read()['positions'])+1)
                self.assertEqual(scanner.status['selected'],[])
                market.calls=[]
                with patch('time.time',return_value=stamp+1800):scanner.cycle(ledger)
                self.assertTrue(any(call[0]=='time_series' and not call[2] for call in market.calls))
                self.assertLessEqual(len(scanner.status['history_batch']),5)
