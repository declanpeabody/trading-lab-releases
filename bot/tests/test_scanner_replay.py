import copy
from datetime import date, timedelta
import unittest
from ranked_backtest import run_batched, parse_series
from strategy import WATCHLIST, screening_universe


def fixture():
    days=[(date(2024,1,1)+timedelta(days=i)).isoformat() for i in range(130) if (date(2024,1,1)+timedelta(days=i)).weekday()<5]
    mapping={s:'NASDAQ' for s in (*WATCHLIST,*(f'S{i:03}' for i in range(280)))}
    data={s:{d:{'open':100+i*.5+j*.01,'close':100+i*.5+j*.01,'volume':50000} for i,d in enumerate(days)} for j,s in enumerate(screening_universe(mapping))}
    data['USD/GBP']={d:{'open':.8,'close':.8} for d in days}
    return data,mapping,days[60],days[81]


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data,cls.mapping,cls.start,cls.end=fixture()
        cls.report=run_batched(cls.data,cls.mapping,cls.start,cls.end)
    def test_three_hundred_actual_scanner_replay_and_cash_reconciliation(self):
        r=self.report;p=r['comparisons']['strategy']
        self.assertEqual(len(r['symbols']),300)
        self.assertTrue(p['trades'])
        self.assertTrue(all(c['api_attempts']<=700 and c['ranked_max']<=300 for c in r['coverage']))
        first=[t for t in p['trades'] if t['date']==self.start and t['side']=='BUY']
        self.assertTrue(all(t['symbol'] in WATCHLIST[:20] for t in first))
        self.assertTrue(all(-t['net_gbp']<=200 for t in p['trades'] if t['side']=='BUY'))
        self.assertTrue(all(c['cash']>=0 and c['positions']<=5 for c in p['curve']))
        self.assertAlmostEqual(1000+sum(t['net_gbp'] for t in p['trades']),p['metrics']['final_gbp'])
    def test_future_bars_cannot_change_earlier_decisions(self):
        data=copy.deepcopy(self.data);boundary=sorted(d for d in data['AAPL'] if self.start<=d<=self.end)[12]
        for s in data:
            for day,row in data[s].items():
                if day>=boundary:row['close']*=.5;row['open']*=.5
        changed=run_batched(data,self.mapping,self.start,self.end)
        prior=lambda r:[t for t in r['comparisons']['strategy']['trades'] if t['date']<boundary]
        self.assertEqual(prior(changed),prior(self.report))
    def test_missing_histories_and_small_budget_limit_coverage(self):
        data=copy.deepcopy(self.data);data['AAPL']={}
        r=run_batched(data,self.mapping,self.start,self.end,normal_limit=45,priority_limit=60)
        self.assertIn('AAPL',r['symbols'])
        self.assertFalse(any(t['symbol']=='AAPL' for t in r['comparisons']['strategy']['trades']))
        self.assertTrue(all(c['api_attempts']<=60 and c['prepared']<300 for c in r['coverage']))
        self.assertTrue(r['replay_errors'])
    def test_expanded_stock_validation_requires_currency_and_volume(self):
        raw={'meta':{'symbol':'ZZZZ','currency':'EUR'},'values':[{'datetime':'2024-01-01','open':1,'close':1,'volume':1}]}
        with self.assertRaises(ValueError):parse_series(raw,'ZZZZ')
        raw['meta']['currency']='USD';raw['values'][0].pop('volume')
        with self.assertRaises(KeyError):parse_series(raw,'ZZZZ')
