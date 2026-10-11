import tempfile
import unittest
from pathlib import Path
from engine import Ledger

class FundingTests(unittest.TestCase):
    def test_existing_account_topup_is_once_and_preserves_profit_and_controls(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'paper.sqlite3'
            old=Ledger(path,initial_cash=100)
            s=old.read();s.pop('contributed_capital')
            position={'qty':.2,'cost_gbp':20,'entry_usd':100,'opened':'2026-10-09'}
            s.update(cash=75,equity=95,day_start=100,positions={'AAPL':position},
                     risk_latched=True,day_block=True,paused=False,buy_block=True)
            old.write(s,['Previous trade event']);old.db.close()
            upgraded=Ledger(path);s=upgraded.read()
            self.assertEqual(s['cash'],975)
            self.assertEqual(s['equity'],995)
            self.assertEqual(s['contributed_capital'],1000)
            self.assertEqual(s['equity']-s['contributed_capital'],-5)
            self.assertEqual(s['day_start'],1000)
            self.assertEqual(s['positions'],{'AAPL':position})
            self.assertTrue(s['risk_latched'] and s['day_block'] and s['buy_block'])
            self.assertFalse(s['paused'])
            self.assertEqual(len(s['events']),2)
            upgraded.db.close()
            again=Ledger(path);self.assertEqual(again.read(),s);again.db.close()

    def test_new_account_has_exactly_1000(self):
        with tempfile.TemporaryDirectory() as d:
            db=Ledger(Path(d)/'new.db')
            self.assertEqual(db.read()['cash'],1000)
            self.assertEqual(db.read()['contributed_capital'],1000)
            db.db.close()
