import copy
from datetime import date,timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from engine import Ledger
from research import Research
from strategy import WATCHLIST,rank_histories,candidates,settings
from ranked_backtest import run
from ranked_scanner import RankedScanner
from test_scanner import FakeMarket,NOW,listing

def fixture(flat=False):
    days=[(date(2023,1,1)+timedelta(days=i)).isoformat() for i in range(100)]
    data={s:{d:{'open':100 if flat else 100+i*(j+1)/30,
                'close':100 if flat else 100+i*(j+1)/30,'volume':20000}
             for i,d in enumerate(days)} for j,s in enumerate(WATCHLIST)}
    data['USD/GBP']={d:{'open':.8,'close':.8} for d in days}
    return data,days[55],days[-1]

class RankedBacktestTests(unittest.TestCase):
    def test_shared_ranker_drives_first_five_purchases(self):
        data,start,end=fixture();days=sorted(data['AAPL']);i=days.index(start)
        expected=candidates(rank_histories({s:[data[s][d] for d in days[i-50:i]] for s in WATCHLIST}),[])[:5]
        r=run(data,start,end,0,0)
        buys=[t for t in r['comparisons']['strategy']['trades'] if t['date']==start and t['side']=='BUY']
        self.assertEqual([t['symbol'] for t in buys],expected)
        self.assertNotEqual(expected,sorted(expected))
        self.assertEqual(r['strategy_settings'],settings())
        self.assertEqual(r['initial_gbp'],1000)
        self.assertTrue(all(-t['net_gbp']<=200 for t in buys))
        self.assertTrue(all(p['positions']<=5 and p['cash']>=0 for p in r['comparisons']['strategy']['curve']))

    def test_flat_costless_portfolios_preserve_1000(self):
        data,a,b=fixture(True);r=run(data,a,b,0,0)
        for portfolio in r['comparisons'].values():
            self.assertAlmostEqual(portfolio['metrics']['final_gbp'],1000)
            self.assertAlmostEqual(portfolio['metrics']['return_pct'],0)

    def test_costs_and_profit_reconcile(self):
        data,a,b=fixture(True);r=run(data,a,b)
        p=r['comparisons']['equal_weight'];m=p['metrics']
        self.assertAlmostEqual(1000+sum(t['net_gbp'] for t in p['trades']),m['final_gbp'])
        self.assertAlmostEqual(m['modelled_costs_gbp'],-m['net_profit_gbp'])
        self.assertAlmostEqual(m['return_pct'],m['net_profit_gbp']/10)

    def test_future_prices_and_volumes_cannot_change_earlier_trades(self):
        data,a,b=fixture();other=copy.deepcopy(data);future=sorted(data['AAPL'])[85]
        for s in WATCHLIST:
            for d in other[s]:
                if d>=future:other[s][d]={'open':1,'close':1,'volume':0}
        before=lambda r:[t for t in r['comparisons']['strategy']['trades'] if t['date']<future]
        self.assertEqual(before(run(data,a,b)),before(run(other,a,b)))

    def test_entry_uses_next_open_not_signal_close(self):
        data,a,b=fixture();report=run(data,a,b,0,0)
        symbol=report['comparisons']['strategy']['trades'][0]['symbol']
        data[symbol][a]['open']=300
        report=run(data,a,b,0,0)
        t=next(t for t in report['comparisons']['strategy']['trades'] if t['symbol']==symbol and t['side']=='BUY')
        self.assertEqual(t['price_usd'],300)

    def test_missing_history_or_volume_rejected(self):
        data,a,b=fixture();del data['AAPL'][a]
        with self.assertRaises(ValueError):run(data,a,b)
        data,a,b=fixture();del data['AAPL'][sorted(data['AAPL'])[50]]['volume']
        with self.assertRaises(KeyError):run(data,a,b)

    def test_current_fx_does_not_leak_into_open_fill(self):
        data,a,b=fixture();other=copy.deepcopy(data);other['USD/GBP'][a]['close']=20
        first=lambda d:[t for t in run(d,a,b)['comparisons']['strategy']['trades'] if t['date']==a]
        self.assertEqual(first(data),first(other))

    def test_new_report_exports_and_legacy_compatibility(self):
        data,a,b=fixture()
        with tempfile.TemporaryDirectory() as root:
            manager=Research(root);manager.latest=run(data,a,b)
            csv,_=manager.export('equity');self.assertIn('unranked_gbp',csv)
            for kind in ('json','trades'):self.assertTrue(manager.export(kind)[0])

class RankedMarket(FakeMarket):
    def get(self,endpoint,priority=False,**params):
        if endpoint=='stocks':
            self.calls.append((endpoint,None,priority))
            return {'data':[listing(s) for s in WATCHLIST]}
        raw=super().get(endpoint,priority=priority,**params)
        if endpoint=='time_series' and params['symbol'] in WATCHLIST:
            j=WATCHLIST.index(params['symbol'])+1
            for i,row in enumerate(raw['values']):row['close']=100+i*j/30
        return raw

class RankedScannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.market=RankedMarket()
        self.ledger=Ledger(Path(self.tmp.name)/'paper.sqlite3');self.ledger.control('start')
        self.scanner=RankedScanner(self.tmp.name,self.market)
    def tearDown(self):self.ledger.db.close();self.tmp.cleanup()
    def scan(self):
        with patch('ranked_scanner.datetime') as clock:
            clock.now.return_value=NOW;self.scanner.cycle(self.ledger)

    def test_complete_watchlist_ranked_before_entries_and_daily_cache_survives(self):
        self.scan();self.assertEqual(self.scanner.status['histories_ready'],30)
        expected=candidates(rank_histories(self.scanner.daily['bars']),[])
        self.assertEqual(self.scanner.status['selected'],expected)
        self.assertEqual(list(self.ledger.read()['positions']),expected[:5])
        self.market.calls=[];self.scanner=RankedScanner(self.tmp.name,self.market);self.scan()
        self.assertFalse(any(c[0]=='time_series' for c in self.market.calls))

    def test_incomplete_watchlist_blocks_entries(self):
        self.market.bad.add(WATCHLIST[0]);self.scan()
        self.assertFalse(self.scanner.status['ranking_complete'])
        self.assertEqual(self.ledger.read()['positions'],{})

    def test_incomplete_watchlist_preserves_held_checks(self):
        self.scan();held=set(self.ledger.read()['positions'])
        absent=next(s for s in WATCHLIST if s not in held)
        self.scanner.daily['bars'].pop(absent);self.market.bad.add(absent)
        self.scan();self.assertFalse(self.scanner.status['ranking_complete'])
        self.assertEqual(set(self.scanner.status['checked']),held)
