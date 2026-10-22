import json,tempfile,unittest
from pathlib import Path
from engine import Ledger

class PositionHistoryTests(unittest.TestCase):
 def test_history_is_independent_of_recent_execution_limit(self):
  with tempfile.TemporaryDirectory() as root:
   ledger=Ledger(Path(root)/'paper.sqlite3');self.addCleanup(ledger.db.close)
   self.assertEqual(ledger.accounting()['closed_positions'],[])
   ledger.control('start');bars={'AAPL':list(range(1,51))}
   ledger.cycle({'AAPL':100},bars,.75,'2026-10-15','2026-10-15T15:00:00Z',symbols=['AAPL'])
   ledger.cycle({'AAPL':20},bars,.76,'2026-10-16','2026-10-16T15:00:00Z',symbols=['AAPL'])
   before=ledger.read();a=ledger.accounting(0)
   self.assertEqual(a['executions'],[])
   self.assertEqual(len(a['closed_positions']),1)
   e=a['closed_positions'][0]
   self.assertEqual(e['symbol'],'AAPL')
   self.assertAlmostEqual(e['net_cash_gbp']-e['cost_basis_gbp'],e['realised_pnl_gbp'])
   self.assertAlmostEqual(a['recorded_realised_gbp'],e['realised_pnl_gbp'])
   self.assertEqual(ledger.read(),before)
