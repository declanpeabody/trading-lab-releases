import json,sqlite3,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from research import ResearchSignals

class ResearchSignalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.journal=ResearchSignals(self.root)
        self.now=datetime(2026,10,20,15,tzinfo=timezone.utc)
        self.bars={'AAPL':[{'close':p,'datetime':'2026-10-19'} for p in range(100,150)]}
    def observe(self,price=150,hours=0,now=None):
        at=self.now+timedelta(hours=hours)
        self.journal.observe({'AAPL':price},self.bars,at.isoformat(),[{'symbol':'AAPL','action':'HOLD'}],now=now or at)
    def test_models_deduplicate_daily_and_keep_original_evidence(self):
        self.observe();self.observe(140)
        rows=self.journal.state()['records'];self.assertEqual(len(rows),2)
        self.assertTrue(all(r['direction']=='BUY' and r['baseline']['price_usd']==150 and r['active_action']=='HOLD' for r in rows))
        self.observe(140,24)
        self.assertEqual(self.journal.state()['total'],4)
    def test_forward_outcomes_and_costs_include_losses(self):
        self.observe();self.observe(140,24)
        rows=[r for r in self.journal.state()['records'] if r['first_seen']==self.now.isoformat()]
        self.assertTrue(all(r['outcomes']['24']['hypothetical_long_after_cost_pct']<r['outcomes']['24']['price_return_pct']<0 for r in rows))
        self.assertEqual(self.journal.state()['summary']['trend_20_50']['buy_positive_after_cost'],0)
    def test_stale_future_and_nonfinite_quotes_rejected(self):
        self.observe(now=self.now+timedelta(seconds=121));self.observe(now=self.now-timedelta(seconds=1));self.observe(float('nan'))
        self.assertEqual(self.journal.state()['total'],0)
    def test_news_requires_subsequent_quote_and_does_not_backfill(self):
        event={'id':'abc','symbol':'AAPL','first_seen':self.now.isoformat(),'published_at':self.now.isoformat(),'title':'AAPL earnings','source':'Test','kind':'news'}
        with sqlite3.connect(self.root/'feed-observations.sqlite3') as db:
            db.execute('CREATE TABLE observations(id TEXT,body TEXT)');db.execute('INSERT INTO observations VALUES (?,?)',('abc',json.dumps(event)))
        self.observe();row=next(r for r in self.journal.state()['records'] if r['module']=='news');self.assertIsNone(row['baseline'])
        self.observe(151,1);row=next(r for r in self.journal.state()['records'] if r['module']=='news');self.assertEqual(row['baseline']['price_usd'],151)
        self.assertEqual(row['direction'],'OBSERVE');self.assertEqual(row['first_seen'],self.now.isoformat())
    def test_expired_windows_stay_unmeasured(self):
        self.observe();self.observe(160,200)
        row=next(r for r in self.journal.state(now=self.now+timedelta(hours=200))['records'] if r['first_seen']==self.now.isoformat())
        self.assertEqual(row['outcomes'],{});self.assertIn('incomplete',row['status'])
    def test_source_failure_is_visible_and_no_network(self):
        (self.root/'feed-observations.sqlite3').write_text('broken')
        with patch('market.Market.get',side_effect=AssertionError('network forbidden')):self.observe()
        self.assertEqual(self.journal.state()['total'],2);self.assertTrue(self.journal.state()['source_errors'])
    def test_hold_sell_and_restart_preserve_evidence(self):
        self.observe(139.5)
        self.assertEqual({r['direction'] for r in self.journal.state()['records']},{'HOLD','SELL'})
        self.assertEqual(ResearchSignals(self.root).state()['total'],2)

    def test_scanner_research_failure_cannot_block_account(self):
        from test_focus_replacement import FocusReplacementTests
        case=FocusReplacementTests();case.setUp()
        try:
            with patch.object(ResearchSignals,'observe',side_effect=OSError('disk full')):
                case.scan();case.scan()
            self.assertTrue(case.ledger.read()['positions'])
            self.assertIsNone(case.ledger.read()['error'])
            self.assertIn('signals_error',case.scanner.status)
        finally:case.doCleanups()
