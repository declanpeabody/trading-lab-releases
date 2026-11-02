"""Isolated forward portfolios: same observations, replacement is the only rule difference.

No network, active ledger, configuration, promotion or retrospective replay.
"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from engine import new_paper_state, positive, signal
from research import ExperimentLedger


class ComparisonLedger(ExperimentLedger):
    max_positions=5
    purchase_cap=1000

    def valid_purchase_limit(self,value):
        return type(value) is int and 1<=value<=1000


class PortfolioComparison:
    def __init__(self,directory):
        self.path=Path(directory)/'portfolio-comparison.sqlite3'
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS comparison(id INTEGER PRIMARY KEY,body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS snapshots(at TEXT PRIMARY KEY,body TEXT NOT NULL)')
            initial=dict(schema=1,started=None,last=None,checks=0,misses=0,days=[],message='Awaiting complete shared snapshots',
                books={name:new_paper_state(10000)|{'paused':False} for name in ('without_replacement','with_replacement')},
                closed={name:0 for name in ('without_replacement','with_replacement')},benchmark=None,curves=[])
            db.execute('INSERT OR IGNORE INTO comparison VALUES(1,?)',(json.dumps(initial),))
        self.path.chmod(0o600)

    def state(self):
        with closing(sqlite3.connect(self.path)) as db:
            state=json.loads(db.execute('SELECT body FROM comparison WHERE id=1').fetchone()[0])
        return self.describe(state)

    @staticmethod
    def describe(state):
        metrics={}
        for name,b in state['books'].items():
            marked=b['equity']-b['cash']
            # Same 0.25% price and 0.15% FX allowances as the active engine.
            liquidation=b['cash']+marked*(1-.0025)*(1-.0015)
            metrics[name]=dict(equity_gbp=b['equity'],cash_gbp=b['cash'],liquidation_gbp=liquidation,
                return_pct=100*(liquidation/10000-1),exposure_pct=100*marked/b['equity'],
                closed_trades=state['closed'][name],positions=list(b['positions']),max_drawdown_pct=b.get('comparison_drawdown',0))
        return state|dict(coverage_note='Completed / attempted comparison snapshots only; excludes Mac downtime and scheduled optional-stage deferrals. Not full-session coverage.',metrics=metrics,coverage=state['checks']/max(1,state['checks']+state['misses']),automatic_deployment=False,
            note='Prospective £10,000 books, £1,000 purchase cap, five holdings; same fresh snapshots, candidates, user buy block, engine exits and modelled costs. Replacement is the only rule difference. Both freeze on missing data. Candidate stream is scanner-selected, not whole-market coverage. Returns mark an immediate liquidation including exit costs. No deposits or active-account writes. Short samples cannot establish profitability. Last 5,000 snapshots retained; cumulative checks and daily curves retained. Benchmark uses prior observed baseline exposure, GBP SPY price moves and zero cash interest; no dividends, taxes or benchmark dealing costs. Benchmark comparisons are valid only across its separately recorded endpoints.')

    def change(self,fn):
        with closing(sqlite3.connect(self.path,timeout=2)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            state=json.loads(db.execute('SELECT body FROM comparison WHERE id=1').fetchone()[0])
            fn(state,db)
            db.execute('UPDATE comparison SET body=? WHERE id=1',(json.dumps(state,allow_nan=False),))

    def holdings(self):
        return sorted({symbol for b in self.state()['books'].values() for symbol in b['positions']})

    def miss(self,reason):
        self.change(lambda s,db:s.update(misses=s['misses']+1,message=reason))

    def observe(self,prices,bars,fx,day,stamp,entries,coverage,buy_block=False,benchmark=None):
        def update(s,db):
            at=datetime.fromisoformat(stamp)
            if at.tzinfo is None:raise ValueError('Timestamp requires timezone')
            if s['last'] and at<=datetime.fromisoformat(s['last']):return
            required={x for b in s['books'].values() for x in b['positions']}|set(entries)
            if not required or not required.issubset(prices) or not required.issubset(bars):
                s.update(misses=s['misses']+1,message='Incomplete shared snapshot; both portfolios held')
                return
            histories={x:[float(r['close']) for r in bars[x]] for x in required}
            for x in required:signal(histories[x],prices[x])
            if any(len(bars[x])!=50 or bars[x][-1]['datetime'][:10]>=day for x in required):raise ValueError('Incomplete/future history')
            copies={};decisions={}
            for name,body in s['books'].items():
                ledger=ComparisonLedger(body);ledger.body['buy_block']=bool(buy_block)
                symbols=list(body['positions'])+[x for x in entries if x not in body['positions']]
                if not symbols:
                    copies[name]=ledger.read();decisions[name]=[];continue
                decisions[name]=ledger.cycle(prices,histories,fx,day,stamp,symbols=symbols,history_rows=bars,purchase_limit=1000,
                    replacement_candidates=entries if name=='with_replacement' else None,replacement_coverage=coverage) or []
                if ledger.read().get('error'):
                    s.update(misses=s['misses']+1,message='Basis verification failed; both portfolios held');return
                copies[name]=ledger.read()
            # Commit both computed books and their exact opportunity snapshot together.
            for name,b in copies.items():
                value=b['cash']+(b['equity']-b['cash'])*(1-.0025)*(1-.0015)
                peak=max(b.get('comparison_peak',10000),value)
                b.update(comparison_peak=peak,comparison_drawdown=max(b.get('comparison_drawdown',0),100*(1-value/peak)))
                s['closed'][name]+=sum(d['action']=='SELL' for d in decisions[name])
            s.update(books=copies,started=s['started'] or stamp,last=stamp,checks=s['checks']+1,message='Both portfolios evaluated on the same snapshot')
            if day not in s['days']:s['days'].append(day)
            metrics=self.describe(s)['metrics']
            s['curves']=[r for r in s['curves'] if r['day']!=day][-999:]+[dict(day=day,at=stamp,metrics=metrics)]
            if benchmark is not None:
                price,rate,observed=benchmark
                # The scanner collected this validated benchmark in this optional cycle.
                if not 0<=(at-datetime.fromisoformat(observed)).total_seconds()<=120:raise ValueError('Stale comparison benchmark')
                mark=positive(price)*positive(rate);b=s['benchmark'];base=metrics['without_replacement']
                if b and datetime.fromisoformat(observed)>datetime.fromisoformat(b['at']):
                    b['nav']*=1+b['weight']*(mark/b['price_gbp']-1)
                    b['observations']+=1
                elif not b:
                    b=dict(start=observed,nav=1.,observations=1,portfolio_start={k:v['liquidation_gbp'] for k,v in metrics.items()})
                b.update(at=observed,price_gbp=mark,weight=base['exposure_pct']/100,return_pct=100*(b['nav']-1),
                    portfolio_return_pct={k:100*(v['liquidation_gbp']/b['portfolio_start'][k]-1) for k,v in metrics.items()})
                s['benchmark']=b
            snapshot=dict(at=stamp,day=day,prices={x:prices[x] for x in required},bars={x:bars[x] for x in required},
                fx=fx,entries=entries,coverage=coverage,buy_block=buy_block,decisions=decisions,metrics=metrics)
            db.execute('INSERT INTO snapshots VALUES(?,?)',(stamp,json.dumps(snapshot,allow_nan=False)))
            db.execute('DELETE FROM snapshots WHERE at NOT IN (SELECT at FROM snapshots ORDER BY at DESC LIMIT 5000)')
        self.change(update)
