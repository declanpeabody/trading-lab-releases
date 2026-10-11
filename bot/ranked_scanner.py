"""Daily full-watchlist ranking, fresh held-stock and top-candidate quotes."""
import json
import sqlite3
import uuid
from contextlib import closing
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from engine import history, quote
from market import session_window
from scanner import Scanner, catalogue, LEGACY
from strategy import WATCHLIST, VERSION, rank_histories, candidates, assess


class RankedScanner(Scanner):
    def __init__(self, directory, market=None):
        super().__init__(directory,market)
        self.journal=Journal(directory)
        self.daily_path=self.directory/'ranked-daily.json'
        self.daily={}
        if self.daily_path.exists():
            try:self.daily=json.loads(self.daily_path.read_text())
            except (OSError,ValueError):pass

    def cycle(self, ledger):
        if not session_window(datetime.now(ZoneInfo('America/New_York'))) or ledger.read()['paused']:return
        self.audit={'bars':{},'prices':{},'decisions':[],'before':ledger.read()}
        error=None
        try:return self._cycle(ledger)
        except Exception as exc:
            error=str(exc) if isinstance(exc,ValueError) else 'Cycle failed before completion'
            raise
        finally:
            try:
                self.journal.record(datetime.now(ZoneInfo('UTC')).isoformat(),
                    datetime.now(ZoneInfo('America/New_York')).date().isoformat(),
                    self.audit,self.status,error)
            except Exception:
                self.status.setdefault('skipped',[]).append('Decision journal write failed; paper decisions were not changed.')

    def _cycle(self, ledger):
        now=datetime.now(ZoneInfo('America/New_York'))
        if not session_window(now) or ledger.read()['paused']:return
        today=now.date().isoformat();account=ledger.read();held=list(account['positions'])
        notes=[]
        self.status=dict(mode='Ranked 30-stock watchlist · US regular hours',version=VERSION,
            universe_count=len(WATCHLIST),selected=[],checked=[],skipped=notes,ranking=[])
        if time.time()-self.state['fetched']>7*86400:
            try:
                result=self.market.get('stocks',country='United States',type='Common Stock')
                universe=catalogue(result['data'])
                if not universe:raise ValueError('Empty stock catalogue')
                self.state.setdefault('known',{}).update(universe)
                self.state.update(universe=universe,fetched=time.time());self.save()
            except (ValueError,KeyError,TypeError):notes.append('Catalogue unavailable; cached mappings used if present.')
        mapping=LEGACY|self.state.get('known',{})|self.state['universe']
        if self.daily.get('day')!=today or self.daily.get('version')!=VERSION:
            self.daily={'day':today,'version':VERSION,'bars':{}}
        bars=self.daily['bars'];histories={}
        self.audit['bars']=bars
        # Held stocks come first and remain monitored if outside the new watchlist.
        for symbol in dict.fromkeys(held+list(WATCHLIST)):
            try:
                if symbol not in mapping:raise ValueError('Exchange identity unavailable')
                if symbol not in bars:
                    raw=self.market.get('time_series',priority=symbol in held,symbol=symbol,
                        exchange=mapping[symbol],interval='1day',outputsize=60,adjust='splits',order='asc')
                    if raw.get('meta',{}).get('exchange')!=mapping[symbol]:raise ValueError('Exchange mismatch')
                    closes=history(raw,symbol,today)
                    rows=sorted([r for r in raw['values'] if r['datetime'][:10]<today],key=lambda r:r['datetime'])[-50:]
                    # Volume is needed for ranking, but not for risk exits.
                    histories[symbol]=closes
                    if symbol in WATCHLIST:rank_histories({symbol:rows})
                    bars[symbol]=rows
                histories[symbol]=[float(r['close']) for r in bars[symbol]]
            except (ValueError,TypeError,KeyError) as exc:
                if symbol in held and symbol not in histories:
                    raise ValueError(f'Held stock {symbol}: history unavailable; no trades made.') from None
                notes.append(f'{symbol}: {exc}')
        tmp=self.daily_path.with_suffix('.tmp');tmp.write_text(json.dumps(self.daily,allow_nan=False));tmp.chmod(0o600);tmp.replace(self.daily_path)
        complete=all(s in bars for s in WATCHLIST)
        if complete:
            sessions={tuple(r['datetime'][:10] for r in bars[s]) for s in WATCHLIST}
            complete=len(sessions)==1
        ranked=rank_histories({s:bars[s] for s in WATCHLIST if s in bars})
        selected=candidates(ranked,held) if complete else []
        if not complete:notes.append('Ranking coverage incomplete: new entries disabled; held-stock checks continue.')
        self.status.update(ranking=ranked,ranking_date=today,ranking_complete=complete,
            histories_ready=sum(s in bars for s in WATCHLIST),selected=selected,
            attempts_today=len(selected),catalogue_at=self.state['fetched'])
        rawquotes={}
        for symbol in held+selected:
            try:
                q=self.market.get('quote',priority=symbol in held,symbol=symbol,
                    exchange=mapping[symbol],prepost='false')
                if q.get('exchange')!=mapping[symbol]:raise ValueError('Quote exchange mismatch')
                rawquotes[symbol]=q
            except (ValueError,KeyError,TypeError) as exc:
                if symbol in held:raise ValueError(f'Held stock {symbol}: quote unavailable; no trades made.') from None
                notes.append(f'{symbol}: {exc}')
        if not rawquotes:
            with ledger.lock:
                s=ledger.read();s.update(last_check=datetime.now(ZoneInfo('UTC')).isoformat(),
                    error=None if complete and not selected else 'No valid ranked candidate quotes; see scanner coverage.')
                ledger.write(s)
            return
        fxdata=self.market.get('quote',priority=bool(held),symbol='USD/GBP');stamp=time.time()
        prices={}
        for symbol,q in rawquotes.items():
            try:
                p=quote(q,symbol,stamp,'USD')
                if q.get('is_extended_hours') is True:raise ValueError('Extended hours disabled')
                if symbol not in held and p<1:raise ValueError('Price below $1')
                prices[symbol]=p
            except (ValueError,KeyError,TypeError) as exc:
                if symbol in held:raise ValueError(f'Held stock {symbol}: invalid quote; no trades made.') from None
                notes.append(f'{symbol}: {exc}')
        fx=quote(fxdata,'USD/GBP',stamp);self.status['checked']=list(prices)
        self.audit['prices']=prices
        if not prices:return
        if not session_window(datetime.now(ZoneInfo('America/New_York'))):raise ValueError('Session ended during check; no trades made.')
        self.audit['decisions']=ledger.cycle(prices,histories,fx,today,datetime.now(ZoneInfo('UTC')).isoformat(),symbols=list(prices)) or []


class Journal:
    """Independent observation database; no writes to the paper account."""
    def __init__(self,directory):
        self.path=__import__('pathlib').Path(directory)/'decisions.sqlite3'
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, at TEXT, day TEXT, symbol TEXT, body TEXT, outcomes TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS decisions_time ON decisions(at)')
        self.path.chmod(0o600)

    def record(self,stamp,day,audit,status,error=None):
        scan=uuid.uuid4().hex;bars=audit['bars'];prices=audit['prices']
        actions={d['symbol']:d for d in audit['decisions']}
        held=audit['before']['positions'];selected=status.get('selected',[])
        ranks={r['symbol']:i+1 for i,r in enumerate(status.get('ranking',[]))}
        with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
            # Resolve old observations only from subsequently received completed bars.
            for ident,symbol,observed,body,old in db.execute(
                    "SELECT id,symbol,day,body,outcomes FROM decisions WHERE day<? AND outcomes NOT LIKE '%\"20\"%'",(day,)).fetchall():
                rows=bars.get(symbol,[]);record=json.loads(body);out=json.loads(old)
                byday={r['datetime'][:10]:float(r['close']) for r in rows if r['datetime'][:10]<day}
                baseline=record.get('reference_day');future=sorted(d for d in byday if d>observed)
                # Rebase reference and future close from the same adjusted history.
                if baseline not in byday:continue
                for horizon in (1,5,20):
                    if str(horizon) not in out and len(future)>=horizon:
                        target=future[horizon-1]
                        out[str(horizon)]={'date':target,'return_pct':(byday[target]/byday[baseline]-1)*100,
                            'adjusted_reference_close':byday[baseline],'close':byday[target],'received_at':stamp}
                db.execute('UPDATE decisions SET outcomes=? WHERE id=?',(json.dumps(out,allow_nan=False),ident))
            for symbol in dict.fromkeys(list(WATCHLIST)+list(held)):
                rows=bars.get(symbol,[]);features=None
                try:features=assess(rows)
                except (ValueError,KeyError,TypeError):pass
                if symbol in actions:action,reason=actions[symbol]['action'],actions[symbol]['reason']
                elif error:action,reason='NOT_EVALUATED',error
                elif not features:action,reason='SKIP','Ranking history unavailable'
                elif symbol in held:action,reason='NOT_EVALUATED','Held-stock check did not complete'
                elif not status.get('ranking_complete'):action,reason='SKIP','Incomplete watchlist coverage'
                elif not features['eligible']:action,reason='SKIP','; '.join(features['reasons'])
                elif symbol not in selected:action,reason='SKIP','Outside top six unheld candidates'
                elif symbol not in prices:action,reason='SKIP','Fresh valid quote unavailable'
                else:action,reason='NOT_EVALUATED','Account paused or cycle not completed'
                body=dict(scan_id=scan,at=stamp,day=day,symbol=symbol,strategy_version=VERSION,
                    action=action,reason=reason,rank=ranks.get(symbol),features=features,
                    selected=symbol in selected,held_before=symbol in held,
                    quote_usd=prices.get(symbol),reference_day=rows[-1]['datetime'][:10] if rows else None,
                    prior_close=features['prior_close'] if features else None,
                    controls={k:audit['before'].get(k) for k in ('paused','buy_block','risk_latched','day_block')},
                    scan_notes=status.get('skipped',[]))
                db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?)',
                    (scan+':'+symbol,stamp,day,symbol,json.dumps(body,allow_nan=False),'{}'))

    def state(self,limit=200):
        with closing(sqlite3.connect(self.path,timeout=10)) as db:
            total=db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]
            rows=[json.loads(body)|{'outcomes':json.loads(out)} for body,out in db.execute(
                'SELECT body,outcomes FROM decisions ORDER BY at DESC,rowid DESC LIMIT ?',(limit,))]
        return dict(total=total,limit=limit,records=rows,
            note='Outcomes are cost-free USD moves from the prior completed close to the 1st, 5th and 20th session closes AFTER the observation date. They are not trade returns. References are rebased using the same split-adjusted history. Blank means pending or insufficient subsequently received history. Intraday observations overlap and are not independent samples.')
