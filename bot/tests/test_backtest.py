import copy
from datetime import date,timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from backtest import run,metrics,parse_series
from engine import SYMBOLS
from mac_update import apply_update,FILES
from research import Research

def fixture(flat=False):
    ds=[(date(2023,1,1)+timedelta(days=i)).isoformat() for i in range(100)]
    series={s:{d:{'open':100 if flat else 100+i,'close':100 if flat else 100+i} for i,d in enumerate(ds)} for s in SYMBOLS}
    series['USD/GBP']={d:{'open':.8,'close':.8} for d in ds}
    return series,ds[55],ds[-1]

class BacktestTests(unittest.TestCase):
    def test_flat_zero_cost_benchmarks_preserve_capital(self):
        s,a,b=fixture(True);r=run(s,a,b,0,0)
        for p in r['comparisons'].values():self.assertAlmostEqual(p['metrics']['final_gbp'],100)
        self.assertEqual(r['comparisons']['strategy']['trades'],[])

    def test_costs_reduce_flat_benchmark_and_reconcile(self):
        s,a,b=fixture(True);r=run(s,a,b)
        p=r['comparisons']['hold_60'];m=p['metrics']
        self.assertLess(m['final_gbp'],100)
        self.assertAlmostEqual(100+sum(t['net_gbp'] for t in p['trades']),m['final_gbp'])
        self.assertAlmostEqual(sum(t['pnl_gbp'] or 0 for t in p['trades']),m['net_profit_gbp'])
        self.assertAlmostEqual(m['modelled_costs_gbp'],-m['net_profit_gbp'])

    def test_next_open_fills_and_purchase_cap(self):
        s,a,b=fixture();s['AAPL'][a]['open']=200
        r=run(s,a,b,0,0);t=r['comparisons']['strategy']['trades'][0]
        self.assertEqual(t['date'],a);self.assertEqual(t['price_usd'],200)
        for t in r['comparisons']['strategy']['trades']:
            if t['side']=='BUY':self.assertLessEqual(-t['net_gbp'],20)

    def test_future_price_changes_do_not_change_earlier_trades(self):
        s,a,b=fixture();future=sorted(s['AAPL'])[85];other=copy.deepcopy(s)
        for sym in SYMBOLS:
            for d in other[sym]:
                if d>=future:other[sym][d]={'open':10,'close':10}
        r1=run(s,a,b);r2=run(other,a,b)
        earlier=lambda r:[t for t in r['comparisons']['strategy']['trades'] if t['date']<future]
        self.assertEqual(earlier(r1),earlier(r2))

    def test_fx_on_trade_date_not_used_for_that_day(self):
        s,a,b=fixture();other=copy.deepcopy(s);other['USD/GBP'][a]['close']=10
        first=lambda x:[t for t in run(x,a,b)['comparisons']['strategy']['trades'] if t['date']==a]
        self.assertEqual(first(s),first(other))

    def test_missing_stock_session_is_rejected(self):
        s,a,b=fixture();del s['MSFT'][a]
        with self.assertRaises(ValueError):run(s,a,b)

    def test_missing_fx_is_rejected(self):
        s,a,b=fixture();s['USD/GBP']={}
        with self.assertRaises(ValueError):run(s,a,b)

    def test_bad_costs_and_insufficient_warmup(self):
        s,a,b=fixture()
        with self.assertRaises(ValueError):run(s,a,b,float('nan'),15)
        with self.assertRaises(ValueError):run(s,'2023-01-05',b)

    def test_drawdown_includes_initial_capital(self):
        curve=[{'date':'2023-01-01','equity':120},{'date':'2023-01-02','equity':90}]
        self.assertEqual(metrics(curve,[],'2023-01-01','2023-01-02')['max_drawdown_pct'],25)
        self.assertIsNone(metrics(curve,[],'2023-01-01','2023-01-02')['win_rate_pct'])

    def test_exports_and_report_restart(self):
        s,a,b=fixture()
        with tempfile.TemporaryDirectory() as d:
            manager=Research(d);manager.latest=run(s,a,b)
            for kind in ('json','equity','trades'):
                data,mime=manager.export(kind);self.assertTrue(data)
            manager._save(manager.directory/'latest.json',manager.latest)
            self.assertEqual(Research(d).latest['start'],a)

class UpdateTests(unittest.TestCase):
    def test_upgrade_preserves_credentials_and_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)/'installed';src=Path(d)/'download';base.mkdir();src.mkdir();(base/'data').mkdir()
            (base/'config.json').write_text('{"key":"private-test-value"}')
            for f in FILES:(src/f).write_text('new '+f)
            (base/'app.py').write_text('old app')
            db=base/'data'/'paper.sqlite3'
            with sqlite3.connect(db) as c:
                c.execute('create table test(value text)');c.execute("insert into test values ('preserved')")
            backup=apply_update(base,src)
            self.assertEqual((base/'config.json').read_text(),'{"key":"private-test-value"}')
            with sqlite3.connect(db) as c:self.assertEqual(c.execute('select value from test').fetchone()[0],'preserved')
            self.assertEqual((backup/'app.py').read_text(),'old app')
            self.assertEqual((base/'app.py').read_text(),'new app.py')

    def test_copy_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)/'installed';src=Path(d)/'source';base.mkdir();src.mkdir();(base/'data').mkdir()
            (base/'config.json').write_text('{}')
            for f in FILES:(src/f).write_text('new')
            (base/'app.py').write_text('old')
            import shutil
            original=shutil.copy2
            def fail(source,target):
                if str(target).endswith('engine.py.new'):raise OSError('simulated copy failure')
                return original(source,target)
            with patch('mac_update.shutil.copy2',side_effect=fail):
                with self.assertRaises(OSError):apply_update(base,src)
            self.assertEqual((base/'app.py').read_text(),'old')
