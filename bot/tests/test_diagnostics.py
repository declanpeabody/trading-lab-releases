import json
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from market import ApiBudget, BudgetDeferred, ConnectionTest, Market, MarketUnavailable
from app import no_trade_reasons

class DiagnosticsTests(unittest.TestCase):
    def test_history_reserve_keeps_candidate_and_holdings_credits(self):
        with tempfile.TemporaryDirectory() as root,patch('market.datetime') as clock:
            now=datetime(2026,10,14,10,0,tzinfo=ZoneInfo('America/New_York'))
            clock.now.side_effect=lambda zone:now.astimezone(zone)
            budget=ApiBudget(root)
            self.assertEqual(budget.history_headroom(),168)
            for _ in range(238):budget.reserve(purpose='history')
            with self.assertRaises(BudgetDeferred):budget.reserve(purpose='history')
            self.assertEqual(budget.used(),238)
            with self.assertRaises(BudgetDeferred):budget.reserve()
            with self.assertRaises(ValueError):budget.reserve(purpose='diagnostic')
            budget.reserve(priority=True)
            self.assertEqual(budget.used(),239)
            self.assertEqual(budget.state()['normal_remaining'],0)
            self.assertEqual(budget.state()['total_remaining'],461)

    def test_research_does_not_spend_during_session_and_has_persisted_subcap(self):
        with tempfile.TemporaryDirectory() as root,patch('market.datetime') as clock:
            now=datetime(2026,10,14,10,tzinfo=ZoneInfo('America/New_York'))
            clock.now.side_effect=lambda zone:now.astimezone(zone)
            budget=ApiBudget(root)
            with self.assertRaises(BudgetDeferred):budget.reserve(purpose='research')
            self.assertEqual(budget.used(),0)
            now=now.replace(hour=17)
            for _ in range(50):budget.reserve(purpose='research')
            with self.assertRaises(BudgetDeferred):ApiBudget(root).reserve(purpose='research')
            self.assertEqual(budget.used(),50)
            budget.reserve();budget.reserve(purpose='history')
            with self.assertRaises(ValueError):budget.reserve(priority=True,purpose='research')
            self.assertEqual(budget.used(),52)

    def test_history_headroom_boundaries(self):
        zone=ZoneInfo('America/New_York')
        for day,hour,minute,expected in [(14,8,0,182),(14,15,59,7),(14,16,0,0),(17,10,0,0)]:
            self.assertEqual(ApiBudget.history_headroom(datetime(2026,10,day,hour,minute,tzinfo=zone)),expected)

    def test_diagnostic_validates_history_without_requiring_open_market(self):
        data={'meta':{'symbol':'AAPL','exchange':'NASDAQ','currency':'USD'},'values':[{'datetime':'2026-10-13','close':'123.4'}]}
        test=ConnectionTest()
        with patch.object(Market,'get',return_value=data) as get:
            test._run()
        self.assertTrue(test.state()['ok']);self.assertFalse(test.state()['running'])
        self.assertEqual(get.call_count,1);self.assertEqual(get.call_args.kwargs['purpose'],'diagnostic')
        self.assertIn('does not verify',test.state()['message'])
        data['values'][0]['close']='nan'
        with patch.object(Market,'get',return_value=data):test._run()
        self.assertFalse(test.state()['ok']);self.assertIn('incomplete or invalid',test.state()['message'])

    def test_diagnostic_failure_is_safe_and_clicks_are_rate_limited(self):
        test=ConnectionTest()
        with patch.object(Market,'get',side_effect=MarketUnavailable('authentication rejected')):test._run()
        self.assertFalse(test.state()['ok']);self.assertEqual(test.state()['message'],'authentication rejected')
        with patch('market.threading.Thread') as thread,patch('market.time.monotonic',return_value=100):
            test.start()
            with self.assertRaisesRegex(ValueError,'already running'):test.start()
            with patch.object(Market,'get',side_effect=OSError('SECRET')):test._run()
            self.assertNotIn('SECRET',json.dumps(test.state()))
            with self.assertRaisesRegex(ValueError,'one minute'):test.start()
            self.assertEqual(thread.call_count,1)

    def test_why_panel_keeps_last_error_and_partial_coverage_distinct(self):
        state={'paused':False,'data_configured':True,'cash':1000,'positions':{},'error':'old failure',
            'scanner':{'ranked_histories':20,'universe_count':300,'selected':['AAPL'],'checked':['AAPL'],
                'decisions':[{'symbol':'AAPL','action':'SKIP','reason':'No entry signal'}],'decision_at':'2026-10-13T15:00:00Z'}}
        result=no_trade_reasons(state)
        self.assertTrue(any('Last account/data error: old failure'==r for r in result['reasons']))
        self.assertTrue(any('does not block all entries' in r for r in result['reasons']))
        self.assertEqual(result['decisions'][0]['reason'],'No entry signal')
        self.assertEqual(state['error'],'old failure')


class QuoteValidationTests(unittest.TestCase):
    def test_reasons_are_specific_and_do_not_echo_bad_field_values(self):
        from engine import quote
        base=dict(symbol='MSFT',currency='USD',timestamp=1000,close=100,is_market_open=True)
        for changes,reason in [({'timestamp':'PRIVATE-SECRET'},'timestamp is missing'),
                ({'close':'PRIVATE-SECRET'},'price is missing'),({'timestamp':800},'200.0 seconds old'),
                ({'timestamp':1010},'10.0 seconds ahead'),({'currency':'PRIVATE-SECRET'},'currency mismatch'),
                ({'is_market_open':False},'Market is closed')]:
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError) as error:quote(base|changes,'MSFT',1000,'USD')
                self.assertIn(reason,str(error.exception));self.assertNotIn('PRIVATE-SECRET',str(error.exception))
        self.assertEqual(quote(base,'MSFT',1120,'USD'),100)
        self.assertEqual(quote(base,'MSFT',995,'USD'),100)
        for now in (1120.01,994.99):
            with self.assertRaises(ValueError):quote(base,'MSFT',now,'USD')
