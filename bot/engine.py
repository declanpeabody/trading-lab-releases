"""Persistent paper broker and explicit, unproven trend strategy. No real orders."""
import copy
import json
import math
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from strategy import PAPER_CAPITAL, MAX_POSITION, MAX_OPEN, STOP_FRACTION, LOSS_FRACTION, DAILY_LOSS_FRACTION

SYMBOLS = ('AAPL', 'MSFT', 'AMZN')
INITIAL_CASH = 100.0
PRICE_ALLOWANCE = 0.0025
FX_ALLOWANCE = 0.0015


def positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Price or quantity is not finite and positive')
    return value


def quote(data, symbol, now, currency=None):
    # Only static validation text and finite derived ages may leave this function.
    # Do not expose provider field values (or float conversion exception text).
    if not isinstance(data,dict):raise ValueError('Quote response is not an object')
    if data.get('symbol') != symbol:
        raise ValueError('Quote symbol mismatch')
    if currency and data.get('currency') != currency:
        raise ValueError('Quote currency mismatch')
    try:stamp=positive(data.get('last_quote_at') or data.get('timestamp',0))
    except (ValueError,TypeError,OverflowError):raise ValueError('Quote timestamp is missing, invalid or non-positive') from None
    age=now-stamp
    if not math.isfinite(age):raise ValueError('Quote age is invalid')
    if age>120:raise ValueError(f'Quote older than two minutes: {age:.1f} seconds old at validation (maximum 120 seconds)')
    if age< -5:raise ValueError(f'Quote timestamp in future: {-age:.1f} seconds ahead (maximum 5 seconds)')
    if data.get('is_market_open') is not True:
        raise ValueError('Market is closed or its status is unavailable')
    try:return positive(data.get('close'))
    except (ValueError,TypeError,OverflowError):raise ValueError('Quote price is missing, invalid or non-positive') from None


def history(data, symbol, today):
    meta = data.get('meta', {})
    if meta.get('symbol') != symbol or meta.get('currency') != 'USD':
        raise ValueError('Daily history symbol or currency mismatch')
    rows = data.get('values', [])
    # Ignore today's unfinished daily candle. Never create a look-ahead signal.
    unique = {}
    for row in rows:
        date = row['datetime'][:10]
        datetime.strptime(date, '%Y-%m-%d')
        if date < today:
            if date in unique:
                raise ValueError('Duplicate daily candle')
            unique[date] = positive(row['close'])
    dates = sorted(unique)
    if len(dates) < 50:
        raise ValueError('At least 50 completed daily candles required')
    age = (datetime.fromisoformat(today) - datetime.fromisoformat(dates[-1])).days
    if age > 4:
        raise ValueError('Daily history is stale')
    return [unique[d] for d in dates[-50:]]


def signal(closes, price):
    if len(closes) != 50:
        raise ValueError('Expected 50 completed daily candles')
    closes = [positive(x) for x in closes]
    price = positive(price)
    fast, slow = sum(closes[-20:]) / 20, sum(closes) / 50
    return 'buy' if price > fast > slow else ('sell' if price < fast else 'hold')


def new_paper_state(initial_cash=PAPER_CAPITAL):
    return dict(mode='paper', paused=True, buy_block=False, risk_latched=False,
                       cash=initial_cash, equity=initial_cash, positions={},
                       contributed_capital=initial_cash,
                       last_buy={}, day='', day_start=initial_cash,
                       last_check=None, last_success=None, error=None,
                       marks={}, fx=None, signals={}, day_block=False)


class Ledger:
    def __init__(self, path, initial_cash=PAPER_CAPITAL, price_allowance=PRICE_ALLOWANCE, fx_allowance=FX_ALLOWANCE, max_positions=MAX_OPEN, purchase_cap=MAX_POSITION):
        if type(max_positions) is not int or not 1<=max_positions<=20 or purchase_cap not in (200,1000):raise ValueError("Invalid paper account limits")
        self.max_positions=max_positions;self.purchase_cap=purchase_cap
        initial_cash = positive(initial_cash)
        if not all(math.isfinite(x) and 0<=x<=.02 for x in (price_allowance,fx_allowance)):raise ValueError('Invalid cost allowances')
        self.price_allowance=price_allowance;self.fx_allowance=fx_allowance
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, message TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY, at TEXT NOT NULL, body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS funding(id TEXT PRIMARY KEY, at TEXT NOT NULL, body TEXT NOT NULL)')
        initial = new_paper_state(initial_cash)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO ledger VALUES (1,?)', (json.dumps(initial),))
            state = json.loads(self.db.execute('SELECT body FROM ledger WHERE id=1').fetchone()[0])
            # Legacy accounts had exactly £100 funding. Persist the funding marker
            # and cash adjustment in one transaction so restarts cannot repeat it.
            funded = positive(state.get('contributed_capital', INITIAL_CASH))
            addition = max(0.0, initial_cash - funded)
            for field in ('cash', 'equity', 'day_start'):
                state[field] += addition
            state['contributed_capital'] = funded + addition
            state['account_limits']={'max_positions':self.max_positions,'purchase_cap':self.purchase_cap}
            self.db.execute('UPDATE ledger SET body=? WHERE id=1', (json.dumps(state,allow_nan=False),))
            if addition:
                funding=dict(id=uuid.uuid4().hex,at=datetime.now(timezone.utc).isoformat(),amount_gbp=addition,total_contributed_gbp=funded+addition,kind='paper_deposit')
                self.db.execute('INSERT INTO funding VALUES (?,?,?)',(funding['id'],funding['at'],json.dumps(funding)))
                self.db.execute('INSERT INTO events(at,message) VALUES (?,?)',
                    (datetime.now(timezone.utc).isoformat(),
                     f'PAPER FUNDING: added £{addition:.2f}; total contributed £{funded+addition:.2f}. Not investment profit.'))

    def read(self):
        with self.lock:
            state = json.loads(self.db.execute('SELECT body FROM ledger WHERE id=1').fetchone()[0])
            state['events'] = [dict(at=a, message=m) for a,m in self.db.execute(
                'SELECT at,message FROM events ORDER BY id DESC LIMIT 50')]
            return state

    def write(self, state, messages=(), executions=()):
        state.pop('events', None)
        with self.db:
            for execution in executions:
                self.db.execute('INSERT INTO executions VALUES (?,?,?)',(execution['id'],execution['at'],json.dumps(execution,allow_nan=False)))
            self.db.execute('UPDATE ledger SET body=? WHERE id=1', (json.dumps(state, allow_nan=False),))
            self.db.executemany('INSERT INTO events(at,message) VALUES (?,?)',
                [(datetime.now(timezone.utc).isoformat(), m) for m in messages])
            self.db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)')

    def control(self, action):
        with self.lock:
            s = self.read()
            if action == 'start': s['paused'] = False
            elif action == 'pause': s['paused'] = True
            elif action == 'block': s['buy_block'] = True
            elif action == 'allow': s['buy_block'] = False
            else: raise ValueError('Unknown control')
            self.write(s, [f'Control: {action} (paper only)'])

    def fail(self, message):
        with self.lock:
            s = self.read()
            changed = s['error'] != message
            s.update(error=message, last_check=datetime.now(timezone.utc).isoformat())
            self.write(s, [message] if changed else [])

    def reconcile(self, symbol, ident, ratio, source):
        """Explicitly confirmed new shares / old shares; never inferred from price."""
        ratio=positive(ratio)
        if not .001<=ratio<=1000 or not isinstance(source,str) or not 8<=len(source.strip())<=300:
            raise ValueError('Enter a verified share ratio and the source you checked (8–300 characters).')
        with self.lock:
            s=self.read();p=s['positions'].get(symbol)
            pending=(p or {}).get('reconciliation')
            if not pending or pending['id']!=ident:raise ValueError('Reconciliation changed or was already applied. Refresh first.')
            p['qty']*=ratio;p['entry_usd']/=ratio
            if symbol in s['marks']:s['marks'][symbol]/=ratio
            p['basis_reference']=pending['reference'];p['basis_confirmed_day']=pending['day']
            del p['reconciliation']
            s['error']='Corporate action confirmed; waiting for a fresh complete check.'
            self.write(s,[f'PAPER RECONCILIATION {symbol}: {ratio:g} new shares per old share; cost basis unchanged. Source: {source.strip()}'])

    def check_basis(self,s,prices,rows,day):
        """Block valuation AND exits before a changed adjusted basis can corrupt equity."""
        blocked=[]
        for symbol,p in s['positions'].items():
            bars=rows.get(symbol,[])
            if not bars:raise ValueError('Held-stock basis history missing')
            reference={'day':bars[-1]['datetime'][:10],'close':positive(bars[-1]['close'])}
            old=p.get('basis_reference');byday={r['datetime'][:10]:positive(r['close']) for r in bars}
            reason=None
            if not old or old['day'] not in byday:reason='Stored price basis needs verification'
            elif not math.isclose(byday[old['day']],old['close'],rel_tol=.001):reason='Previously completed prices were adjusted; check for a stock split or data correction'
            elif p.get('basis_confirmed_day')!=day and abs(prices[symbol]/reference['close']-1)>.35:
                reason='Large price discontinuity; check for a corporate action or genuine price move'
            if p.get('reconciliation') or reason:
                if not p.get('reconciliation'):
                    p['reconciliation']={'id':uuid.uuid4().hex,'day':day,'reason':reason,
                        'reference':reference,'previous_reference':old,'observed_price':prices[symbol]}
                blocked.append(symbol)
            else:p['basis_reference']=reference
        return blocked

    def valid_purchase_limit(self,value):
        return type(value) in (int,float) and value in (100,150,200,1000) and value<=getattr(self,'purchase_cap',MAX_POSITION)

    def cycle(self, prices, histories, fx, date, stamp, symbols=None, history_rows=None, purchase_limit=MAX_POSITION):
        # Validate the complete snapshot before touching cash or holdings.
        if not self.valid_purchase_limit(purchase_limit):
            raise ValueError('Purchase setting exceeds the configured paper safety cap')
        fx = positive(fx)
        symbols = tuple(SYMBOLS if symbols is None else symbols)
        if not symbols or len(set(symbols)) != len(symbols):
            raise ValueError('Empty or duplicate snapshot universe')
        prices = {symbol: positive(prices[symbol]) for symbol in symbols}
        signals = {symbol: signal(histories[symbol], prices[symbol]) for symbol in symbols}
        with self.lock:
            s = self.read()
            if s['paused']:
                return
            if not set(s['positions']).issubset(prices):
                raise ValueError('Missing held-stock prices; no trades made')
            if history_rows is not None:
                blocked=self.check_basis(s,prices,history_rows,date)
                if blocked:
                    s.update(last_check=stamp,error='Corporate-action verification required: '+', '.join(blocked)+'. Open Positions. Account valuation and decisions held.')
                    self.write(s)
                    return [dict(symbol=k,action='NOT_EVALUATED',reason=s['error']) for k in symbols]
            before = copy.deepcopy(s)
            s.update(last_check=stamp, last_success=stamp, error=None,
                     marks=prices, fx=fx, signals=signals)
            equity = s['cash'] + sum(p['qty'] * prices[k] * fx for k,p in s['positions'].items())
            if s['day'] != date:
                s.update(day=date, day_start=equity, day_block=False)
            s['risk_latched'] |= equity <= s['contributed_capital'] * (1-LOSS_FRACTION)
            s['day_block'] |= equity <= s['day_start'] * (1-DAILY_LOSS_FRACTION)
            messages = []
            decisions = []; executions=[]
            if s['risk_latched'] and not before['risk_latched']:
                messages.append('Total drawdown threshold reached: new buying locked; exits continue.')
            if s['day_block'] and not before['day_block']:
                messages.append('Daily loss threshold reached: new buying blocked for today; exits continue.')
            # Process all held stocks before candidates, so exits free cash first.
            ordered = list(s['positions']) + [k for k in symbols if k not in s['positions']]
            for symbol in ordered:
                p = s['positions'].get(symbol)
                action,reason="SKIP","No entry signal"
                if p and (signals[symbol] == 'sell' or prices[symbol] <= p['entry_usd'] * (1-STOP_FRACTION)):
                    action,reason='SELL',('Observed price loss exit' if prices[symbol] <= p['entry_usd']*(1-STOP_FRACTION) else 'Trend exit')
                    proceeds = p['qty'] * prices[symbol] * (1-self.price_allowance) * fx * (1-self.fx_allowance)
                    s['cash'] += proceeds
                    gross=p['qty']*prices[symbol]*fx
                    executions.append(dict(id=uuid.uuid4().hex,at=stamp,day=date,symbol=symbol,side='SELL',reason=reason,
                        quantity=p['qty'],quote_usd=prices[symbol],execution_usd=prices[symbol]*(1-self.price_allowance),
                        fx_gbp_per_usd=fx,price_allowance=self.price_allowance,fx_allowance=self.fx_allowance,
                        gross_gbp=gross,price_cost_gbp=gross*self.price_allowance,
                        fx_cost_gbp=gross*(1-self.price_allowance)*self.fx_allowance,net_cash_gbp=proceeds,
                        cost_basis_gbp=p['cost_gbp'],realised_pnl_gbp=proceeds-p['cost_gbp'],
                        entry_at=p.get('opened'),entry_execution_id=p.get('execution_id'),entry_fx=p.get('entry_fx'),
                        entry_basis_usd=p['entry_usd'],legacy_entry=p.get('execution_id') is None))
                    del s['positions'][symbol]
                    # A stopped-out stock cannot be re-entered on the same day.
                    s['last_buy'][symbol] = date
                    messages.append(f'PAPER SELL {symbol}: {p["qty"]:.6f} shares, £{proceeds:.2f} proceeds after modelled costs.')
                elif (not p and signals[symbol] == 'buy' and not s['buy_block']
                      and len(s['positions']) < getattr(self,'max_positions',MAX_OPEN)
                      and not s['risk_latched'] and not s['day_block']
                      and s['last_buy'].get(symbol) != date):
                    unit_cost = prices[symbol] * (1+self.price_allowance) * fx * (1+self.fx_allowance)
                    budget = min(purchase_limit, getattr(self,'purchase_cap',MAX_POSITION), s['cash'])
                    qty = math.floor(budget / unit_cost * 1_000_000) / 1_000_000
                    cost = qty * unit_cost
                    if qty <= 0 or cost < 1:
                        decisions.append(dict(symbol=symbol,action="SKIP",reason="Insufficient cash / fractional precision"))
                        continue
                    if cost > min(getattr(self,'purchase_cap',MAX_POSITION),purchase_limit) + 1e-8 or cost > s['cash'] + 1e-8:
                        raise ValueError('Budget check failed')
                    action,reason='BUY','Trend entry; controls and budget passed'
                    s['cash'] -= cost
                    s['positions'][symbol] = dict(qty=qty, cost_gbp=cost,
                        entry_usd=prices[symbol]*(1+self.price_allowance), opened=stamp)
                    if history_rows is not None:
                        row=history_rows[symbol][-1]
                        s['positions'][symbol]['basis_reference']={'day':row['datetime'][:10],'close':positive(row['close'])}
                    ident=uuid.uuid4().hex;gross=qty*prices[symbol]*fx
                    if hasattr(self,'db'):s['positions'][symbol].update(execution_id=ident,entry_fx=fx)
                    executions.append(dict(id=ident,at=stamp,day=date,symbol=symbol,side='BUY',reason=reason,
                        quantity=qty,quote_usd=prices[symbol],execution_usd=prices[symbol]*(1+self.price_allowance),
                        fx_gbp_per_usd=fx,price_allowance=self.price_allowance,fx_allowance=self.fx_allowance,
                        gross_gbp=gross,price_cost_gbp=gross*self.price_allowance,
                        fx_cost_gbp=gross*(1+self.price_allowance)*self.fx_allowance,net_cash_gbp=-cost,
                        cost_basis_gbp=cost,realised_pnl_gbp=None))
                    s['last_buy'][symbol] = date
                    messages.append(f'PAPER BUY {symbol}: {qty:.6f} shares, £{cost:.2f} including modelled costs.')
                else:
                    if p:action,reason='HOLD','No exit signal'
                    elif s['buy_block']:reason='User blocked buys'
                    elif s['risk_latched']:reason='Total drawdown buying lock'
                    elif s['day_block']:reason='Daily drawdown buying block'
                    elif s['last_buy'].get(symbol)==date:reason='Same-day re-entry blocked'
                    elif len(s['positions'])>=getattr(self,'max_positions',MAX_OPEN):reason=('Five-position limit' if getattr(self,'max_positions',MAX_OPEN)==5 else f"{self.max_positions}-position limit")
                decisions.append(dict(symbol=symbol,action=action,reason=reason,execution_id=executions[-1]['id'] if action in ('BUY','SELL') else None))
            s['equity'] = s['cash'] + sum(p['qty'] * prices[k] * fx for k,p in s['positions'].items())
            if s['cash'] < -1e-8:
                raise ValueError('Negative cash rejected')
            if hasattr(self,'db'):self.write(s,messages,executions)
            else:self.write(s,messages)
            return decisions


    def accounting(self,limit=100):
        with self.lock:
            executions=[json.loads(x[0]) for x in self.db.execute('SELECT body FROM executions ORDER BY at DESC,rowid DESC LIMIT ?',(limit,))]
            funding=[json.loads(x[0]) for x in self.db.execute('SELECT body FROM funding ORDER BY at DESC,rowid DESC')]
            summary=self.db.execute("SELECT COUNT(*),COALESCE(SUM(json_extract(body,'$.realised_pnl_gbp')),0),COALESCE(SUM(json_extract(body,'$.price_cost_gbp')+json_extract(body,'$.fx_cost_gbp')),0) FROM executions").fetchone()
            return dict(executions=executions,funding=funding,recorded_executions=summary[0],recorded_realised_gbp=summary[1],recorded_modelled_costs_gbp=summary[2],
                note='Complete execution records start with this update. Earlier trades are not reconstructed. Recorded realised profit and costs cover these records only; deposits are not profit. Execution costs are assumptions, not broker charges.')

    def record_benchmark(self,spy,fx,at,account_at,day):
        """Daily paired observation; restart comparison on any funding change."""
        spy=positive(spy);fx=positive(fx)
        with self.lock:
            s=self.read()
            if s['last_success']!=account_at or s.get('error'):return
            age=(datetime.fromisoformat(at)-datetime.fromisoformat(account_at)).total_seconds()
            if not 0<=age<=120:return
            b=s.get('benchmark');capital=s['contributed_capital'];equity=s['equity']
            # Normalised total account mark versus SPY GBP price index. Costs and
            # dividends excluded from SPY; account trading costs remain included.
            if not b or b['capital']!=capital:
                if b:s['benchmark_history']=(s.get('benchmark_history',[])+[b])[-10:]
                b=dict(start=at,capital=capital,initial_equity=equity,initial_spy_gbp=spy*fx,
                    reset_reason='New comparison period; earlier results excluded. Funding changes restart the comparison.',observations=[])
            if b['observations'] and b['observations'][-1]['day']==day:return
            observation=dict(day=day,at=at,account_at=account_at,equity_gbp=equity,spy_usd=spy,fx=fx,
                account_return_pct=100*(equity/b['initial_equity']-1),spy_price_return_pct=100*(spy*fx/b['initial_spy_gbp']-1),cash_return_pct=0)
            b['observations']=(b['observations']+[observation])[-1000:];s['benchmark']=b;self.write(s)
