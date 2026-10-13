"""Background research jobs; separate files from the active paper account."""
import csv
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
