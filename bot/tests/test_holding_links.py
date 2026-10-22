import json,tempfile,unittest
from pathlib import Path
from app import holding_exchanges

class HoldingLinksTests(unittest.TestCase):
 def test_saved_catalogue_only_and_unknown_exchange(self):
  with tempfile.TemporaryDirectory() as root:
   self.assertEqual(holding_exchanges(root,['CRM']),{})
   p=Path(root)/'scanner.json'
   p.write_text(json.dumps({'known':{'CRM':'NASDAQ','AAPL':'NASDAQ','BAD':'other'},'universe':{'CRM':'NYSE','MSFT':'NASDAQ'}}))
   self.assertEqual(holding_exchanges(root,['CRM','AAPL','BAD','UNKNOWN']),{'CRM':'NYSE','AAPL':'NASDAQ'})
   p.write_text('null');self.assertEqual(holding_exchanges(root,['CRM']),{})
   p.write_text('{');self.assertEqual(holding_exchanges(root,['CRM']),{})
