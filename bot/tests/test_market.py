import io
import json
import os
import socket
import ssl
import tempfile
import unittest
from contextlib import ExitStack
from urllib.error import HTTPError,URLError
from unittest.mock import patch
from market import Market,MarketUnavailable,ApiBudget

class MarketErrors(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        tmp=self.stack.enter_context(tempfile.TemporaryDirectory());self.directory=tmp
        self.stack.enter_context(patch.dict(os.environ,{'DATA_DIR':tmp,'TWELVE_DATA_API_KEY':'PRIVATE-TEST-SECRET'}))
        self.clock=self.stack.enter_context(patch('market.time.monotonic',return_value=10000))
        self.stack.enter_context(patch('market.time.sleep'))
        for name,value in [('blocked_until',0),('blocked_message',''),('last_global_request',0)]:
            self.stack.enter_context(patch.object(Market,name,value))
        self.transport=self.stack.enter_context(patch('market.urlopen'))
        self.market=Market()
    def test_network_categories_do_not_leak_exception_contents(self):
        cases=[(ssl.SSLCertVerificationError('PRIVATE-TEST-SECRET'),'certificate verification'),
               (ssl.SSLError('PRIVATE-TEST-SECRET'),'TLS connection'),
               (socket.gaierror('PRIVATE-TEST-SECRET'),'DNS'),
               (TimeoutError('PRIVATE-TEST-SECRET'),'timed out'),
               (ConnectionRefusedError('PRIVATE-TEST-SECRET'),'network connection')]
        for reason,label in cases:
            with self.subTest(label=label):
                Market.blocked_until=0;self.transport.side_effect=URLError(reason)
                with self.assertRaises(MarketUnavailable) as err:self.market.get('time_series',symbol='AAPL')
                self.assertIn(label,str(err.exception));self.assertNotIn('PRIVATE-TEST-SECRET',str(err.exception))
    def test_http_codes_and_body_are_safe(self):
        for code,label in [(401,'authentication'),(403,'access denied'),(429,'credit limit'),(503,'service error')]:
            with self.subTest(code=code):
                Market.blocked_until=0
                self.transport.side_effect=HTTPError('https://secret.example/?key=PRIVATE-TEST-SECRET',code,'PRIVATE-TEST-SECRET',{},io.BytesIO(b'PRIVATE-TEST-SECRET'))
                with self.assertRaises(MarketUnavailable) as err:self.market.get('quote',symbol='AAPL')
                self.assertIn(label,str(err.exception));self.assertNotIn('PRIVATE-TEST-SECRET',str(err.exception));self.assertNotIn('secret.example',str(err.exception))
    def test_success_status_api_error_opens_circuit(self):
        self.transport.return_value=io.BytesIO(json.dumps({'status':'error','code':401,'message':'PRIVATE-TEST-SECRET'}).encode())
        with self.assertRaisesRegex(MarketUnavailable,'authentication'):self.market.get('quote',symbol='AAPL')
        self.assertGreater(Market.blocked_until,10000)
    def test_bad_json_and_shape_are_shared_failures(self):
        for raw in (b'not json PRIVATE-TEST-SECRET',b'[]',b'null',b'\xff'):
            Market.blocked_until=0;self.transport.return_value=io.BytesIO(raw)
            with self.assertRaises(MarketUnavailable) as err:self.market.get('stocks')
            self.assertNotIn('PRIVATE-TEST-SECRET',str(err.exception))
    def test_shared_cooldown_uses_no_more_credits_and_recovers(self):
        self.transport.side_effect=URLError(TimeoutError())
        with self.assertRaises(MarketUnavailable):self.market.get('quote',symbol='AAPL')
        for priority in (False,True):
            with self.assertRaisesRegex(MarketUnavailable,'no additional API credit'):Market().get('quote',symbol='MSFT',priority=priority)
        self.assertEqual(self.transport.call_count,1);self.assertEqual(ApiBudget(self.directory).used(),1)
        self.clock.return_value=10901;self.transport.side_effect=None;self.transport.return_value=io.BytesIO(b'{"symbol":"AAPL"}')
        self.assertEqual(self.market.get('quote',symbol='AAPL'),{'symbol':'AAPL'})
        self.assertEqual(ApiBudget(self.directory).used(),2);self.assertEqual(Market.blocked_until,0)
    def test_individual_request_rejection_does_not_open_circuit(self):
        self.transport.side_effect=HTTPError('https://api.twelvedata.com',404,'not found',{},io.BytesIO())
        with self.assertRaises(ValueError) as err:self.market.get('quote',symbol='MISSING')
        self.assertNotIsInstance(err.exception,MarketUnavailable);self.assertEqual(Market.blocked_until,0)
    def test_missing_key_uses_no_credit(self):
        with patch.dict(os.environ,{'TWELVE_DATA_API_KEY':''}):
            with self.assertRaisesRegex(MarketUnavailable,'Setup needed'):self.market.get('quote',symbol='AAPL')
        self.transport.assert_not_called();self.assertEqual(ApiBudget(self.directory).used(),0)

    def test_malformed_key_is_reported_before_network_or_budget_use(self):
        for key in ('first\nsecond','first second','first\rsecond','first\tsecond','first\u200bsecond'):
            with self.subTest(key_type='hidden or whitespace'),patch.dict(os.environ,{'TWELVE_DATA_API_KEY':key}):
                with self.assertRaisesRegex(MarketUnavailable,'saved API key contains') as err:self.market.get('stocks')
                self.assertNotIn(key,str(err.exception))
        self.transport.assert_not_called();self.assertEqual(ApiBudget(self.directory).used(),0)

    def test_header_value_error_is_not_mislabelled_as_json(self):
        self.transport.side_effect=ValueError('Invalid header PRIVATE-TEST-SECRET')
        with self.assertRaises(MarketUnavailable) as err:self.market.get('stocks')
        message=str(err.exception)
        self.assertIn('Python rejected',message)
        self.assertNotIn('not valid JSON',message)
        self.assertNotIn('PRIVATE-TEST-SECRET',message)

    def test_invalid_json_requires_an_actual_response_body(self):
        self.transport.return_value=io.BytesIO(b'<html>PRIVATE-TEST-SECRET</html>')
        with self.assertRaisesRegex(MarketUnavailable,'HTTP response was received') as err:self.market.get('stocks')
        self.assertNotIn('PRIVATE-TEST-SECRET',str(err.exception))

    def test_outer_clipboard_whitespace_is_trimmed(self):
        with patch.dict(os.environ,{'TWELVE_DATA_API_KEY':'  PRIVATE-TEST-SECRET\n'}):
            self.transport.return_value=io.BytesIO(b'{"data":[]}')
            self.assertEqual(self.market.get('stocks'),{'data':[]})
        request=self.transport.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'),'apikey PRIVATE-TEST-SECRET')
