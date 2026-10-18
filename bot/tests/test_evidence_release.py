import tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from research import SessionScorecard,EventCalendar,ShadowTrials
from engine import Ledger
from test_universe import LargeMarket

class EvidenceReleaseTests(unittest.TestCase):
 def test_session_success_failure_interruption_and_gaps(self):
  with tempfile.TemporaryDirectory() as root:
   c=SessionScorecard(root);before={'last_success':None}
   for minute,success,error in ((0,True,None),(10,False,'Held stock MSFT: invalid quote'),(20,True,None)):
    stamp=f'2026-10-16T14:{minute:02}:00+00:00';ident=c.begin(stamp)
    after={'last_success':stamp if success else before['last_success'],'error':error};c.finish(ident,stamp,before,after);before=after
   c.begin('2026-10-16T14:30:00+00:00');s=SessionScorecard(root).state()['sessions'][0]
   self.assertEqual((s['attempts'],s['successful'],s['unfinished'],s['quote_failures']),(4,2,1,1))
   self.assertEqual(s['longest_success_gap_minutes'],20);self.assertEqual(s['success_pct'],50)
 def test_calendar_validation_and_no_lookahead(self):
  with tempfile.TemporaryDirectory() as root:
   c=EventCalendar(root);now=datetime(2026,10,18,14,tzinfo=timezone.utc)
   event={'symbol':'AAPL','at':(now+timedelta(hours=24)).isoformat(),'source':'https://investor.example/earnings'}
   c.add(event,now);c.add(event,now+timedelta(hours=1));self.assertEqual(len(c.state()['events']),1)
   self.assertFalse(c.upcoming('AAPL',(now-timedelta(minutes=1)).isoformat()))
   self.assertTrue(c.upcoming('AAPL',now.isoformat()));self.assertFalse(c.upcoming('MSFT',now.isoformat()))
   for bad in ({'at':'2026-10-19T14:00:00'},{'source':'javascript:alert(1)'},{'source':'https://user:secret@example.com'},{'at':(now-timedelta(hours=1)).isoformat()}):
    with self.assertRaises(ValueError):c.add(event|bad,now)
 def test_trade_evidence_is_atomic_and_sampled(self):
  with tempfile.TemporaryDirectory() as root:
   l=Ledger(Path(root)/'paper.sqlite3');self.addCleanup(l.db.close);l.control('start');history={'AAPL':list(range(1,51))}
   for day,price in ((15,100),(16,110),(17,90)):
    l.cycle({'AAPL':price},history,.75,f'2026-10-{day}',f'2026-10-{day}T15:00:00Z',symbols=['AAPL'])
   e=l.accounting()['closed_reviews'][0];r=e['trade_review']
   self.assertEqual((r['low_usd'],r['high_usd'],r['observations']),(90,110,3));self.assertFalse(r['partial'])
   self.assertEqual(e['entry_evidence']['sma20'],40.5);self.assertFalse(e['entry_evidence']['news_used'])
   self.assertAlmostEqual(l.read()['cash']-1000,e['realised_pnl_gbp'])
 def test_legacy_position_starts_partial_evidence(self):
  with tempfile.TemporaryDirectory() as root:
   l=Ledger(Path(root)/'paper.sqlite3');self.addCleanup(l.db.close);l.control('start');history={'AAPL':list(range(1,51))}
   l.cycle({'AAPL':100},history,.75,'2026-10-15','2026-10-15T15:00:00Z',symbols=['AAPL'])
   s=l.read();s['positions']['AAPL'].pop('trade_review');s['positions']['AAPL'].pop('entry_evidence');l.write(s)
   l.cycle({'AAPL':90},history,.75,'2026-10-16','2026-10-16T15:00:00Z',symbols=['AAPL'])
   e=l.accounting()['closed_reviews'][0];self.assertTrue(e['trade_review']['partial']);self.assertIsNone(e['entry_evidence'])
 def test_models_use_shared_data_and_never_promote(self):
  with tempfile.TemporaryDirectory() as root:
   t=ShadowTrials(root);t.control('enabled',True);stamp='2026-10-09T14:00:00+00:00'
   bars={s:LargeMarket().get('time_series',symbol=s)['values'][-50:] for s in ('AAPL','MSFT')}
   EventCalendar(root).add({'symbol':'AAPL','at':'2026-10-10T14:00:00+00:00','source':'https://example.com/event'},datetime.fromisoformat(stamp))
   with patch('strategy.assess',side_effect=lambda rows:{'volatility_pct':10 if rows is bars['AAPL'] else 2}):
    t.observe({'AAPL':200,'MSFT':200},bars,.75,'2026-10-09',stamp,['AAPL','MSFT'])
   s=t.state();p=s['books']['volatility_size']['positions'];self.assertLessEqual(p['AAPL']['cost_gbp'],25);self.assertLessEqual(p['MSFT']['cost_gbp'],125)
   self.assertGreater(p['MSFT']['cost_gbp'],100);self.assertNotIn('AAPL',s['books']['event_guard']['positions']);self.assertIn('MSFT',s['books']['event_guard']['positions'])
   self.assertEqual(s['books']['trend_breadth']['positions'],{});self.assertFalse(any(g['eligible_for_promotion'] for g in s['review_gates'].values()))
   self.assertFalse((Path(root)/'paper.sqlite3').exists())
 def test_old_comparisons_preserved_until_explicit_restart(self):
  with tempfile.TemporaryDirectory() as root:
   t=ShadowTrials(root);t.change(lambda s:s['books'].pop('event_guard'));t=ShadowTrials(root);s=t.state()
   self.assertTrue(s['invalid']);self.assertFalse(s['enabled']);self.assertNotIn('event_guard',s['books'])
   with self.assertRaises(ValueError):t.control('enabled',True)
   t.control('restart');self.assertFalse(t.state()['invalid']);self.assertIn('event_guard',t.state()['books'])
