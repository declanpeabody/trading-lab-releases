"""Background research jobs; separate files from the active paper account."""
import csv
from contextlib import contextmanager, closing
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import threading
from ranked_backtest import dates, parse_series, run, walk_forward, run_batched
from strategy import WATCHLIST, screening_universe
from scanner import catalogue
from market import Market, MarketUnavailable, BudgetDeferred
from mac_update import prune_files

class Research:
    def __init__(self, directory):
        self.directory=Path(directory)/'research';self.directory.mkdir(parents=True,exist_ok=True)
        self.lock=threading.Lock();self.busy=False;self.error=None
        self.latest=None
        path=self.directory/'latest.json'
        if path.exists():
            try:self.latest=json.loads(path.read_text())
            except (OSError,ValueError):self.error='Previous report could not be read; run a new test.'

    def state(self):
        with self.lock:return {'running':self.busy,'error':self.error,'report':self.latest}

    def start(self, options):
        if not isinstance(options,dict):raise ValueError('Invalid research options.')
        start,end=str(options.get('start','')),str(options.get('end',''))
        mode=options.get('mode','historical')
        if mode not in ('historical','walk_forward','scanner_replay'):raise ValueError('Unknown research mode')
        dates(start,end)
        slip=float(options.get('slippage_bps',25));fee=float(options.get('fx_fee_bps',15))
        if not (0<=slip<=200 and 0<=fee<=200):raise ValueError('Cost allowances must be 0–200 basis points.')
        with self.lock:
            if self.busy:raise ValueError('A historical test is already running.')
            self.busy=True;self.error=None
        threading.Thread(target=self._job,args=(start,end,slip,fee,options.get('refresh') is True,mode),daemon=True).start()

    def _job(self,start,end,slip,fee,refresh,mode="historical"):
        try:
            a,b=dates(start,end);warm=(a-timedelta(days=150)).isoformat()
            data={};provenance={};market=Market()
            mapping=catalogue(market.get('stocks',purpose='research',country='United States',type='Common Stock')['data'])
            if mode!='scanner_replay' and not all(s in mapping for s in WATCHLIST):raise ValueError('Watchlist exchange mappings incomplete; no test run.')
            symbols=screening_universe(mapping) if mode=='scanner_replay' else WATCHLIST
            unavailable={}
            for symbol in (*symbols,'USD/GBP'):
                token=hashlib.sha256(f'{symbol}|{mapping.get(symbol)}|{warm}|{end}|splits|rank-v1'.encode()).hexdigest()
                cache=self.directory/(token+'.cache.json')
                try:
                    if cache.exists() and not refresh:
                        bundle=json.loads(cache.read_text())
                    else:
                        extra={'exchange':mapping[symbol]} if symbol in mapping else {}
                        raw=market.get('time_series',purpose='research',symbol=symbol,**extra,interval='1day',start_date=warm,
                            end_date=end,outputsize=5000,adjust='splits',order='asc')
                        parse_series(raw,symbol)
                        if symbol in mapping and raw.get('meta',{}).get('exchange')!=mapping[symbol]:raise ValueError('Historical exchange mismatch')
                        bundle={'retrieved_at':datetime.now(timezone.utc).isoformat(),'raw':raw}
                        self._save(cache,bundle)
                    data[symbol]=parse_series(bundle['raw'],symbol)
                    provenance[symbol]={'retrieved_at':bundle['retrieved_at'],
                        'sha256':hashlib.sha256(json.dumps(bundle['raw'],sort_keys=True).encode()).hexdigest()}
                except (MarketUnavailable,BudgetDeferred):raise
                except (ValueError,KeyError,TypeError) as exc:
                    if mode!='scanner_replay' or symbol=='USD/GBP' or 'Local daily API budget' in str(exc):raise
                    data[symbol]={};unavailable[symbol]=str(exc)[:200]
            report=run_batched(data,mapping,start,end,slip,fee) if mode=='scanner_replay' else run(data,start,end,slip,fee)
            if mode=='scanner_replay':report['unavailable_histories']=unavailable
            if mode=="walk_forward":
                report["walk_forward"]=walk_forward(data,start,end,slip,fee)
                report['warnings']=[w for w in report['warnings'] if 'No optimisation' not in w]
                report['warnings'].append('The main chart is the fixed-rule full-period comparison. Purchase-size selection and reserved-test results are reported separately above; they do not alter the main curve or paper strategy.')
            report["research_mode"]=mode
            report.update(created_at=datetime.now(timezone.utc).isoformat(),data_source='Twelve Data daily bars',provenance=provenance)
            self._save(self.directory/'latest.json',report)
            self._save(self.directory/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.report.json'),report)
            with self.lock:self.latest=report
        except (ValueError,KeyError,TypeError) as exc:
            # Only our validation errors are shown; raw API bodies/URLs are never exposed.
            message=str(exc) if isinstance(exc,ValueError) else 'Historical data format is incomplete or unexpected.'
            with self.lock:self.error=message[:300]
        except Exception:
            with self.lock:self.error='Research failed; check the data connection and available disk space. Paper account unchanged.'
        finally:
            try:
                prune_files(self.directory,'*.report.json',10,50_000_000)
                prune_files(self.directory,'*.cache.json',400,200_000_000)
            except OSError:pass
            with self.lock:self.busy=False

    @staticmethod
    def _save(path,value):
        temp=path.with_suffix(path.suffix+'.tmp')
        temp.write_text(json.dumps(value,allow_nan=False));temp.chmod(0o600);temp.replace(path)

    def export(self,kind):
        with self.lock:r=self.latest
        if r is None:raise ValueError('Run a historical test first.')
        if kind=='json':return json.dumps(r,indent=2,allow_nan=False),'application/json'
        output=io.StringIO();writer=csv.writer(output)
        if kind=='equity':
            names=list(r['comparisons'])
            writer.writerow(['date']+[n+'_gbp' for n in names])
            for i,p in enumerate(r['comparisons']['strategy']['curve']):
                writer.writerow([p['date']]+[r['comparisons'][n]['curve'][i]['equity'] for n in names])
        elif kind=='trades':
            fields=['date','symbol','side','quantity','price_usd','fx_gbp_per_usd','net_gbp','cost_gbp','pnl_gbp','reason']
            writer.writerow(['portfolio']+fields)
            for name,portfolio in r['comparisons'].items():
                for t in portfolio['trades']:writer.writerow([name]+[t[k] for k in fields])
        else:raise ValueError('Unknown export format.')
        return output.getvalue(),'text/csv; charset=utf-8'


# Controlled forward experiments. Parameter values and gates are code constants;
# no generated code, arbitrary expressions, broker execution or editable risk caps.
EXPERIMENT_SIZES=(100,150,200)
EXPERIMENT_SESSIONS=30
EXPERIMENT_MIN_CLOSES=10
EXPERIMENT_EDGE_PCT=1.0
EXPERIMENT_SCHEMA=1


def experiment_metrics(book, curve, closes):
    """Comparable after-cost liquidation marks; no simulated trades added."""
    from engine import PAPER_CAPITAL, PRICE_ALLOWANCE, FX_ALLOWANCE
    held_value=sum(p['qty']*book.get('marks',{}).get(s,0)*(book.get('fx') or 0) for s,p in book['positions'].items())
    equity=book['cash']+held_value*(1-PRICE_ALLOWANCE)*(1-FX_ALLOWANCE)
    peak=PAPER_CAPITAL;dd=book.get("experiment_drawdown",0)
    for value in [PAPER_CAPITAL,*curve,equity]:
        peak=max(peak,value);dd=max(dd,(peak-value)/peak*100)
    return {'equity':equity,'return_pct':(equity/PAPER_CAPITAL-1)*100,'drawdown_pct':dd,'closed_trades':closes}


def experiment_gate(candidate,reference):
    return (candidate['closed_trades']>=EXPERIMENT_MIN_CLOSES and reference['closed_trades']>=EXPERIMENT_MIN_CLOSES
        and candidate['return_pct']>=max(0,reference['return_pct'])+EXPERIMENT_EDGE_PCT
        and candidate['drawdown_pct']<=reference['drawdown_pct']+1e-9)


from engine import Ledger as PaperLedger


class ExperimentLedger(PaperLedger):
    """Use the real paper rules on an isolated in-memory book; persist as one cohort."""
    def __init__(self,body):
        import copy
        from engine import PRICE_ALLOWANCE, FX_ALLOWANCE
        self.body=copy.deepcopy(body);self.lock=threading.RLock()
        self.price_allowance=PRICE_ALLOWANCE;self.fx_allowance=FX_ALLOWANCE
    def valid_purchase_limit(self,value):
        return type(value) is int and 1<=value<=200

    def read(self):
        import copy
        return copy.deepcopy(self.body)
    def write(self,state,messages=()):
        import copy
        state=copy.deepcopy(state)
        state['events']=(list(state.get('events',[]))+[{'at':state.get('last_check'),'message':message} for message in messages])[-200:]
        self.body=state


class Experiments:
    """One atomic SQLite row for all books, selection, gates and active profile."""
    def __init__(self,directory):
        import sqlite3
        from contextlib import closing
        self.path=Path(directory)/'experiments.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS experiment_state(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
            state={'schema':EXPERIMENT_SCHEMA,'enabled':True,'automatic':True,'active':200,'previous':200,
                'round':0,'phase':'waiting','message':'Waiting for complete fresh data; no experiments have traded.',
                'books':{},'curves':{},'closes':{},'sessions':[],'day':None,'pending':None,'reports':[],'events':[]}
            db.execute('INSERT OR IGNORE INTO experiment_state VALUES(1,?)',(json.dumps(state),))
        self.path.chmod(0o600)

    def _change(self,fn):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            state=json.loads(db.execute('SELECT body FROM experiment_state WHERE id=1').fetchone()[0])
            if state['schema']!=EXPERIMENT_SCHEMA or state['active'] not in EXPERIMENT_SIZES:raise ValueError('Experiment settings need review; unsafe profile rejected')
            result=fn(state)
            db.execute('UPDATE experiment_state SET body=? WHERE id=1',(json.dumps(state,allow_nan=False),))
            return result

    def state(self):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.path,timeout=10)) as db:
            s=json.loads(db.execute('SELECT body FROM experiment_state WHERE id=1').fetchone()[0])
        if s['schema']!=EXPERIMENT_SCHEMA or s['active'] not in EXPERIMENT_SIZES:raise ValueError('Invalid experiment profile')
        stats={k:experiment_metrics(b,s['curves'].get(k,[]),s['closes'].get(k,0)) for k,b in s['books'].items()}
        return s|{'metrics':stats,'qualified_sessions':sum(x['qualified'] for x in s['sessions']),
            'required_sessions':EXPERIMENT_SESSIONS,'min_closed_trades':EXPERIMENT_MIN_CLOSES,'edge_pct':EXPERIMENT_EDGE_PCT,
            'limits':{'capital':1000,'maximum_purchase':200,'maximum_positions':5,'stop_loss_pct':5,'total_loss_block_pct':10,'daily_loss_block_pct':3},
            'note':'Forward paper experiments only. £100/£150/£200 purchase settings share fresh quotes, candidate opportunities and modelled costs. Each phase resets separate accounts to £1,000. 30 qualifying development sessions select one challenger; 30 later sessions test it. A qualifying session needs 3 complete snapshots spanning at least 1 hour. Both phases require 95% complete observations, 10 closed trades per compared model, at least 1 percentage point more return than the incumbent and fixed £200 reference (a £200 challenger compares with the incumbent), a positive return and no greater observed drawdown. These are screening gates, not proof of profitability. Quotes for experiment holdings share the six non-held quote slots; candidate coverage may be reduced. Latest 200 account events per model and six phase reports are retained. No news or external bots are used as training data.'}

    @staticmethod
    def _event(s,message):
        s['events']=(s['events']+[{'at':datetime.now(timezone.utc).isoformat(),'message':message}])[-100:]

    @staticmethod
    def _reset_books(s):
        from engine import new_paper_state
        s.update(books={str(n):new_paper_state()|{'paused':False} for n in EXPERIMENT_SIZES},
            curves={str(n):[] for n in EXPERIMENT_SIZES},closes={str(n):0 for n in EXPERIMENT_SIZES},sessions=[],day=None,last_stamp=None,attempted_misses=0,restart_required=False,finished_day=None)

    def _new_round(self,s):
        self._reset_books(s)
        s.update(round=s['round']+1,phase='development',incumbent=s['active'],candidate=None,pending=None,
            message='Collecting prospective development observations. Active paper settings unchanged.')
        self._event(s,f"Round {s['round']} started; incumbent £{s['active']}, fixed reference £200.")

    def control(self,action,enabled=None):
        def change(s):
            if action in ('enabled','automatic'):
                if type(enabled) is not bool:raise ValueError('Control must be true or false')
                s[action]=enabled;self._event(s,f'{action}: {enabled}')
            elif action=='rollback':
                s['active'],s['previous']=s['previous'],s['active'];s['automatic']=False
                s.update(phase='complete',pending=None,message='Previous purchase setting restored; automatic promotion disabled. Existing holdings and safety flags unchanged.')
                self._event(s,f"Rollback to £{s['active']}; automatic promotion disabled.")
            elif action=='restart':
                self._new_round(s)
                self._event(s,'Evaluation restarted by user; discarded evidence cannot qualify a promotion.')
            else:raise ValueError('Unknown experiment control')
        self._change(change)

    def holdings(self):
        s=self.state()
        return sorted({symbol for b in s['books'].values() for symbol in b['positions']}) if s['enabled'] and s['phase'] in ('development','validation') else []

    def active_limit(self):return self.state()['active']

    def invalidate(self,message):
        def change(s):
            s.update(phase='complete',pending=None,restart_required=True,message=message)
            self._event(s,message)
        self._change(change)

    def miss(self,day):
        def change(s):
            if s['enabled'] and s['phase'] in ('development','validation'):
                s['attempted_misses']=s.get('attempted_misses',0)+1
                if s.get('day') and s['day']['date']==day:s['day']['missed']+=1
                s['message']='Incomplete experiment snapshot; all comparison accounts held together. Waiting for complete quotes and history.'
        self._change(change)

    def _finish_day(self,s):
        day=s['day']
        if not day:return
        day['qualified']=day['checks']>=3 and day['last']-day['first']>=3600
        s['sessions'].append(day);s['day']=None
        if len(s['sessions'])>120:
            s.update(phase='complete',restart_required=True,message='Evaluation stopped: insufficient complete observations within 120 observed sessions. No promotion.')
            self._event(s,s['message']);return
        if sum(d['qualified'] for d in s['sessions'])<EXPERIMENT_SESSIONS:return
        stats={k:experiment_metrics(b,s['curves'][k],s['closes'][k]) for k,b in s['books'].items()}
        checks=sum(d['checks'] for d in s['sessions']);misses=s.get('attempted_misses',0)
        coverage=checks/(checks+misses) if checks+misses else 0
        eligible=lambda key:coverage>=.95 and all(experiment_gate(stats[key],stats[str(ref)]) for ref in {200,s['incumbent']}-{int(key)})
        report={'round':s['round'],'phase':s['phase'],'start':s['sessions'][0]['date'],'end':day['date'],
            'incumbent':s['incumbent'],'candidate':s.get('candidate'),'metrics':stats,'coverage':coverage,
            'recent_events':{key:book.get('events',[]) for key,book in s['books'].items()},
            'sessions':list(s['sessions']),'eligible':[n for n in EXPERIMENT_SIZES if n!=s['incumbent'] and eligible(str(n))]}
        s['reports']=(s['reports']+[report])[-6:]
        if s['phase']=='development':
            eligible_sizes=report['eligible']
            if not eligible_sizes:
                s.update(phase='complete',message='Development gates not met; incumbent retained. Start a new evaluation when ready.')
                self._event(s,s['message']);return
            chosen=max(eligible_sizes,key=lambda n:(stats[str(n)]['return_pct']-.5*stats[str(n)]['drawdown_pct'],-n))
            self._reset_books(s)
            s.update(phase='validation',candidate=chosen,message=f'£{chosen} selected using development data only. Collecting a separate 30-session forward test.')
            self._event(s,s['message'])
        else:
            chosen=s['candidate']
            s.update(phase='complete',pending=chosen if eligible(str(chosen)) else None,
                message='Forward-test gates passed; promotion waits for automatic permission and an unblocked, flat active paper account.' if eligible(str(chosen)) else 'Forward-test gates failed; incumbent retained.')
            self._event(s,s['message'])

    def promote(self,ledger,today=None):
        def change(s):
            if not(s['enabled'] and s['automatic'] and s.get('pending')):return
            candidate=s['pending'];reports=s['reports'][-2:]
            evidence=(len(reports)==2 and [r['phase'] for r in reports]==['development','validation']
                and reports[0]['end']<reports[1]['start'] and reports[1]['candidate']==candidate
                and all(r['round']==s['round'] and candidate in r['eligible'] and r['coverage']>=.95
                    and sum(d['qualified'] for d in r['sessions'])>=EXPERIMENT_SESSIONS
                    and all(experiment_gate(r['metrics'][str(candidate)],r['metrics'][str(ref)])
                        for ref in {200,s['incumbent']}-{candidate}) for r in reports))
            if not evidence:
                s.update(pending=None,restart_required=True,message='Promotion evidence missing or inconsistent; incumbent retained.')
                self._event(s,s['message']);return
            observed=(today or datetime.now(timezone.utc).date().isoformat())
            if (datetime.fromisoformat(observed)-datetime.fromisoformat(s['reports'][-1]['end'])).days>7:
                s.update(pending=None,message='Promotion evidence expired after seven calendar days; incumbent retained.')
                self._event(s,s['message']);return
            with ledger.lock:
                live=ledger.read()
                if live['positions'] or any(live.get(k) for k in ('paused','buy_block','risk_latched','day_block','error')):return
                new=s['pending']
                if new not in EXPERIMENT_SIZES:raise ValueError('Unsafe promotion rejected')
                s['previous']=s['active'];s['active']=new;s['pending']=None
                s['message']=f'Automatically promoted £{new} purchases. Previous setting retained for rollback; account and risk flags unchanged.'
                self._event(s,s['message'])
        self._change(change)

    def observe(self,prices,bars,fx,day,stamp,entry_symbols,buy_block=False):
        from engine import signal, positive
        fx=positive(fx)
        timestamp=datetime.fromisoformat(stamp).timestamp()
        def change(s):
            if not s['enabled']:return
            if s['phase']=='waiting' or (s['phase']=='complete' and not s.get('pending') and not s.get('restart_required') and s.get('finished_day') and day>s['finished_day']):self._new_round(s)
            if s['phase'] not in ('development','validation'):return
            if s.get('last_stamp') and timestamp-s['last_stamp']<900:return
            if s.get('day') and s['day']['date']!=day:
                self._finish_day(s)
                if s['phase']=='complete':s['finished_day']=day
            if s['phase'] not in ('development','validation'):return
            required={symbol for book in s['books'].values() for symbol in book['positions']}|set(entry_symbols)
            if not prices or not required.issubset(prices) or not required.issubset(bars):
                s['attempted_misses']=s.get('attempted_misses',0)+1
                if s.get('day'):s['day']['missed']+=1
                s['message']='Waiting for complete shared experiment quotes and histories; no comparison account traded.'
                return
            histories={symbol:[float(row['close']) for row in bars[symbol]] for symbol in required}
            copies={}
            # Validate every book and compute every transition before the single commit.
            for key,body in s['books'].items():
                ledger=ExperimentLedger(body)
                ledger.body['buy_block']=bool(buy_block)
                symbols=list(body['positions'])+[x for x in entry_symbols if x not in body['positions']]
                if not symbols:
                    ledger.body.update(last_check=stamp,last_success=stamp,error=None,equity=ledger.body['cash'])
                    copies[key]=(ledger.read(),0);continue
                for symbol in symbols:signal(histories[symbol],prices[symbol])
                actions=ledger.cycle(prices,histories,fx,day,stamp,symbols=symbols,history_rows=bars,purchase_limit=int(key))
                if ledger.read().get('error'):
                    s.update(phase='complete',pending=None,restart_required=True,message='Experiment basis verification required; evaluation stopped without promotion. Restart after reviewing the data.')
                    self._event(s,s['message']);return
                copies[key]=(ledger.read(),sum(a['action']=='SELL' for a in actions or []))
            for key,(book,closed) in copies.items():
                s['books'][key]=book;s['closes'][key]+=closed
                value=experiment_metrics(book,[],0)['equity']
                peak=max(book.get('experiment_peak',1000),value)
                book['experiment_peak']=peak
                book['experiment_drawdown']=max(book.get('experiment_drawdown',0),(peak-value)/peak*100)
                # One latest mark per observed session plus intraday minimum preserves
                # drawdown evidence while bounding storage to 240 values per phase.
                if s.get('day'):
                    s['curves'][key][-2]=min(s['curves'][key][-2],value);s['curves'][key][-1]=value
                else:s['curves'][key].extend([value,value])
            if not s.get('day'):s['day']={'date':day,'checks':0,'missed':0,'first':timestamp,'last':timestamp}
            s['day']['checks']+=1;s['day']['last']=timestamp;s['last_stamp']=timestamp
            s['message']=f"Collecting {s['phase']} observations; settings remain fixed during this phase."
        self._change(change)


class FasterDailyLedger(ExperimentLedger):
    """Isolated 10/30-session comparison on the existing shared candidate stream."""
    def trading_signal(self,closes,price):
        from engine import signal
        signal(closes,price)  # Retain full snapshot validation.
        closes=[float(x) for x in closes];price=float(price)
        fast,slow=sum(closes[-10:])/10,sum(closes[-30:])/30
        return 'buy' if price>fast>slow else 'sell' if price<fast else 'hold'


class ShadowTrials:
    """Prospective, opt-in comparisons. No promotion or active-ledger reference."""
    NAMES=('baseline','allocation','filing_guard','concentration','confirmed_entry','volatility_size','trend_breadth','event_guard','faster_daily')
    def __init__(self,directory):
        import sqlite3
        self.directory=Path(directory);self.path=self.directory/'shadow-trials.sqlite3'
        self.directory.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS trials(id INTEGER PRIMARY KEY,body TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO trials VALUES(1,?)',(json.dumps(self.initial()),))
        self.path.chmod(0o600)
        def migrate(s):
            if set(s['books'])!=set(self.NAMES):
                s.update(enabled=False,invalid=True,message='New comparison models require a restart; previous books retained until you explicitly restart.')
        self.change(migrate)
    @classmethod
    def initial(cls):
        from engine import new_paper_state
        return dict(schema=1,enabled=False,started=None,last=None,checks=0,misses=0,days=[],message='Not started',
            books={n:new_paper_state()|{'paused':False} for n in cls.NAMES},closes={n:0 for n in cls.NAMES},
            decisions=[],invalid=False)
    def change(self,fn):
        import sqlite3
        with sqlite3.connect(self.path,timeout=10) as db:
            db.execute('BEGIN IMMEDIATE');s=json.loads(db.execute('SELECT body FROM trials WHERE id=1').fetchone()[0])
            if s.get('schema')!=1:raise ValueError('Unknown trial schema')
            value=fn(s);db.execute('UPDATE trials SET body=? WHERE id=1',(json.dumps(s,allow_nan=False),));return value
    def state(self):
        import sqlite3
        with sqlite3.connect(self.path) as db:s=json.loads(db.execute('SELECT body FROM trials WHERE id=1').fetchone()[0])
        metrics={k:experiment_metrics(b,[],s['closes'][k]) for k,b in s['books'].items()}
        baseline=metrics['baseline']
        gates={k:dict(sessions=len(s['days'])>=60,closed_trades=min(m['closed_trades'],baseline['closed_trades'])>=30,
            after_cost_outperformance=m['return_pct']>baseline['return_pct'],drawdown_no_worse=m['drawdown_pct']<=baseline['drawdown_pct'],
            coverage=s['checks']>=1 and s['misses']/max(1,s['checks']+s['misses'])<=.1,
            independent_holdout_verified=False,eligible_for_promotion=False) for k,m in metrics.items() if k!='baseline'}
        return s|{'metrics':metrics,'review_gates':gates,
            'review_note':'Screening thresholds: 60 observed sessions, 30 closed trades in each book, positive after-cost advantage, no worse observed drawdown and ≤10% missed snapshots. These are minimum review criteria, not statistical proof. Independent holdout review and explicit approval are always required; no automatic promotion.',
            'note':'Prospective shared-data paper comparisons, never promoted automatically. Baseline: £200 cap. Allocation: £5 modelled price-risk budget, £150 cap, £200 cash reserve, two new entries per cycle and 0.8 positive correlation filter. Filing guard: £200 cap, requires SEC coverage within one hour and defers entries with a risk-coded filing first seen within seven days. This is not a news sentiment model. No coverage means no filing-guard buy. All accounts freeze together on missing holdings data. Returns include modelled exit costs; gaps can exceed the modelled risk budget. Concentration: £200 cap, at most two of AAPL/MSFT/AMZN/GOOGL/GOOG/META/NVDA/TSLA plus pairwise correlation ≤0.8; unknown/misaligned histories defer entry. Confirmed entry: buy signal on consecutive complete observations 15–60 minutes apart on the same day, at most two new entries per cycle. Original exits retained except in faster_daily. These £1,000/£200 books do not represent the £10,000 active allocation. Volatility size: each entry capped at £5 divided by twice daily volatility (1% floor), maximum £200. Trend breadth: requires 30 aligned supplied histories, with at least 60% above their 50-session mean; this is partial scanner coverage, not a broad-market index. Event guard: defers entries within 48 hours before manually recorded events; missing calendar coverage remains unknown. Faster daily: 10/30-session entry and trend-exit rules, £200 cap, existing 5% observed-loss exit and same-day re-entry block. Uses the active scanner candidate stream, so this does not test all possible faster-strategy entries. No extra quote allowance. No claim of statistical significance.'}
    def control(self,action,enabled=None):
        def update(s):
            if action=='enabled' and type(enabled) is bool:
                if enabled and s['invalid']:raise ValueError('Restart invalid comparison before enabling')
                s['enabled']=enabled
            elif action=='restart':s.clear();s.update(self.initial());s['enabled']=True
            else:raise ValueError('Invalid trial control')
        self.change(update)
    def holdings(self):
        s=self.state();return sorted({x for b in s['books'].values() for x in b['positions']}) if s['enabled'] else []
    def invalidate(self,reason):
        self.change(lambda s:s.update(enabled=False,invalid=True,message=reason))
    def miss(self,reason):
        def update(s):
            if s['enabled']:s.update(misses=s['misses']+1,message=reason)
        self.change(update)
    def news_gate(self,symbol,stamp):
        import sqlite3
        from contextlib import closing
        at=datetime.fromisoformat(stamp)
        try:
            path=self.directory/'news'/'events.sqlite3'
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                info=json.loads(db.execute('SELECT body FROM meta WHERE id=1').fetchone()[0])
                checked=next(x['checked_at'] for x in info['coverage'] if x['symbol']==symbol and x.get('checked_at'))
                age=(at-datetime.fromisoformat(checked)).total_seconds()
                if not 0<=age<=3600:return False,'SEC coverage missing, future-dated or older than one hour'
                for row in db.execute("SELECT body FROM events WHERE json_extract(body,'$.symbol')=?",(symbol,)):
                    e=json.loads(row[0]);age=(at-datetime.fromisoformat(e['first_seen'])).total_seconds()
                    if 0<=age<=7*86400 and e.get('flag')=='Risk review':return False,'Recent SEC risk-coded filing; experimental entry deferred'
            return True,'Fresh SEC metadata coverage; no recent risk-coded filing observed'
        except (OSError,sqlite3.Error,ValueError,KeyError,TypeError,StopIteration):return False,'SEC coverage unavailable'
    @staticmethod
    def correlated(a,b,bars):
        import statistics
        x=bars.get(a,[]);y=bars.get(b,[])
        if len(x)!=50 or len(y)!=50 or [r['datetime'] for r in x]!=[r['datetime'] for r in y]:return True
        rx=[float(x[i]['close'])/float(x[i-1]['close'])-1 for i in range(1,50)]
        ry=[float(y[i]['close'])/float(y[i-1]['close'])-1 for i in range(1,50)]
        if statistics.pstdev(rx)==0 or statistics.pstdev(ry)==0:return True
        return statistics.correlation(rx,ry)>.8
    def observe(self,prices,bars,fx,day,stamp,entries,buy_block=False):
        from engine import signal
        from strategy import assess
        def update(s):
            if not s['enabled']:return
            if s['last'] and (datetime.fromisoformat(stamp)-datetime.fromisoformat(s['last'])).total_seconds()<900:return
            required={x for b in s['books'].values() for x in b['positions']}|set(entries)
            if not required.issubset(prices) or not required.issubset(bars) or not prices:
                s.update(misses=s['misses']+1,message='Incomplete shared snapshot; all trial books held');return
            histories={x:[float(r['close']) for r in bars[x]] for x in required}
            updated={};actions=[]
            for name,body in s['books'].items():
                ledger=(FasterDailyLedger if name=='faster_daily' else ExperimentLedger)(body);ledger.body['buy_block']=bool(buy_block)
                chosen=[x for x in entries if x not in body['positions'] and ledger.trading_signal(histories[x],prices[x])=='buy']
                cap=200
                if name=='allocation':
                    selected=[]
                    for symbol in chosen:
                        if any(self.correlated(symbol,other,bars) for other in list(body['positions'])+selected):
                            actions.append({'model':name,'symbol':symbol,'action':'SKIP','reason':'Correlation above 0.8 or insufficient aligned history'});continue
                        selected.append(symbol)
                        if len(selected)==2:break
                    cap=min([150]+[5/max(.05,2*assess(bars[x])['volatility_pct']/100) for x in selected])
                    chosen=selected[:max(0,int((body['cash']-200)//cap))]
                elif name=='volatility_size':
                    ledger.research_caps={x:max(1,min(200,int(5/max(.02,2*assess(bars[x])['volatility_pct']/100)))) for x in chosen}
                elif name=='trend_breadth':
                    grids={tuple(r['datetime'] for r in rows) for rows in bars.values() if len(rows)==50}
                    usable=[rows for rows in bars.values() if len(rows)==50]
                    fraction=sum(float(rows[-1]['close'])>sum(float(r['close']) for r in rows)/50 for rows in usable)/len(usable) if usable else 0
                    s['breadth_observation']=dict(at=stamp,histories=len(usable),above_mean_fraction=fraction,aligned=len(grids)==1)
                    if len(usable)<30 or len(grids)!=1 or fraction<.6:
                        actions.extend(dict(model=name,symbol=x,action='SKIP',reason='Supplied-history breadth insufficient, misaligned or below 60%') for x in chosen);chosen=[]
                elif name=='event_guard':
                    allowed=[]
                    for x in chosen:
                        deferred=EventCalendar(self.directory).upcoming(x,stamp)
                        actions.append(dict(model=name,symbol=x,action='REVIEW',reason='Event within 48 hours; entry deferred' if deferred else 'No recorded imminent event; coverage unverified'))
                        if not deferred:allowed.append(x)
                    chosen=allowed
                elif name=='concentration':
                    group={'AAPL','MSFT','AMZN','GOOGL','GOOG','META','NVDA','TSLA'}
                    allowed=[]
                    for symbol in chosen:
                        peers=list(body['positions'])+allowed
                        if (symbol in group and sum(x in group for x in peers)>=2) or any(self.correlated(symbol,x,bars) for x in peers):
                            actions.append(dict(model=name,symbol=symbol,action='SKIP',reason='Concentration group cap or correlation >0.8 / unavailable history'));continue
                        allowed.append(symbol)
                    chosen=allowed
                elif name=='confirmed_entry':
                    previous=s.get('entry_confirmation',{})
                    elapsed=(datetime.fromisoformat(stamp)-datetime.fromisoformat(previous['at'])).total_seconds() if previous else 0
                    confirmed=[x for x in chosen if x in previous.get('symbols',[]) and previous.get('day')==day and 900<=elapsed<=3600]
                    for symbol in chosen:
                        if symbol not in confirmed[:2]:actions.append(dict(model=name,symbol=symbol,action='SKIP',reason='Waiting for consecutive confirmation or two-entry staging cap'))
                    chosen=confirmed[:2]
                elif name=='filing_guard':
                    allowed=[]
                    for symbol in chosen:
                        ok,reason=self.news_gate(symbol,stamp)
                        actions.append({'model':name,'symbol':symbol,'action':'REVIEW','reason':reason})
                        if ok:allowed.append(symbol)
                    chosen=allowed
                symbols=list(body['positions'])+chosen
                # Isolated books accept bounded whole-pound risk allocations.
                if name=='allocation':
                    cap=max(1,min(150,int(cap)))
                if symbols:
                    decisions=ledger.cycle(prices,histories,fx,day,stamp,symbols=symbols,history_rows=bars,
                        purchase_limit=cap) or []
                else:
                    ledger.body.update(last_check=stamp,last_success=stamp,error=None,equity=ledger.body['cash']);decisions=[]
                if ledger.read().get('error'):raise ValueError('Trial corporate-action verification required')
                book=ledger.read();metric=experiment_metrics(book,[],0);peak=max(book.get('experiment_peak',1000),metric['equity'])
                book['experiment_peak']=peak;book['experiment_drawdown']=max(book.get('experiment_drawdown',0),(peak-metric['equity'])/peak*100)
                updated[name]=(book,sum(d['action']=='SELL' for d in decisions))
                actions.extend(dict(model=name,**d) for d in decisions)
            s['entry_confirmation']={'at':stamp,'day':day,'symbols':[x for x in entries if signal(histories[x],prices[x])=='buy']}
            for name,(book,closed) in updated.items():s['books'][name]=book;s['closes'][name]+=closed
            s.update(started=s['started'] or stamp,last=stamp,checks=s['checks']+1,decisions=actions,
                days=sorted(set(s['days']+[day]))[-252:],message='Collecting prospective evidence; active account unchanged')
        self.change(update)


class ReliableExperiments:
    """Optional research failures never prevent held-position checks.

    A separate validated setting survives experiment-store failure. New entries
    are deferred while research state is unavailable; exits still use live data.
    """
    def __init__(self,directory):
        self.directory=Path(directory);self.backend=None;self.error=None
        self.setting=self.directory/'active-purchase.json'
        self.last_limit=100;self.cached=False
        try:
            value=json.loads(self.setting.read_text())['active']
            if type(value) is int and value in EXPERIMENT_SIZES:self.last_limit=value;self.cached=True
        except (OSError,ValueError,KeyError,TypeError):pass

    def _call(self,name,*args,**kwargs):
        try:
            if self.backend is None:self.backend=Experiments(self.directory)
            result=getattr(self.backend,name)(*args,**kwargs)
            if name=='state':
                limit=result['active']
                if type(limit) is not int or limit not in EXPERIMENT_SIZES:raise ValueError('Invalid setting')
                if not self.cached or not self.setting.exists() or limit!=self.last_limit:
                    import tempfile,os
                    with tempfile.NamedTemporaryFile(mode='w',dir=self.directory,delete=False) as f:
                        temporary=Path(f.name);json.dump({'active':limit},f);f.flush();os.fsync(f.fileno())
                    try:temporary.chmod(0o600);temporary.replace(self.setting)
                    finally:temporary.unlink(missing_ok=True)
                self.last_limit=limit;self.cached=True
            self.error=None
            return result
        except Exception:
            self.backend=None
            self.error='Strategy experiment storage unavailable. New entries deferred; held-stock checks and exits continue. Repair or restore experiment storage; existing evidence was preserved.'
            if name=='control':raise ValueError(self.error) from None
            return None

    def state(self):
        result=self._call('state')
        return result if result is not None else dict(enabled=False,automatic=False,active=self.last_limit,
            previous=self.last_limit,round=0,phase='unavailable',message=self.error,error=self.error,
            books={},metrics={},qualified_sessions=0,required_sessions=EXPERIMENT_SESSIONS,
            reports=[],events=[],note='Last validated purchase setting shown; no new entries until storage recovers.')
    def active_limit(self):return self.state()['active']
    def holdings(self):return self._call('holdings') or []
    def observe(self,*args,**kwargs):return self._call('observe',*args,**kwargs)
    def promote(self,*args,**kwargs):return self._call('promote',*args,**kwargs)
    def miss(self,*args,**kwargs):return self._call('miss',*args,**kwargs)
    def invalidate(self,*args,**kwargs):return self._call('invalidate',*args,**kwargs)
    def control(self,*args,**kwargs):return self._call('control',*args,**kwargs)


class StrategyObservatory:
    """User-supplied observations only: no network, strategy execution or ledger writes."""
    lock=threading.RLock()
    columns=('source','strategy','kind','symbol','action','published_at','reported_price_usd')
    def __init__(self,directory):
        self.path=Path(directory)/'strategy-observatory.sqlite3'

    def connect(self):
        import sqlite3
        db=sqlite3.connect(self.path)
        db.execute('CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
        return db

    def import_csv(self,text,now=None):
        import math,re
        from contextlib import closing
        now=now or datetime.now(timezone.utc)
        if not isinstance(text,str) or len(text.encode())>90000:raise ValueError('CSV exceeds 90 KB')
        reader=csv.DictReader(io.StringIO(text))
        if reader.fieldnames!=list(self.columns):raise ValueError('Use the exact CSV template columns in order')
        rows=list(reader)
        if not 1<=len(rows)<=100:raise ValueError('Import 1 to 100 observations at a time')
        prepared=[]
        for row in rows:
            if None in row or any(v is None for v in row.values()):raise ValueError('Invalid CSV row')
            row={k:v.strip() for k,v in row.items()}
            if any(not 1<=len(row[k])<=100 or any(ord(c)<32 for c in row[k]) for k in ('source','strategy')):raise ValueError('Source and strategy require 1–100 printable characters')
            if row['kind'] not in ('live','paper','backtest','unknown'):raise ValueError('Kind must be live, paper, backtest or unknown; labels remain unverified')
            if not re.fullmatch(r'[A-Z][A-Z0-9.]{0,9}',row['symbol']) or row['action'] not in ('BUY','SELL'):raise ValueError('Use a US stock symbol and BUY or SELL')
            try:published=datetime.fromisoformat(row['published_at'].replace('Z','+00:00'))
            except ValueError:raise ValueError('Invalid publication timestamp') from None
            if published.tzinfo is None or published>now:raise ValueError('Publication timestamp needs a timezone and cannot be in the future')
            price=None
            if row['reported_price_usd']:
                try:price=float(row['reported_price_usd'])
                except ValueError:raise ValueError('Invalid reported price') from None
                if not math.isfinite(price) or price<=0:raise ValueError('Reported price must be finite and positive')
            row.update(published_at=published.astimezone(timezone.utc).isoformat(),reported_price_usd=price)
            ident=hashlib.sha256(json.dumps([row[k] for k in ('source','strategy','symbol','action','published_at')]).encode()).hexdigest()
            row.update(id=ident,first_seen=now.isoformat(),verification='User-supplied, unverified; not proof of an automated strategy',baseline=None,outcomes={})
            prepared.append(row)
        with self.lock,closing(self.connect()) as db,db:
            existing=db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
            new=sum(not db.execute('SELECT 1 FROM observations WHERE id=?',(r['id'],)).fetchone() for r in {r['id']:r for r in prepared}.values())
            if existing+new>10000:raise ValueError('Observation store is full (10,000); export before starting another study')
            for row in prepared:db.execute('INSERT OR IGNORE INTO observations VALUES (?,?)',(row['id'],json.dumps(row,allow_nan=False)))
        return new

    def observe(self,account,now=None):
        import math
        from contextlib import closing
        now=now or datetime.now(timezone.utc)
        if account.get('error'):return
        try:
            at=datetime.fromisoformat(account['last_success'])
            if at.tzinfo is None or not 0<=(now-at).total_seconds()<=120:return
        except (KeyError,TypeError,ValueError):return
        with self.lock,closing(self.connect()) as db,db:
            for ident,body in db.execute('SELECT id,body FROM observations').fetchall():
                row=json.loads(body);price=account.get('marks',{}).get(row['symbol'])
                if type(price) not in (int,float) or not math.isfinite(price) or price<=0:continue
                if at<=datetime.fromisoformat(row['first_seen']):continue
                if row['baseline'] is None:row['baseline']={'at':at.isoformat(),'price_usd':price}
                elapsed=(at-datetime.fromisoformat(row['baseline']['at'])).total_seconds()
                for label,seconds in (('1h',3600),('24h',86400),('120h',432000)):
                    if elapsed>=seconds and label not in row['outcomes']:
                        row['outcomes'][label]={'at':at.isoformat(),'price_usd':price,'elapsed_hours':elapsed/3600,'price_return_pct':100*(price/row['baseline']['price_usd']-1)}
                db.execute('UPDATE observations SET body=? WHERE id=?',(json.dumps(row,allow_nan=False),ident))

    def state(self):
        from contextlib import closing
        with self.lock,closing(self.connect()) as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT body FROM observations ORDER BY rowid DESC')]
        return {'records':rows,'total':len(rows),'note':'Imported claims remain unverified. No automatic subscriptions or copied orders. Baselines use the first fresh saved account mark AFTER import, never the reported entry price. Outcomes are underlying USD price moves, not strategy returns or SELL profits; exclude costs, dividends and FX, and are not corporate-action adjusted. Horizons are elapsed hours, not trading sessions; the first available later mark may be much later. Uncovered stocks remain unmeasured. Internal comparison books are shown separately under Research comparisons.'}


class ResearchSignals:
    """Prospective, bounded evidence journal. No network or active ledger writes."""
    lock=threading.RLock()
    def __init__(self,directory):
        self.directory=Path(directory);self.path=self.directory/'research-signals.sqlite3'

    def connect(self):
        import sqlite3
        db=sqlite3.connect(self.path,timeout=1)
        db.execute('CREATE TABLE IF NOT EXISTS signals(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
        return db

    @staticmethod
    def time(value):
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None:raise ValueError('Timezone required')
        return result.astimezone(timezone.utc)

    def sources(self):
        import sqlite3
        rows=[];errors=[]
        for path,table,kind in ((self.directory/'feed-observations.sqlite3','observations','feed'),(self.directory/'news'/'events.sqlite3','events','filing')):
            if not path.exists():continue
            try:
                with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                    for body, in db.execute(f'SELECT body FROM {table} ORDER BY rowid DESC LIMIT 500'):
                        item=json.loads(body);item['_kind']=kind;rows.append(item)
            except (sqlite3.Error,ValueError):errors.append(kind+' source storage unavailable')
        return rows,errors

    def observe(self,prices,bars,stamp,decisions=(),now=None):
        import math
        from zoneinfo import ZoneInfo
        now=now or datetime.now(timezone.utc);at=self.time(stamp)
        if not 0<=(now-at).total_seconds()<=120:return
        prices={s:p for s,p in prices.items() if type(p) in (int,float) and math.isfinite(p) and p>0}
        day=at.astimezone(ZoneInfo('America/New_York')).date().isoformat()
        actions={d['symbol']:d.get('action') for d in decisions}
        prepared=[]
        for symbol,price in prices.items():
            try:
                closes=[float(r['close']) for r in bars[symbol]]
                if len(closes)<50 or any(not math.isfinite(p) or p<=0 for p in closes):continue
                for name,short,long in (('trend_20_50',20,50),('faster_10_30',10,30)):
                    fast=sum(closes[-short:])/short;slow=sum(closes[-long:])/long
                    direction='BUY' if price>fast>slow else 'SELL' if price<fast else 'HOLD'
                    prepared.append({'id':f'{name}:{symbol}:{day}','symbol':symbol,'module':name,'direction':direction,
                        'first_seen':stamp,'published_at':stamp,'source':'Shared scanner snapshot','source_url':None,
                        'evidence':{'price_usd':price,'short_mean':fast,'long_mean':slow,'history_end':bars[symbol][-1].get('datetime')},
                        'active_action':actions.get(symbol,'Not evaluated'),'baseline':{'at':stamp,'price_usd':price},'outcomes':{}})
            except (KeyError,TypeError,ValueError):continue
        sources,errors=self.sources()
        for e in sources:
            try:
                seen=self.time(e['first_seen'])
                if not 0<=(at-seen).total_seconds()<=86400:continue
                identity=e.get('id') or e.get('metadata_sha256')
                if not identity:continue
                prepared.append({'id':e['_kind']+':'+identity,'symbol':e['symbol'],'module':e.get('kind',e['_kind']),
                    'direction':'OBSERVE','first_seen':stamp,'published_at':e.get('published_at') or e.get('accepted_at'),
                    'source_first_seen':e['first_seen'],'source':e.get('source','SEC EDGAR'),'source_url':e.get('source_url'),
                    'evidence':{'title':e.get('title'),'categories':e.get('categories'),'form':e.get('form'),'flag':e.get('flag'),
                                'confidence':e.get('confidence','Filing metadata; not a directional prediction')},
                    'active_action':actions.get(e['symbol'],'Not evaluated'),'baseline':None,'outcomes':{}})
            except (KeyError,TypeError,ValueError):continue
        with self.lock,closing(self.connect()) as db,db:
            count=db.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
            for row in prepared:
                if count>=10000:break
                count+=db.execute('INSERT OR IGNORE INTO signals VALUES (?,?)',(row['id'],json.dumps(row,allow_nan=False))).rowcount
            for ident,body in db.execute('SELECT id,body FROM signals').fetchall():
                row=json.loads(body);price=prices.get(row['symbol']);seen=self.time(row['first_seen'])
                if not price or at<seen:continue
                base=row['baseline']
                if base is None and 0<(at-seen).total_seconds()<=86400:
                    row['baseline']=base={'at':stamp,'price_usd':price}
                if base:
                    hours=(at-self.time(base['at'])).total_seconds()/3600
                    for horizon in (24,120):
                        if horizon<=hours<=horizon+72 and str(horizon) not in row['outcomes']:
                            ratio=price/base['price_usd']
                            row['outcomes'][str(horizon)]={'at':stamp,'elapsed_hours':hours,'price_return_pct':100*(ratio-1),
                                'hypothetical_long_after_cost_pct':100*(ratio*(1-.0025)*(1-.0015)/((1+.0025)*(1+.0015))-1)}
                db.execute('UPDATE signals SET body=? WHERE id=?',(json.dumps(row,allow_nan=False),ident))

    def state(self,export=False,now=None):
        now=now or datetime.now(timezone.utc)
        with self.lock,closing(self.connect()) as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT body FROM signals ORDER BY rowid DESC')]
        groups={}
        for r in rows:
            base=r['baseline'];age=(now-self.time(base['at'] if base else r['first_seen'])).total_seconds()/3600
            r['status']='Unmeasured: no baseline within 24h' if not base and age>24 else 'Awaiting baseline' if not base else 'Complete' if len(r['outcomes'])==2 else 'Window ended: incomplete coverage' if age>192 else 'Awaiting later quotes'
            g=groups.setdefault(r['module'],{'recorded':0,'measured_24h':0,'buy_measured_24h':0,'buy_positive_after_cost':0})
            g['recorded']+=1
            if '24' in r['outcomes']:
                g['measured_24h']+=1
                if r['direction']=='BUY':
                    g['buy_measured_24h']+=1;g['buy_positive_after_cost']+=int(r['outcomes']['24']['hypothetical_long_after_cost_pct']>0)
        return {'records':rows if export else rows[:100],'total':len(rows),'summary':groups,'source_errors':self.sources()[1],
            'full':len(rows)>=10000,'note':'Observation only. First snapshot per stock/model/New York day, including BUY, SELL and HOLD; candidate coverage is selected by the active scanner. News is context, not a buy/sell prediction. Only newly discovered items (within 24h) enter; no retroactive baselines. Identical stored feed IDs are deduplicated, but differently worded reports may share an underlying story: counts are not independent votes. Baselines require fresh shared quotes; news requires a subsequent quote within 24h. Outcomes use the first available quote 24/120 elapsed hours after baseline, allowing up to 72h delay; missing windows remain unmeasured. USD moves are unadjusted for splits/dividends/FX. Hypothetical long results apply 0.25% price and 0.15% FX allowances on each side with constant FX; they are not actual trades, SELL profits or portfolio returns. Overlapping observations are not independent. No automatic promotion. Store stops at 10,000 records; export retains all evidence.'}


class SessionScorecard:
    """Durable local check observations; never infers uptime outside recorded checks."""
    def __init__(self,directory):
        self.path=Path(directory)/'session-scorecard.sqlite3'
        self.path.parent.mkdir(parents=True,exist_ok=True)
    @contextmanager
    def connect(self):
        import sqlite3
        with closing(sqlite3.connect(self.path,timeout=2)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY,started TEXT,finished TEXT,success INTEGER,error TEXT)')
            yield db
    def begin(self,at):
        with self.connect() as db:
            ident=db.execute('INSERT INTO checks(started) VALUES(?)',(at,)).lastrowid
            db.execute('DELETE FROM checks WHERE id < ?',(ident-20000,))
            return ident
    def finish(self,ident,at,before,after):
        success=bool(after.get('last_success') and after.get('last_success')!=before.get('last_success'))
        with self.connect() as db:
            db.execute('UPDATE checks SET finished=?,success=?,error=? WHERE id=?',(at,int(success),str(after.get('error') or '')[:500],ident))
    def state(self):
        from zoneinfo import ZoneInfo
        groups={}
        with self.connect() as db:rows=db.execute('SELECT started,finished,success,error FROM checks ORDER BY id').fetchall()
        for start,end,success,error in rows:
            day=datetime.fromisoformat(start).astimezone(ZoneInfo('America/New_York')).date().isoformat()
            g=groups.setdefault(day,dict(day=day,attempts=0,successful=0,unfinished=0,quote_failures=0,successful_times=[],last_error=None))
            g['attempts']+=1;g['successful']+=int(bool(success));g['unfinished']+=int(end is None)
            if success:g['successful_times'].append(start)
            if error:g['last_error']=error
            if error and ('quote' in error.lower() or 'stale' in error.lower()):g['quote_failures']+=1
        for g in groups.values():
            times=g.pop('successful_times');g['longest_success_gap_minutes']=max([(datetime.fromisoformat(b)-datetime.fromisoformat(a)).total_seconds()/60 for a,b in zip(times,times[1:])],default=None)
            g['success_pct']=100*g['successful']/g['attempts']
        return dict(sessions=list(groups.values())[-30:],note='Recorded cycles only, grouped by New York date. Unfinished means running or interrupted. Gaps measure successful cycle start times, exclude unobserved time before/after, and do not prove continuous monitoring. Request usage is shown in the existing budget panel.')


class EventCalendar:
    """User-supplied upcoming events, never fetched or treated as verified coverage."""
    def __init__(self,directory):self.path=Path(directory)/'research-events.sqlite3'
    @contextmanager
    def connect(self):
        import sqlite3
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=3)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS events(symbol TEXT,at TEXT,source TEXT,seen TEXT,PRIMARY KEY(symbol,at))')
            yield db
    def remove(self,payload):
        with self.connect() as db:db.execute('DELETE FROM events WHERE symbol=? AND at=?',(str(payload.get('symbol','')),str(payload.get('at',''))))
    def add(self,payload,now=None):
        import re
        from urllib.parse import urlsplit
        symbol=str(payload.get('symbol','')).strip().upper();at=datetime.fromisoformat(str(payload.get('at','')).replace('Z','+00:00'))
        now=now or datetime.now(timezone.utc);source=str(payload.get('source','')).strip();url=urlsplit(source)
        if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,9}',symbol) or at.tzinfo is None or not now<at<=now+timedelta(days=366):raise ValueError('Provide a stock symbol and a future event time with timezone, within one year.')
        if len(source)>500 or url.scheme!='https' or not url.hostname or url.username or url.password:raise ValueError('Provide an HTTPS source URL without credentials.')
        with self.connect() as db:
            db.execute('DELETE FROM events WHERE at < ?',((now-timedelta(days=30)).isoformat(),))
            if db.execute('SELECT COUNT(*) FROM events').fetchone()[0]>=1000:raise ValueError('Calendar limit reached.')
            db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?)',(symbol,at.astimezone(timezone.utc).isoformat(),source,now.isoformat()))
    def state(self):
        with self.connect() as db:rows=db.execute('SELECT symbol,at,source,seen FROM events ORDER BY at DESC LIMIT 1000').fetchall()
        return dict(events=[dict(zip(('symbol','at','source','first_seen'),r)) for r in rows],note='Manually recorded, unverified events. No automatic earnings feed; absent events mean unknown coverage. Source URLs are not fetched. Research only, active trades unchanged.')
    def upcoming(self,symbol,stamp):
        at=datetime.fromisoformat(stamp)
        return any(e['symbol']==symbol and datetime.fromisoformat(e['first_seen'])<=at and timedelta(0)<=datetime.fromisoformat(e['at'])-at<=timedelta(hours=48) for e in self.state()['events'])


"""Daily, local-only evidence review and bounded strategy-code generation.

No model provider, arbitrary code execution, network calls or active account writes.
A frozen candidate is assessed on signals first observed AFTER selection.
"""
import hashlib
import json
import math
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
MODELS = {'trend_20_50': (20, 50), 'faster_10_30': (10, 30)}
BASELINE = 'trend_20_50'
MIN_DAYS = 30
MIN_BUYS = 20
NOTE = ('Research only; no active rule changes. Scores describe selected scanner observations, '
        'not portfolio returns or independent samples. News is context, not a prediction. '
        '120-hour outcomes include modelled trading friction but not changing FX, dividends or splits. '
        'Missing observations are not zero returns. Candidate screening needs 30 distinct observation '
        'dates, 20 BUY observations per model, 90% paired coverage, positive mean score, '
        '>0.1 percentage-point mean advantage and no worse worst daily score. '
        'Selection is followed by a separate prospective test with the same gates. '
        'Passing proposes a review, never a live deployment. Code generation uses two fixed templates only.')


def timestamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timezone required')
    return dt.astimezone(timezone.utc)


def strategy_code(model):
    short, long = MODELS[model]  # Never interpolate external text into code.
    return (f'"""Generated research candidate: {model}. No execution authority."""\n'
            f'SHORT = {short}\nLONG = {long}\n\n'
            'def signal(price, closes):\n'
            '    if len(closes) < LONG:\n        return "HOLD"\n'
            '    fast = sum(closes[-SHORT:]) / SHORT\n'
            '    slow = sum(closes[-LONG:]) / LONG\n'
            '    return "BUY" if price > fast > slow else "SELL" if price < fast else "HOLD"\n')


def outcome(row, now):
    value = row.get('outcomes', {}).get('120')
    if not value or timestamp(value['at']) > now:
        return None
    base = row.get('baseline')
    if not base or timestamp(base['at']) < timestamp(row['first_seen']):
        return None
    hours = (timestamp(value['at']) - timestamp(base['at'])).total_seconds() / 3600
    result = value.get('hypothetical_long_after_cost_pct')
    if not 120 <= hours <= 192 or type(result) not in (int, float) or not math.isfinite(result):
        return None
    return result


def scorecards(rows, now):
    groups = {}
    for r in rows:
        key = (r['module'], r['source'])
        g = groups.setdefault(key, dict(module=key[0], source=key[1], recorded=0,
             measured=0, pending=0, missing=0, buys=0, positive=0, negative=0,
             missed_buy_observations=0, avoided_negative_observations=0, _values=[]))
        g['recorded'] += 1
        result = outcome(r, now)
        if result is None:
            base = r.get('baseline')
            deadline = timestamp(base['at'] if base else r['first_seen']) + timedelta(hours=192 if base else 24)
            g['missing' if now > deadline else 'pending'] += 1
        else:
            g['measured'] += 1
            if r['direction'] == 'BUY':
                g['buys'] += 1
                g['positive'] += result > 0
                g['negative'] += result < 0
                g['_values'].append(result)
                # Unknown/unexamined candidates cannot be labelled missed trades.
                if r.get('active_action') in ('HOLD', 'SKIP', 'SELL'):
                    g['missed_buy_observations'] += result > 0
                    g['avoided_negative_observations'] += result < 0
    for g in groups.values():
        values = g.pop('_values')
        g['mean_buy_after_cost_pct'] = mean(values) if values else None
        g['predictive_value'] = 'Not established; descriptive observations only'
    return list(groups.values())


def paired_scores(rows, now, start=None, end=None):
    """Paired stock/day opportunities; no retrospective holdout reuse."""
    pairs = {}
    for r in rows:
        seen = timestamp(r['first_seen'])
        if r['module'] not in MODELS or seen > now or (start and seen <= start) or (end and seen > end):
            continue
        # All rows in this cohort must have had time to finish the full window.
        if (now - seen).total_seconds() < 193 * 3600:
            continue
        key = (r['symbol'], seen.astimezone(NY).date().isoformat())
        pairs.setdefault(key, {})[r['module']] = r
    daily = {m: {} for m in MODELS}
    buys = {m: 0 for m in MODELS}
    measured = 0
    for (_, day), pair in pairs.items():
        if set(pair) != set(MODELS):
            continue
        values = {m: outcome(r, now) for m, r in pair.items()}
        if any(v is None for v in values.values()):
            continue
        # Both original models must have shared the same snapshot price/time.
        if pair[BASELINE]['baseline'] != pair['faster_10_30']['baseline']:
            continue
        measured += 1
        for m, r in pair.items():
            buy = r['direction'] == 'BUY'
            buys[m] += buy
            daily[m].setdefault(day, []).append(values[m] if buy else 0.0)
    metrics = {}
    for m, days in daily.items():
        values = [mean(v) for v in days.values()]
        metrics[m] = dict(days=len(values), buys=buys[m],
                          mean_score_pct=mean(values) if values else None,
                          worst_day_score_pct=min(values) if values else None)
    return dict(eligible_pairs=len(pairs), measured_pairs=measured,
                coverage=measured / len(pairs) if pairs else 0, models=metrics)


def passes(stats):
    a, b = stats['models']['faster_10_30'], stats['models'][BASELINE]
    return (stats['coverage'] >= .9 and all(m['days'] >= MIN_DAYS and m['buys'] >= MIN_BUYS for m in (a, b))
            and a['mean_score_pct'] > 0 and a['mean_score_pct'] - b['mean_score_pct'] > .1
            and a['worst_day_score_pct'] >= b['worst_day_score_pct'])


class DailyLearning:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'daily-learning.sqlite3'
        self.error = None

    def connect(self):
        db = sqlite3.connect(self.path, timeout=1)
        db.execute('CREATE TABLE IF NOT EXISTS reviews(day TEXT PRIMARY KEY, body TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS model(id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        return db

    def state(self):
        with closing(self.connect()) as db:
            r = db.execute('SELECT body FROM reviews ORDER BY day DESC LIMIT 1').fetchone()
            m = db.execute('SELECT body FROM model WHERE id=1').fetchone()
        return dict(last_review=json.loads(r[0]) if r else None,
                    model=json.loads(m[0]) if m else {'phase': 'development'},
                    error=self.error, schedule='Once per weekday after 18:00 New York; requires running app. No backfilled daily reviews.',
                    note=NOTE, automatic_deployment=False)

    def review(self, ledger, now=None):
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(NY)
        if local.weekday() >= 5 or local.hour < 18:
            return False
        day = local.date().isoformat()
        with closing(self.connect()) as db:
            if db.execute('SELECT 1 FROM reviews WHERE day=?', (day,)).fetchone():
                return False
        evidence = ResearchSignals(self.directory).state(export=True, now=now)
        rows = [r for r in evidence['records'] if timestamp(r['first_seen']) <= now]
        accounting = ledger.accounting(100000)
        executions = [e for e in accounting['executions'] if timestamp(e['at']) <= now]
        today = [e for e in executions if timestamp(e['at']).astimezone(NY).date().isoformat() == day]
        closed = [e for e in today if e['side'] == 'SELL']
        cards = scorecards(rows, now)
        development = paired_scores(rows, now)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM reviews WHERE day=?', (day,)).fetchone():
                return False
            old = db.execute('SELECT body FROM model WHERE id=1').fetchone()
            model = json.loads(old[0]) if old else {'phase': 'development'}
            decision = 'No change: insufficient qualifying evidence or screening gates not met.'
            if model['phase'] == 'development' and passes(development) and not evidence['source_errors']:
                code = strategy_code('faster_10_30')
                compile(code, '<generated-research-candidate>', 'exec')  # Syntax check only.
                digest = hashlib.sha256(code.encode()).hexdigest()
                folder = self.directory / 'learning-candidates'
                folder.mkdir(exist_ok=True)
                target = folder / (digest + '.py')
                temp = target.with_suffix('.tmp')
                temp.write_text(code); temp.chmod(0o600); temp.replace(target)
                model = dict(phase='forward_test', selected='faster_10_30', frozen_at=now.isoformat(),
                             code_sha256=digest, candidate_file=target.name, development=development,
                             approval_required=True)
                decision = 'Candidate code generated and frozen; waiting for new forward-test observations.'
            if model['phase'] == 'forward_test':
                candidate = self.directory / 'learning-candidates' / model['candidate_file']
                if hashlib.sha256(candidate.read_bytes()).hexdigest() != model['code_sha256']:
                    raise ValueError('Candidate code changed; forward test cannot continue')
                test = paired_scores(rows, now, start=timestamp(model['frozen_at']))
                model['forward_test'] = test
                decision = 'No change: frozen candidate collecting new forward-test evidence.'
                if test['models'][BASELINE]['days'] >= MIN_DAYS:
                    model['phase'] = 'review_proposal' if passes(test) and not evidence['source_errors'] else 'rejected'
                    model['finished_at'] = now.isoformat()
                    decision = ('Candidate passed screening: independent portfolio review and approval required.'
                                if model['phase'] == 'review_proposal' else 'No change: candidate failed forward-test gates.')
            elif model['phase'] in ('review_proposal', 'rejected'):
                decision = 'No change: completed evaluation retained; no automatic retesting or deployment.'
            report = dict(day=day, completed_at=now.isoformat(), observations=len(rows),
                          usable_observations=sum(g['measured'] for g in cards), source_scorecards=cards,
                          source_errors=evidence['source_errors'], store_full=evidence['full'],
                          closed_trades=len(closed), realised_pnl_gbp=sum(e['realised_pnl_gbp'] for e in closed),
                          execution_costs_gbp=sum(e['price_cost_gbp'] + e['fx_cost_gbp'] for e in today),
                          closed_trade_reviews=closed, development=development, decision=decision,
                          limitations='Closed-trade totals cover this review date and detailed recorded fills only. Missed BUY observations are hypothetical, not executable missed profits. No benchmark-adjusted news attribution yet.')
            db.execute('INSERT OR REPLACE INTO model VALUES(1,?)', (json.dumps(model, allow_nan=False),))
            db.execute('INSERT INTO reviews VALUES(?,?)', (day, json.dumps(report, allow_nan=False)))
            db.execute('DELETE FROM reviews WHERE day NOT IN (SELECT day FROM reviews ORDER BY day DESC LIMIT 366)')
        return True

    def worker(self, ledger, stop):
        while not stop.is_set():
            try:
                self.review(ledger)
                self.error = None
            except Exception:
                self.error = 'Daily review failed; existing evidence preserved. Check storage and source records.'
            stop.wait(60)
