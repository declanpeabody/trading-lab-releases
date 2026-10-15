import tempfile,unittest
from pathlib import Path
from engine import Ledger

class CapacityTests(unittest.TestCase):
 def test_funding_is_once_and_preserves_loss_and_controls(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'paper.sqlite3';old=Ledger(path)
   state=old.read();state.update(cash=982.13,equity=982.13,day_start=995,risk_latched=True,buy_block=True);old.write(state);old.db.close()
   for _ in range(2):
    new=Ledger(path,initial_cash=10000,max_positions=20,purchase_cap=1000);state=new.read()
    self.assertAlmostEqual(state['cash'],9982.13);self.assertEqual(state['contributed_capital'],10000)
    self.assertAlmostEqual(state['equity']-state['contributed_capital'],-17.87)
    self.assertTrue(state['risk_latched']);self.assertTrue(state['buy_block']);new.db.close()
 def test_twenty_slots_and_purchase_cap(self):
  with tempfile.TemporaryDirectory() as folder:
   ledger=Ledger(Path(folder)/'paper.sqlite3',initial_cash=10000,max_positions=20,purchase_cap=1000);ledger.control('start')
   symbols=[f'S{i}' for i in range(21)];prices={s:100 for s in symbols};histories={s:list(range(1,51)) for s in symbols}
   ledger.cycle(prices,histories,1,'2026-10-15','2026-10-15T15:00:00Z',symbols=symbols,purchase_limit=200)
   self.assertEqual(len(ledger.read()['positions']),20);ledger.db.close()
  with tempfile.TemporaryDirectory() as folder:
   ledger=Ledger(Path(folder)/'paper.sqlite3',initial_cash=10000,max_positions=20,purchase_cap=1000);ledger.control('start')
   ledger.cycle({'AAPL':100},{'AAPL':list(range(1,51))},1,'2026-10-15','2026-10-15T15:00:00Z',symbols=['AAPL'],purchase_limit=1000)
   cost=ledger.read()['positions']['AAPL']['cost_gbp'];self.assertGreater(cost,999);self.assertLessEqual(cost,1000)
   with self.assertRaises(ValueError):ledger.cycle({'AAPL':100},{'AAPL':list(range(1,51))},1,'2026-10-15','x',symbols=['AAPL'],purchase_limit=1001)
   ledger.db.close()
