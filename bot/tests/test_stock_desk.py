import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from research import StockDesk, company_facts, price_context, return_correlation, Research
from ranked_backtest import stress_test, run, validate_dividends
from test_ranked import fixture

NOW=datetime(2026,10,20,23,tzinfo=timezone.utc)

def facts():
    tags={}
    def add(tag,unit,value,start=None,end='2025-12-31',filed='2026-02-01'):
        row=dict(val=value,end=end,filed=filed,form='10-K',accn='0000000001-26-000001')
        if start:row['start']=start
        tags.setdefault(tag,{'units':{unit:[]}})['units'][unit].append(row)
    for end,start,value,filed in [('2024-12-31','2024-01-01',100,'2025-02-01'),('2025-12-31','2025-01-01',120,'2026-02-01')]:
        add('Revenues','USD',value,start,end,filed)
    for tag,value in [('NetIncomeLoss',12),('NetCashProvidedByUsedInOperatingActivities',30),('PaymentsToAcquirePropertyPlantAndEquipment',10)]:add(tag,'USD',value,'2025-01-01')
    add('EarningsPerShareDiluted','USD/shares',2,'2025-01-01')
    add('LongTermDebtCurrent','USD',5);add('LongTermDebtNoncurrent','USD',15);add('CashAndCashEquivalentsAtCarryingValue','USD',7)
    add('CommonStockSharesOutstanding','shares',50,end='2026-06-30',filed='2026-08-01')
    add('CommonStockSharesOutstanding','shares',40,end='2025-06-30',filed='2025-08-01')
    return dict(cik=1,entityName='Example',facts={'us-gaap':tags})

def bars(multiplier=1):
    start=NOW.date()-timedelta(days=75);days=[start+timedelta(days=i) for i in range(75) if (start+timedelta(days=i)).weekday()<5][-50:]
    return [dict(datetime=d.isoformat(),close=100+multiplier*i+(i%3)*.1,volume=20000) for i,d in enumerate(days)]

class CompanyFactsTests(unittest.TestCase):
    def test_periods_growth_and_cashflow(self):
        f=company_facts(facts(),1,NOW)
        self.assertAlmostEqual(f['metrics']['revenue_growth_pct'],20)
        self.assertEqual(f['metrics']['net_margin_pct'],10)
        self.assertEqual(f['metrics']['free_cash_flow_usd'],20)
        self.assertEqual(f['metrics']['debt_usd'],20)
        self.assertEqual(f['metrics']['share_growth_pct'],25)
        self.assertEqual(f['records']['revenue'][0]['accession'],'0000000001-26-000001')
    def test_future_same_day_and_quarterly_facts_excluded(self):
        raw=facts();rs=raw['facts']['us-gaap']['Revenues']['units']['USD']
        rs.extend([dict(rs[-1],val=999,filed='2026-10-20'),dict(rs[-1],val=888,filed='2026-10-21'),dict(rs[-1],val=777,start='2025-10-01')])
        self.assertEqual(company_facts(raw,1,NOW)['records']['revenue'][0]['value'],120)
    def test_misaligned_cashflow_and_margin_unknown(self):
        raw=facts();raw['facts']['us-gaap']['PaymentsToAcquirePropertyPlantAndEquipment']['units']['USD'][0]['end']='2025-12-30'
        raw['facts']['us-gaap']['NetIncomeLoss']['units']['USD'][0]['end']='2025-12-30'
        f=company_facts(raw,1,NOW);self.assertIsNone(f['metrics']['free_cash_flow_usd']);self.assertIsNone(f['metrics']['net_margin_pct'])
    def test_identity_nonfinite_and_unknown_debt(self):
        with self.assertRaises(ValueError):company_facts(facts(),2,NOW)
        raw=facts();raw['facts']['us-gaap']['LongTermDebtCurrent']['units']['USD'][0]['val']=float('nan')
        # Invalid floats are not JSON evidence; reject rather than writing corrupt data.
        with self.assertRaises(ValueError):company_facts(raw,1,NOW)
        del raw['facts']['us-gaap']['LongTermDebtCurrent']
        self.assertIsNone(company_facts(raw,1,NOW)['metrics']['debt_usd'])
    def test_restatement_snapshot_and_rate_limit(self):
        with tempfile.TemporaryDirectory() as d:
            desk=StockDesk(d);client=Mock();client.get.return_value=facts()
            desk.refresh('TEST',1,client,NOW);self.assertFalse(desk.refresh('TEST',1,client,NOW+timedelta(hours=23)))
            revised=facts();revised['facts']['us-gaap']['Revenues']['units']['USD'][-1].update(val=140,filed='2026-10-20')
            client.get.return_value=revised;desk.refresh('TEST',1,client,NOW+timedelta(days=1))
            with desk.connect() as db:rows=db.execute('SELECT body FROM facts ORDER BY seen').fetchall()
            self.assertEqual(len(rows),2);self.assertEqual(json.loads(rows[0][0])['records']['revenue'][0]['value'],120)
            self.assertEqual(json.loads(rows[1][0])['records']['revenue'][0]['value'],140)
            client.get.side_effect=ValueError('SECRET');desk.refresh('TEST',1,client,NOW+timedelta(days=2))
            state=desk.state({},now=NOW+timedelta(days=2));self.assertNotIn('SECRET',json.dumps(state));self.assertIn('unavailable',state['cards'][0]['refresh']['error'])

class DeskTests(unittest.TestCase):
    def test_price_validation_and_correlation(self):
        a=price_context(bars(),NOW.date());b=price_context(bars(2),NOW.date())
        self.assertTrue(a['usable']);self.assertGreater(return_correlation(a,b),.8)
        self.assertFalse(price_context(bars()+bars()[-1:],NOW.date())['usable'])
        self.assertFalse(price_context(bars(),NOW.date()+timedelta(days=10))['usable'])
        bad=bars();bad[-1]['close']=10000;self.assertFalse(price_context(bad,NOW.date())['usable'])
        other=copy.deepcopy(b);other['dates'][0]='2000-01-01';self.assertIsNone(return_correlation(a,other))
    def test_empty_state_has_no_invented_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            state=StockDesk(d).state({'positions':{'TEST':{'qty':1}},'cash':100,'equity':100},now=NOW)
            card=state['cards'][0];self.assertIsNone(card['fundamentals']);self.assertEqual(card['valuation'],{})
            self.assertIsNone(card['context']['market_relative_20_pct']);self.assertIn('Upcoming event coverage unknown',card['missing'])
    def test_portfolio_peer_context_valuation_and_no_account_changes(self):
        with tempfile.TemporaryDirectory() as d:
            desk=StockDesk(d);data={f'T{i}':bars(i+1) for i in range(22)}
            Path(d,'ranked-daily.json').write_text(json.dumps({'bars':data}))
            for s in data:
                desk.profile(s,{'sic':'3571','sicDescription':'Computers'},NOW)
                client=Mock();client.get.return_value=facts();desk.refresh(s,1,client,NOW)
            account=dict(positions={'T0':{'qty':1}},marks={'T0':150},fx=.8,equity=1000,cash=880,account_limits={'max_positions':1,'purchase_cap':200})
            original=copy.deepcopy(account);state=desk.state(account,now=NOW);self.assertEqual(account,original)
            c=next(c for c in state['cards'] if c['symbol']=='T1')
            self.assertEqual(c['context']['peer_count'],21);self.assertIsNotNone(c['context']['market_relative_20_pct'])
            self.assertIn('No free position slots',c['portfolio_fit']['flags']);self.assertIsNotNone(c['valuation']['annual_pe'])
            self.assertEqual(c['valuation_peers']['annual_pe']['peer_count'],21)
            self.assertIn('High observed correlation with a holding',c['portfolio_fit']['flags'])
    def test_own_valuation_history_is_prospective_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as d:
            desk=StockDesk(d);client=Mock();client.get.return_value=facts()
            Path(d,'ranked-daily.json').write_text(json.dumps({'bars':{'TEST':bars()}}))
            desk.refresh('TEST',1,client,NOW)
            desk.refresh('TEST',1,client,NOW+timedelta(days=1))
            state=desk.state({},now=NOW+timedelta(days=1));card=state['cards'][0]
            self.assertEqual(len(card['valuation_history']),1)
            self.assertIsNone(card['valuation_history_summary']['annual_pe_median'])
            self.assertEqual(desk.state({},now=NOW-timedelta(days=1))['facts_covered'],0)
    def test_negative_eps_not_cheap_and_unsupported_facts_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            raw=facts();raw['facts']['us-gaap']['EarningsPerShareDiluted']['units']['USD/shares'][0]['val']=-2
            client=Mock();client.get.return_value=raw;desk=StockDesk(d);desk.refresh('TEST',1,client,NOW)
            Path(d,'ranked-daily.json').write_text(json.dumps({'bars':{'TEST':bars()}}))
            self.assertNotIn('annual_pe',desk.state({},now=NOW)['cards'][0]['valuation'])
    def test_calendar_verification_revision_and_invalid_source(self):
        with tempfile.TemporaryDirectory() as d:
            desk=StockDesk(d);payload=dict(symbol='TEST',at='2026-11-01T21:00:00Z',source='https://example.com/ir',verified=True)
            one=desk.save_event(payload,NOW);two=desk.save_event(payload|dict(id=one['id'],at='2026-11-02T21:00:00Z',verified=False),NOW+timedelta(hours=1))
            self.assertEqual(two['previous_at'],one['at']);self.assertEqual(two['revision'],2);self.assertEqual(two['verification'],'Unverified')
            with desk.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM calendar_history').fetchone()[0],2)
            for source in ['javascript:alert(1)','https://a:b@example.com']:
                with self.assertRaises(ValueError):desk.save_event(payload|dict(source=source),NOW)
    def test_guidance_diff_and_accession_deduplication(self):
        with tempfile.TemporaryDirectory() as d:
            news={'events':[dict(symbol='TEST',accession=str(i),first_seen=str(i),document={'evidence':[{'term':'guidance','excerpt':text}]}) for i,text in [(1,'old'),(2,'new'),(2,'new')]]}
            c=StockDesk(d).state({'positions':{'TEST':{}}},news,NOW)['cards'][0]
            self.assertEqual(len(c['catalysts']),2);self.assertTrue(c['guidance']['text_changed'])

class StressTests(unittest.TestCase):
    def series(self,flat=False):
        data,a,b=fixture(flat);data['SPY']=copy.deepcopy(data['AAPL']);return data,a,b
    def test_stress_capital_costs_benchmark_and_accounting(self):
        data,a,b=self.series();r=stress_test(data,a,b)
        self.assertEqual(r['initial_gbp'],10000);self.assertIn('spy',r['comparisons']);self.assertEqual(len(r['stress']['cases']),3)
        for p in r['comparisons'].values():self.assertAlmostEqual(10000+sum(t['net_gbp'] for t in p['trades']),p['metrics']['final_gbp'])
        strategy=r['comparisons']['strategy'];self.assertTrue(all(-t['net_gbp']<=1000 for t in strategy['trades'] if t['side']=='BUY'))
        self.assertTrue(all(p['positions']<=5 for p in strategy['curve']))
    def test_spread_cost_on_flat_hold(self):
        data,a,b=self.series(True);zero=run(data,a,b,0,0,spread_bps=0);spread=run(data,a,b,0,0,spread_bps=100)
        self.assertLess(spread['comparisons']['equal_weight']['metrics']['final_gbp'],zero['comparisons']['equal_weight']['metrics']['final_gbp'])
    def test_dividend_entitlement_payment_and_receivable(self):
        data,a,b=self.series(True);days=sorted(data['AAPL']);ex=days[60];pay=days[65]
        event=dict(symbol='AAPL',ex_date=ex,payment_date=pay,amount_usd=1,source='https://example.com/dividend')
        r=run(data,a,b,0,0,dividends=[event]);p=r['comparisons']['equal_weight'];payments=[t for t in p['trades'] if t['side']=='DIVIDEND']
        self.assertEqual(len(payments),1);self.assertEqual(payments[0]['date'],pay)
        self.assertAlmostEqual(p['metrics']['final_gbp'],1000+payments[0]['net_gbp'])
        accrued=next(c for c in p['curve'] if c['date']==ex)['dividend_receivable_gbp'];self.assertGreater(accrued,0)
        # A purchase on ex-date does not receive the dividend.
        r=run(data,a,b,0,0,dividends=[event|{'ex_date':a}]);self.assertFalse(any(t['side']=='DIVIDEND' for t in r['comparisons']['equal_weight']['trades']))
        r=run(data,a,b,0,0,dividends=[event|{'payment_date':'2023-05-01'}]);p=r['comparisons']['equal_weight']
        self.assertAlmostEqual(p['metrics']['final_gbp'],1000+p['curve'][-1]['dividend_receivable_gbp'])
    def test_future_prices_do_not_change_prior_orders_and_gap_visible(self):
        data,a,b=self.series();other=copy.deepcopy(data);day=sorted(data['AAPL'])[80];other['AAPL'][day]['open']=1
        before=lambda r:[t for t in r['comparisons']['strategy']['trades'] if t['date']<day]
        self.assertEqual(before(stress_test(data,a,b)),before(stress_test(other,a,b)))
        self.assertEqual(stress_test(other,a,b)['stress']['largest_gaps'][0]['symbol'],'AAPL')
    def test_reject_invalid_dividends_and_missing_benchmark(self):
        data,a,b=self.series();del data['SPY'][a]
        with self.assertRaises(ValueError):stress_test(data,a,b)
        with self.assertRaises(ValueError):validate_dividends([{'symbol':'AAPL','ex_date':a,'payment_date':b,'amount_usd':float('nan'),'source':'https://example.com'}],a,b)
    def test_start_validation_before_background_work(self):
        with tempfile.TemporaryDirectory() as d,patch('research.threading.Thread') as thread:
            with self.assertRaises(ValueError):Research(d).start(dict(mode='stress',start='2023-01-01',end='2023-12-31',spread_bps=float('nan')))
            thread.assert_not_called()

class DeskIntegrationTests(unittest.TestCase):
    def test_news_fetches_at_most_two_facts_per_poll_and_reuses_day_cache(self):
        from news import News
        class Client:
            calls=[]
            def __init__(self,contact):pass
            def get(self,url):
                self.calls.append(url)
                if 'company_tickers' in url:return {str(i):{'ticker':s,'cik_str':1} for i,s in enumerate(('AAPL','MSFT','AMZN'))}
                if '/companyfacts/' in url:return facts()
                return dict(cik=1,name='Company',sic='3571',filings={'recent':dict(accessionNumber=[],form=[],filingDate=[],primaryDocument=[])})
        with tempfile.TemporaryDirectory() as d:
            news=News(d,Client);news.config.update(enabled=True,contact='test@example.com')
            news.poll({});self.assertEqual(sum('/companyfacts/' in u for u in Client.calls),2)
            news.info['last_attempt']=None;news.poll({});self.assertEqual(sum('/companyfacts/' in u for u in Client.calls),3)
            news.db.close()
    def test_stress_job_collects_spy_and_persists_complete_report(self):
        from strategy import WATCHLIST
        data,a,b=fixture();data['SPY']=copy.deepcopy(data['AAPL'])
        class Market:
            def get(self,endpoint,**kw):
                if endpoint=='stocks':return {'data':[dict(symbol=s,exchange='NASDAQ',country='United States',currency='USD',type='Common Stock',name=s) for s in WATCHLIST]}
                symbol=kw['symbol'];return dict(meta=dict(symbol=symbol,currency='USD',exchange='NYSE Arca' if symbol=='SPY' else 'NASDAQ'),values=[dict(datetime=d,**r) for d,r in data[symbol].items()])
        with tempfile.TemporaryDirectory() as d,patch('research.Market',Market):
            research=Research(d);research._job(a,b,25,15,False,'stress')
            self.assertIsNone(research.error);self.assertIn('spy',research.latest['comparisons']);self.assertIn('SPY',research.latest['provenance'])
            self.assertEqual(Research(d).latest['stress']['cases'],research.latest['stress']['cases'])
