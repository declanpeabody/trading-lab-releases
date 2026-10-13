"""Daily full-watchlist ranking, fresh held-stock and top-candidate quotes."""
import json
import sqlite3
import uuid
from contextlib import closing
import time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from engine import history, quote
from market import session_window, ApiBudget, MarketUnavailable
from scanner import Scanner, catalogue, LEGACY
from strategy import WATCHLIST, VERSION, rank_histories, candidates, assess, LIVE_VERSION, UNIVERSE_TARGET, HISTORY_BATCH, screening_universe


class RankedScanner(Scanner):
    """Trade from previously prepared histories, then prepare the next bounded batch."""
    def __init__(self, directory, market=None, clock=None, budget=None, experiments=None):
        self.clock=clock;self.budget=budget;self.experiments=experiments
        super().__init__(directory,market)
        self.journal=Journal(directory)
        self.daily_path=self.directory/'ranked-daily.json'
        self.daily={}
        if self.daily_path.exists():
            try:self.daily=json.loads(self.daily_path.read_text())
            except (OSError,ValueError):pass
        # Recover the old release's connection failures without resetting quota/history.
        retry={symbol for symbol,message in self.daily.get('errors',{}).items()
               if message in ('Market-data connection failed; no trades made.',
                              'Market-data request rejected; check key, quota and data entitlement.')}
        if retry:
            self.daily['attempted']=[s for s in self.daily.get('attempted',[]) if s not in retry]
            for symbol in retry:self.daily['errors'].pop(symbol,None)
            self.save_daily()

    def now(self, zone):
        return self.clock.now(zone) if self.clock else datetime.now(ZoneInfo(zone))

    def timestamp(self):
        return self.clock.timestamp() if self.clock else time.time()

    def monotonic(self):
        return self.clock.timestamp() if self.clock else time.monotonic()

    def save_daily(self):
        tmp=self.daily_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.daily,allow_nan=False));tmp.chmod(0o600);tmp.replace(self.daily_path)

    def cycle(self, ledger):
        if not session_window(self.now('America/New_York')) or ledger.read()['paused']:return
        self.audit={'bars':{},'prices':{},'decisions':[],'before':ledger.read()}
        error=None
        try:return self._cycle(ledger)
        except Exception as exc:
            error=str(exc) if isinstance(exc,ValueError) else 'Cycle failed before completion'
            if self.experiments:
                try:self.experiments.miss(self.now('America/New_York').date().isoformat())
                except Exception:pass
            if isinstance(exc,MarketUnavailable):self.status.setdefault('skipped',[]).append(str(exc))
            raise
        finally:
            try:
                self.journal.record(self.audit.get('at',self.now('UTC').isoformat()),
                    self.now('America/New_York').date().isoformat(),self.audit,self.status,error)
            except Exception:
                self.status.setdefault('skipped',[]).append('Decision journal write failed; paper decisions were not changed.')

    def _history(self,symbol,mapping,today,priority=False):
        if symbol not in mapping:raise ValueError('Exchange identity unavailable')
        raw=self.market.get('time_series',priority=priority,symbol=symbol,exchange=mapping[symbol],
            interval='1day',outputsize=60,adjust='splits',order='asc')
        if raw.get('meta',{}).get('exchange')!=mapping[symbol]:raise ValueError('Exchange mismatch')
        history(raw,symbol,today)
        return sorted([r for r in raw['values'] if r['datetime'][:10]<today],key=lambda r:r['datetime'])[-50:]

    def _cycle(self,ledger):
        today=self.now('America/New_York').date().isoformat()
        held=list(ledger.read()['positions']);notes=[]
        self.status=dict(mode='Batched 300-stock target · US regular hours',version=LIVE_VERSION,
            target_count=UNIVERSE_TARGET,universe_count=0,selected=[],checked=[],skipped=notes,ranking=[])
        cached=screening_universe(self.state['universe'])
        self.status.update(universe_count=len(cached),universe_symbols=cached)
        if self.timestamp()-self.state['fetched']>7*86400:
            try:
                universe=catalogue(self.market.get('stocks',country='United States',type='Common Stock')['data'])
                if not universe:raise ValueError('Empty catalogue')
                self.state.setdefault('known',{}).update(universe)
                self.state.update(universe=universe,fetched=self.timestamp());self.save()
            except MarketUnavailable:raise
            except (ValueError,KeyError,TypeError) as exc:notes.append(f'Catalogue unavailable; using cached mappings. {exc}')
        mapping=LEGACY|self.state.get('known',{})|self.state['universe']
        universe=screening_universe(self.state['universe'])
        self.status.update(universe_count=len(universe),universe_symbols=universe,catalogue_at=self.state['fetched'])
        if len(universe)<UNIVERSE_TARGET:notes.append(f'Catalogue currently provides {len(universe)} of the 300 target stocks.')
        if self.daily.get('day')!=today or self.daily.get('version')!=LIVE_VERSION:
            previous=self.daily.get('bars',{}) if self.daily.get('version')==LIVE_VERSION else {}
            self.daily={'day':today,'version':LIVE_VERSION,'bars':{},'attempted':[],'errors':{},'previous_bars':previous}
        bars=self.daily['bars'];attempted=set(self.daily['attempted'])
        self.status.update(histories_ready=sum(s in bars for s in universe),
            history_failures=dict(self.daily['errors']),histories_pending=sum(s not in bars and s not in attempted for s in universe))
        # Holdings need complete price histories for exits, but never need volume eligibility.
        for symbol in held:
            if symbol not in bars:
                try:bars[symbol]=self._history(symbol,mapping,today,True)
                except MarketUnavailable:raise
                except (ValueError,TypeError,KeyError):raise ValueError(f'Held stock {symbol}: history unavailable; decisions blocked this cycle.') from None
        self.save_daily()
        # Ranking uses ONLY valid bars fetched today and a common completed session grid.
        valid={}
        for symbol in universe:
            if symbol not in bars:continue
            try:
                assess(bars[symbol]);valid[symbol]=bars[symbol]
            except (ValueError,TypeError,KeyError):continue
        grids={s:tuple(r['datetime'][:10] for r in rows) for s,rows in valid.items()}
        # Select the most recent grid, preferring the most populated if dates tie.
        grid=max(set(grids.values()),key=lambda g:(g[-1],sum(v==g for v in grids.values()),g)) if grids else None
        ready={s:valid[s] for s in valid if grids[s]==grid}
        ranked=rank_histories(ready);selected=candidates(ranked,held)
        experiment_held=self.experiments.holdings() if self.experiments else []
        extras=[s for s in experiment_held if s not in held]
        if len(extras)>6:
            self.experiments.invalidate('Evaluation stopped: divergent experiment holdings exceed the six shared quote slots. No promotion; review and restart the evaluation.')
            experiment_held=[];extras=[]
        # Six normal quote slots remain a hard ceiling; observer holdings share
        # them with candidates. Existing account holdings retain priority credits.
        requested=list(dict.fromkeys(extras+selected))[:6]
        selected=[s for s in selected if s in requested]
        if extras:notes.append('Experiment holdings share the six non-held quote slots; entry coverage may be reduced.')
        for symbol in requested:
            if symbol in experiment_held and symbol not in bars:
                try:bars[symbol]=self._history(symbol,mapping,today)
                except MarketUnavailable:raise
                except (ValueError,KeyError,TypeError):notes.append(f'{symbol}: experiment history unavailable; comparison held.')
        self.status.update(ranking=ranked,ranking_date=today,ranking_complete=len(ready)==len(universe) and bool(universe),
            partial_ranking_allowed=True,rankable_symbols=list(ready),ranked_histories=len(ready),
            ranking_asof=grid[-1] if grid else None,selected=selected)
        if len(ready)<len(universe):notes.append('Partial ranking: only ready stocks are eligible; the rest wait for valid history.')
        self.audit['bars']=dict(bars)
        self.audit['at']=self.now('UTC').isoformat()
        raw={}
        for symbol in held+requested:
            try:
                q=self.market.get('quote',priority=symbol in held,symbol=symbol,exchange=mapping[symbol],prepost='false')
                if q.get('exchange')!=mapping[symbol]:raise ValueError('Quote exchange mismatch')
                raw[symbol]=q
            except MarketUnavailable:raise
            except (ValueError,KeyError,TypeError) as exc:
                if symbol in held:raise ValueError(f'Held stock {symbol}: quote unavailable; decisions blocked this cycle.') from None
                notes.append(f'{symbol}: {exc}')
        if raw:
            fxdata=self.market.get('quote',priority=bool(held),symbol='USD/GBP');stamp=self.timestamp();prices={}
            for symbol,q in raw.items():
                try:
                    p=quote(q,symbol,stamp,'USD')
                    if q.get('is_extended_hours') is True:raise ValueError('Extended hours disabled')
                    if symbol not in held and symbol not in experiment_held and p<1:raise ValueError('Price below $1')
                    prices[symbol]=p
                except (ValueError,KeyError,TypeError) as exc:
                    if symbol in held:raise ValueError(f'Held stock {symbol}: invalid quote; decisions blocked this cycle.') from None
                    notes.append(f'{symbol}: {exc}')
            fx=quote(fxdata,'USD/GBP',stamp)
            if not session_window(self.now('America/New_York')):raise ValueError('Session ended; no decisions this cycle.')
            self.audit['prices']=prices;self.status['checked']=list(prices)
            if prices:
                entry_symbols=[s for s in selected if prices.get(s,1)>=1]
                active_prices={s:p for s,p in prices.items() if s in held or s in entry_symbols}
                histories={s:[float(r['close']) for r in bars[s]] for s in active_prices}
                limit=self.experiments.active_limit() if self.experiments else 200
                self.status['active_purchase_limit']=limit
                if active_prices:
                    self.audit['decisions']=ledger.cycle(active_prices,histories,fx,today,self.audit['at'],symbols=list(active_prices),history_rows=bars,purchase_limit=limit) or []
                else:
                    with ledger.lock:
                        state=ledger.read()
                        state.update(last_check=self.audit['at'],error='No valid ranked candidate quotes; see scanner coverage.' if selected else None)
                        ledger.write(state)
                live=ledger.read()
                if self.experiments and not live['paused'] and not live.get('error'):
                    try:
                        self.experiments.observe(prices,bars,fx,today,self.audit['at'],entry_symbols,buy_block=live['buy_block'])
                        self.experiments.promote(ledger,today)
                    except Exception:
                        notes.append('Experiment storage/processing unavailable; no promotion confirmed. Active paper decisions already recorded.')
                        try:self.experiments.miss(today)
                        except Exception:pass
        if not self.status['checked']:
            if self.experiments:self.experiments.miss(today)
            with ledger.lock:
                message=('No valid ranked candidate quotes; see scanner coverage.' if selected else None)
                self.status['decision_state']='data_blocked' if selected else 'warming_up' if not ready else 'no_eligible_candidates'
                state=ledger.read();state.update(last_check=self.audit['at'],error=message);ledger.write(state)
        # Decisions finish first. New histories only enter the NEXT scan's ranking.
        # Preserve the cursor across days/restarts so quota limits cannot starve the tail.
        cursor=self.state.get('screen_cursor',0);visited=0;batch=[];deadline=self.monotonic()+180
        budget=self.budget or ApiBudget(self.directory);shared_failure=None
        while universe and visited<len(universe) and len(batch)<HISTORY_BATCH:
            if ledger.read()['paused'] or not session_window(self.now('America/New_York')):break
            if self.monotonic()>=deadline:break
            if budget.used()>=500:
                notes.append('Normal API budget exhausted; history batches paused, priority holdings checks retained.');break
            symbol=universe[cursor%len(universe)];cursor=(cursor+1)%len(universe);visited+=1
            if symbol in attempted or symbol in bars:continue
            batch.append(symbol);attempted.add(symbol)
            try:
                rows=self._history(symbol,mapping,today)
                assess(rows);bars[symbol]=rows;self.daily['errors'].pop(symbol,None)
                # A new session requires fresh bars. Only identical grids verified
                # by today's first successful response can reuse previous bars.
                if 'verified_grid' not in self.daily:
                    grid=[r['datetime'][:10] for r in rows];self.daily['verified_grid']=grid
                    for cached,old in self.daily.pop('previous_bars',{}).items():
                        if cached not in held and cached in universe and cached not in bars and [r['datetime'][:10] for r in old]==grid:
                            bars[cached]=old
            except MarketUnavailable as exc:
                # Keep this symbol pending and stop the batch, rather than poisoning
                # every stock's daily history cache with an account/network failure.
                attempted.discard(symbol);cursor-=1;shared_failure=exc
                notes.append('History batch stopped after a shared data failure. Pending stocks will retry at a later scheduled check.')
                break
            except (ValueError,TypeError,KeyError) as exc:
                self.daily['errors'][symbol]=str(exc)
                if 'Local daily API budget' in str(exc):break
        self.daily['attempted']=sorted(attempted);self.save_daily()
        self.state['screen_cursor']=cursor;self.save()
        count=sum(s in bars for s in universe)
        self.status.update(histories_ready=count,history_batch=batch,attempts_today=len(attempted),
            history_failures=dict(self.daily['errors']),histories_pending=sum(s not in bars and s not in attempted for s in universe))
        if shared_failure:raise shared_failure
        if self.daily['errors']:notes.append(f'{len(self.daily["errors"])} histories unavailable today; those stocks are skipped until next day.')


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
            oldest=(date.fromisoformat(day)-timedelta(days=60)).isoformat()
            for ident,symbol,observed,body,old in db.execute(
                    "SELECT id,symbol,day,body,outcomes FROM decisions WHERE day<? AND day>=? AND outcomes NOT LIKE '%\"20\"%'",(day,oldest)).fetchall():
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
            symbols=list(dict.fromkeys(status.get("universe_symbols",list(WATCHLIST))+list(held)))
            # Keep held/selected decisions visible in the latest bounded UI/export window.
            symbols.sort(key=lambda s:(s in held or s in selected,s))
            for symbol in symbols:
                rows=bars.get(symbol,[]);features=None
                try:features=assess(rows)
                except (ValueError,KeyError,TypeError):pass
                if symbol in actions:action,reason=actions[symbol]['action'],actions[symbol]['reason']
                elif error:action,reason='NOT_EVALUATED',error
                elif not features:action,reason='SKIP','Ranking history unavailable'
                elif symbol in held:action,reason='NOT_EVALUATED','Held-stock check did not complete'
                elif status.get('partial_ranking_allowed') and symbol not in status.get('rankable_symbols',[]):action,reason='SKIP','History not on the current ranking session grid'
                elif not status.get('ranking_complete') and not status.get('partial_ranking_allowed'):action,reason='SKIP','Incomplete watchlist coverage'
                elif not features['eligible']:action,reason='SKIP','; '.join(features['reasons'])
                elif symbol not in selected:action,reason='SKIP','Outside top six unheld candidates'
                elif symbol not in prices:action,reason='SKIP','Fresh valid quote unavailable'
                else:action,reason='NOT_EVALUATED','Account paused or cycle not completed'
                body=dict(scan_id=scan,at=stamp,day=day,symbol=symbol,strategy_version=status.get("version",VERSION),
                    action=action,reason=reason,rank=ranks.get(symbol),features=features,purchase_limit=status.get('active_purchase_limit',200),
                    selected=symbol in selected,held_before=symbol in held,
                    quote_usd=prices.get(symbol),reference_day=rows[-1]['datetime'][:10] if rows else None,
                    prior_close=features['prior_close'] if features else None,
                    controls={k:audit['before'].get(k) for k in ('paused','buy_block','risk_latched','day_block')},
                    scan_notes=status.get('skipped',[]))
                db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?)',
                    (scan+':'+symbol,stamp,day,symbol,json.dumps(body,allow_nan=False),'{}'))
            cutoff=(date.fromisoformat(day)-timedelta(days=90)).isoformat()
            db.execute('DELETE FROM decisions WHERE day<?',(cutoff,))
            db.execute('DELETE FROM decisions WHERE rowid IN (SELECT rowid FROM decisions ORDER BY at DESC,rowid DESC LIMIT -1 OFFSET 250000)')

    def state(self,limit=200):
        with closing(sqlite3.connect(self.path,timeout=10)) as db:
            total=db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]
            rows=[json.loads(body)|{'outcomes':json.loads(out)} for body,out in db.execute(
                'SELECT body,outcomes FROM decisions ORDER BY at DESC,rowid DESC LIMIT ?',(limit,))]
        return dict(total=total,limit=limit,records=rows,
            note='Retention: latest 90 days, capped at 250,000 observations. Export regularly to keep older records. Outcomes are cost-free USD moves from the prior completed close to the 1st, 5th and 20th session closes AFTER the observation date. They are not trade returns. References are rebased using the same split-adjusted history. Blank means pending or insufficient subsequently received history. Intraday observations overlap and are not independent samples.')
