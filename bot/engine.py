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
    if data.get('symbol') != symbol:
        raise ValueError('Quote symbol mismatch')
    if currency and data.get('currency') != currency:
        raise ValueError('Quote currency mismatch')
    stamp = positive(data.get('last_quote_at') or data.get('timestamp', 0))
    if not -5 <= now - stamp <= 120:
        raise ValueError('Quote older than two minutes or timestamp in future')
    if data.get('is_market_open') is not True:
        raise ValueError('Market is closed or its status is unavailable')
    return positive(data['close'])


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


class Ledger:
    def __init__(self, path, initial_cash=PAPER_CAPITAL, price_allowance=PRICE_ALLOWANCE, fx_allowance=FX_ALLOWANCE):
        initial_cash = positive(initial_cash)
        if not all(math.isfinite(x) and 0<=x<=.02 for x in (price_allowance,fx_allowance)):raise ValueError('Invalid cost allowances')
        self.price_allowance=price_allowance;self.fx_allowance=fx_allowance
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, message TEXT NOT NULL)')
        initial = dict(mode='paper', paused=True, buy_block=False, risk_latched=False,
                       cash=initial_cash, equity=initial_cash, positions={},
                       contributed_capital=initial_cash,
                       last_buy={}, day='', day_start=initial_cash,
                       last_check=None, last_success=None, error=None,
                       marks={}, fx=None, signals={}, day_block=False)
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
            self.db.execute('UPDATE ledger SET body=? WHERE id=1', (json.dumps(state,allow_nan=False),))
            if addition:
                self.db.execute('INSERT INTO events(at,message) VALUES (?,?)',
                    (datetime.now(timezone.utc).isoformat(),
                     f'PAPER FUNDING: added £{addition:.2f}; total contributed £{funded+addition:.2f}. Not investment profit.'))

    def read(self):
        with self.lock:
            state = json.loads(self.db.execute('SELECT body FROM ledger WHERE id=1').fetchone()[0])
            state['events'] = [dict(at=a, message=m) for a,m in self.db.execute(
                'SELECT at,message FROM events ORDER BY id DESC LIMIT 50')]
            return state

    def write(self, state, messages=()):
        state.pop('events', None)
        with self.db:
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

    def cycle(self, prices, histories, fx, date, stamp, symbols=None, history_rows=None):
        # Validate the complete snapshot before touching cash or holdings.
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
            decisions = []
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
                    del s['positions'][symbol]
                    # A stopped-out stock cannot be re-entered on the same day.
                    s['last_buy'][symbol] = date
                    messages.append(f'PAPER SELL {symbol}: {p["qty"]:.6f} shares, £{proceeds:.2f} proceeds after modelled costs.')
                elif (not p and signals[symbol] == 'buy' and not s['buy_block']
                      and len(s['positions']) < MAX_OPEN
                      and not s['risk_latched'] and not s['day_block']
                      and s['last_buy'].get(symbol) != date):
                    unit_cost = prices[symbol] * (1+self.price_allowance) * fx * (1+self.fx_allowance)
                    budget = min(MAX_POSITION, s['cash'])
                    qty = math.floor(budget / unit_cost * 1_000_000) / 1_000_000
                    cost = qty * unit_cost
                    if qty <= 0 or cost < 1:
                        decisions.append(dict(symbol=symbol,action="SKIP",reason="Insufficient cash / fractional precision"))
                        continue
                    if cost > MAX_POSITION + 1e-8 or cost > s['cash'] + 1e-8:
                        raise ValueError('Budget check failed')
                    action,reason='BUY','Trend entry; controls and budget passed'
                    s['cash'] -= cost
                    s['positions'][symbol] = dict(qty=qty, cost_gbp=cost,
                        entry_usd=prices[symbol]*(1+self.price_allowance), opened=stamp)
                    if history_rows is not None:
                        row=history_rows[symbol][-1]
                        s['positions'][symbol]['basis_reference']={'day':row['datetime'][:10],'close':positive(row['close'])}
                    s['last_buy'][symbol] = date
                    messages.append(f'PAPER BUY {symbol}: {qty:.6f} shares, £{cost:.2f} including modelled costs.')
                else:
                    if p:action,reason='HOLD','No exit signal'
                    elif s['buy_block']:reason='User blocked buys'
                    elif s['risk_latched']:reason='Total drawdown buying lock'
                    elif s['day_block']:reason='Daily drawdown buying block'
                    elif s['last_buy'].get(symbol)==date:reason='Same-day re-entry blocked'
                    elif len(s['positions'])>=MAX_OPEN:reason='Five-position limit'
                decisions.append(dict(symbol=symbol,action=action,reason=reason))
            s['equity'] = s['cash'] + sum(p['qty'] * prices[k] * fx for k,p in s['positions'].items())
            if s['cash'] < -1e-8:
                raise ValueError('Negative cash rejected')
            self.write(s, messages)
            return decisions
