import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from app import monitoring_rpc, monitoring_stdio, monitoring_enabled, set_monitoring, monitor_db
from engine import Ledger
from ranked_scanner import Journal

class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.data=self.root/'data';self.data.mkdir()
        self.ledger=Ledger(self.data/'paper.sqlite3');self.addCleanup(self.ledger.db.close)
        state=self.ledger.read();state.update(cash=850,equity=1010,fx=.8,last_success='2026-10-14T17:00:00+00:00',error='test PRIVATE-KEY')
        state['positions']={'MSFT':{'qty':2,'entry_usd':90,'cost_gbp':150,'opened':'2026-10-14T15:00:00Z','private_field':'not shared'}}
        state['marks']={'MSFT':100};state['DASHBOARD_PASSWORD']='not shared';self.ledger.write(state)
        (self.root/'config.json').write_text(json.dumps({'TWELVE_DATA_API_KEY':'PRIVATE-KEY','DASHBOARD_PASSWORD':'PRIVATE-PASSWORD'}))
        Journal(self.data)
        with sqlite3.connect(self.data/'decisions.sqlite3') as db:
            for i,action in enumerate(('BUY','HOLD','SELL')):
                body={'at':f'2026-10-14T17:00:0{i}Z','day':'2026-10-14','symbol':'MSFT','action':action,'reason':'test PRIVATE-KEY','private_field':'not shared'}
                db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?)',(str(i),body['at'],body['day'],'MSFT',json.dumps(body),'{}'))

    def call(self,name='get_bot_status',args=None):
        return monitoring_rpc({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args or {}}},self.data)
    def output(self,response):return json.loads(response['result']['content'][0]['text'])

    def test_disabled_by_default_revocation_and_unknown_write_tools(self):
        self.assertFalse(monitoring_enabled(self.data));self.assertTrue(self.call()['result']['isError'])
        set_monitoring(self.data,True);self.assertNotIn('isError',self.call()['result'])
        set_monitoring(self.data,False);self.assertTrue(self.call()['result']['isError'])
        for name in ('buy','sell','pause','read_file','set_monitoring'):
            self.assertEqual(self.call(name)['error']['code'],-32602)
        with self.assertRaises(ValueError):set_monitoring(self.data,'true')

    def test_snapshot_is_allowlisted_redacted_and_does_not_write_or_fetch(self):
        set_monitoring(self.data,True);before=self.ledger.read()
        with patch('app.Market.get',side_effect=AssertionError('no API calls')):
            output=self.output(self.call())
        self.assertEqual(self.ledger.read(),before)
        self.assertEqual(output['positions'][0]['unrealised_gbp'],10)
        self.assertEqual(output['positions'][0]['value_gbp'],160)
        self.assertEqual(output['error'],'test [redacted]')
        raw=json.dumps(output)
        for secret in ('PRIVATE-KEY','PRIVATE-PASSWORD','private_field','DASHBOARD_PASSWORD','csrf'):self.assertNotIn(secret,raw)
        self.assertIn('Unknown',output['worker_health']);self.assertIn('seconds_since_success',output)
        db=monitor_db(self.data,'paper.sqlite3')
        try:
            with self.assertRaises(sqlite3.OperationalError):db.execute('DELETE FROM ledger')
        finally:db.close()

    def test_date_and_limit_bounds_and_trades_filter(self):
        set_monitoring(self.data,True)
        trades=self.output(self.call('get_recent_trades',{'day':'2026-10-14','limit':1}))
        self.assertEqual(trades['records'][0]['action'],'SELL');self.assertTrue(trades['more_available'])
        self.assertEqual(self.output(self.call('get_recent_decisions',{'day':'2026-10-13'}))['records'],[])
        for args in ({'limit':True},{'limit':101},{'day':'2026-02-31'},{'path':'/etc/passwd'}):
            self.assertTrue(self.call('get_recent_decisions',args)['result']['isError'])
        self.assertTrue(self.call(args={'path':'/etc/passwd'})['result']['isError'])

    def test_missing_store_is_not_created_and_errors_hide_paths(self):
        empty=self.root/'empty';empty.mkdir();set_monitoring(empty,True)
        response=monitoring_rpc({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'get_bot_status'}},empty)
        self.assertTrue(response['result']['isError']);self.assertNotIn(str(self.root),json.dumps(response))
        self.assertFalse((empty/'paper.sqlite3').exists())

    def test_protocol_and_stream_are_bounded_and_read_only(self):
        initialize=monitoring_rpc({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26'}},self.data)
        self.assertEqual(initialize['result']['protocolVersion'],'2025-03-26')
        listed=monitoring_rpc({'jsonrpc':'2.0','id':2,'method':'tools/list'},self.data)['result']['tools']
        self.assertEqual(len(listed),3);self.assertTrue(all(t['annotations']['readOnlyHint'] for t in listed))
        request={'jsonrpc':'2.0','method':'tools/call','params':{'name':'get_bot_status'}}
        self.assertIsNone(monitoring_rpc(request,self.data))
        source=io.StringIO('bad\n'+'x'*65537+'\n'+json.dumps({'jsonrpc':'2.0','id':3,'method':'ping'})+'\n')
        target=io.StringIO();monitoring_stdio(self.data,source,target)
        messages=[json.loads(line) for line in target.getvalue().splitlines()]
        self.assertEqual(messages[0]['error']['code'],-32700);self.assertEqual(messages[1]['error']['code'],-32600)
        self.assertEqual(messages[2]['result'],{})

    def test_stdio_launch_never_starts_dashboard_or_creates_data(self):
        env=os.environ|{'DATA_DIR':str(self.root/'absent')}
        proc=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'app.py'),'--monitor-stdio'],
            input=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list'})+'\n',capture_output=True,text=True,env=env,timeout=10,check=True)
        self.assertEqual(len(json.loads(proc.stdout)['result']['tools']),3)
        self.assertEqual(proc.stderr,'');self.assertFalse((self.root/'absent').exists())
