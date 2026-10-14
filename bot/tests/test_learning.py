import copy
from datetime import date,timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from strategy import WATCHLIST,assess
from ranked_scanner import Journal
from ranked_backtest import walk_forward,run
import test_ranked


def long_fixture():
    days=[(date(2022,1,1)+timedelta(days=i)).isoformat() for i in range(400)]
    data={s:{day:{'open':100+i*(j+1)/100,'close':100+i*(j+1)/100,'volume':40000}
             for i,day in enumerate(days)} for j,s in enumerate(WATCHLIST)}
    data['USD/GBP']={d:{'open':.8,'close':.8} for d in days}
    return data,days[55],days[-1]

class WalkForwardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data,cls.start,cls.end=long_fixture()
        cls.result=walk_forward(cls.data,cls.start,cls.end)
    def test_time_order_and_reserved_final_period(self):
        r=self.result;hold=r['holdout'];self.assertGreaterEqual(len(r['folds']),2)
        for fold in r['folds']:
            self.assertLess(fold['train_end'],fold['test_start'])
            self.assertLess(fold['test_end'],hold['start'])
            self.assertGreaterEqual(fold['train_sessions'],126)
            self.assertLessEqual(fold['test_sessions'],63)
            expected=max((100,150,200),key=lambda x:(fold['training_scores'][str(x)],-x))
            self.assertEqual(fold['selected_purchase'],expected)
        self.assertLess(hold['train_end'],hold['start'])
    def test_holdout_prices_cannot_change_selection_or_development(self):
        other=copy.deepcopy(self.data);boundary=self.result['holdout']['start']
        for s in WATCHLIST:
            for day in other[s]:
                if day>=boundary:other[s][day]={'open':10,'close':10,'volume':100000}
        changed=walk_forward(other,self.start,self.end)
        self.assertEqual(self.result['folds'],changed['folds'])
        self.assertEqual(self.result['holdout']['training_scores'],changed['holdout']['training_scores'])
        self.assertEqual(self.result['holdout']['selected_purchase'],changed['holdout']['selected_purchase'])
    def test_future_test_data_cannot_change_first_selection(self):
        other=copy.deepcopy(self.data);boundary=self.result['folds'][0]['test_start']
        for s in WATCHLIST:
            for day in other[s]:
                if day>=boundary:other[s][day]={'open':30,'close':30,'volume':200000}
        changed=walk_forward(other,self.start,self.end)
        self.assertEqual(self.result['folds'][0]['training_scores'],changed['folds'][0]['training_scores'])
        self.assertEqual(self.result['folds'][0]['selected_purchase'],changed['folds'][0]['selected_purchase'])
    def test_size_limit_and_reconciliation(self):
        r=run(self.data,self.start,self.end,purchase_limit=100)
        p=r['comparisons']['strategy']
        self.assertTrue(all(-t['net_gbp']<=100 for t in p['trades'] if t['side']=='BUY'))
        self.assertAlmostEqual(1000+sum(t['net_gbp'] for t in p['trades']),p['metrics']['final_gbp'])
        self.assertEqual(r['strategy_settings']['max_purchase'],100)
    def test_background_job_saves_report_without_changing_account(self):
        from research import Research
        from engine import Ledger
        from unittest.mock import MagicMock
        import json
        with tempfile.TemporaryDirectory() as root:
            ledger=Ledger(Path(root)/'paper.sqlite3');before=ledger.read()
            market=MagicMock()
            def get(endpoint,**params):
                if endpoint=='stocks':return {'data':[]}
                symbol=params['symbol']
                return {'meta':{'symbol':symbol,'currency':'USD','exchange':'NASDAQ'},
                    'values':[dict(datetime=day,**row) for day,row in self.data[symbol].items()]}
            market.get.side_effect=get
            manager=Research(root)
            with patch('research.Market',return_value=market),patch('research.catalogue',return_value={s:'NASDAQ' for s in WATCHLIST}):
                manager._job(self.start,self.end,25,15,False,'walk_forward')
            self.assertIsNone(manager.error)
            self.assertIn('walk_forward',manager.latest)
            self.assertEqual(ledger.read(),before);ledger.db.close()
            saved=json.loads((Path(root)/'research'/'latest.json').read_text())
            self.assertEqual(saved['research_mode'],'walk_forward')
            self.assertEqual(len(saved['provenance']),31)
            self.assertIn('walk_forward',json.loads(manager.export('json')[0]))

    def test_short_history_and_unknown_size_rejected(self):
        with self.assertRaisesRegex(ValueError,'315'):walk_forward(self.data,'2022-08-01',self.end)
        with self.assertRaises(ValueError):run(self.data,self.start,self.end,purchase_limit=300)

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.j=Journal(self.tmp.name)
    def audit(self,day):
        base=date(2023,1,1)
        rows=[{'datetime':(day-timedelta(days=i)).isoformat(),
               'close':100+(day-timedelta(days=i)-base).days,'volume':30000}
              for i in range(50,0,-1)]
        return {'bars':{'AAPL':rows},'prices':{},'decisions':[],
                'before':{'positions':{},'paused':False,'buy_block':False}}
    def record(self,day,status=None):
        self.j.record(day.isoformat()+'T15:00:00+00:00',day.isoformat(),self.audit(day),
                      status or {'ranking_complete':True,'selected':[],'ranking':[]})
    def test_all_stocks_recorded_and_features_persist(self):
        self.record(date(2023,3,1));state=Journal(self.tmp.name).state()
        self.assertEqual(state['total'],30)
        a=next(r for r in state['records'] if r['symbol']=='AAPL')
        self.assertEqual(a['reason'],'Outside this cycle’s candidate quote allocation')
        self.assertIsNone(a['quote_usd']);self.assertTrue(a['features']['eligible'])
        self.assertEqual(a['outcomes'],{})
    def test_outcomes_arrive_only_after_completed_future_bars(self):
        first=date(2023,3,1);self.record(first)
        self.record(first+timedelta(days=1))
        old=next(r for r in self.j.state()['records'] if r['symbol']=='AAPL' and r['day']==first.isoformat())
        self.assertEqual(old['outcomes'],{})
        self.record(first+timedelta(days=22))
        old=next(r for r in self.j.state()['records'] if r['symbol']=='AAPL' and r['day']==first.isoformat())
        self.assertEqual(set(old['outcomes']),{'1','5','20'})
        self.assertEqual(old['outcomes']['1']['date'],'2023-03-02')
        self.assertAlmostEqual(old['outcomes']['1']['return_pct'],(160/158-1)*100)
    def test_filter_failure_explained(self):
        a=self.audit(date(2023,3,1))
        for row in a['bars']['AAPL']:row['volume']=0
        self.j.record('2023-03-01T15:00:00+00:00','2023-03-01',a,{'ranking_complete':True})
        r=next(r for r in self.j.state()['records'] if r['symbol']=='AAPL')
        self.assertIn('Dollar volume',r['reason']);self.assertFalse(r['features']['eligible'])
    def test_scan_failure_and_empty_data_not_fabricated(self):
        self.j.record('2023-03-01T15:00:00+00:00','2023-03-01',
            {'bars':{},'prices':{},'decisions':[],'before':{'positions':{}}},{},'Held quote missing')
        self.assertTrue(all(r['action']=='NOT_EVALUATED' and not r['features'] for r in self.j.state()['records']))
    def test_journal_is_separate_from_paper_database(self):
        self.record(date(2023,3,1))
        self.assertFalse((Path(self.tmp.name)/'paper.sqlite3').exists())

class ScannerJournalTests(unittest.TestCase):
    setUp=test_ranked.RankedScannerTests.setUp
    tearDown=test_ranked.RankedScannerTests.tearDown
    scan=test_ranked.RankedScannerTests.scan
    def test_actual_buys_and_position_limit_are_recorded(self):
        self.scan();self.scan();r=self.scanner.journal.state()['records'][:30]
        self.assertEqual(sum(x['action']=='BUY' for x in r),5)
        self.assertEqual(sum(x['reason']=='Five-position limit' for x in r),1)
        self.assertEqual(len(r),30)
    def test_journal_failure_does_not_change_trades(self):
        self.scan()
        with patch.object(self.scanner.journal,'record',side_effect=OSError('disk full')):self.scan()
        self.assertEqual(len(self.ledger.read()['positions']),5)
        self.assertIn('journal write failed',' '.join(self.scanner.status['skipped']))
