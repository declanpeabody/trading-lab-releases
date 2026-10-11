"""Persistent paper broker and explicit, unproven trend strategy. No real orders."""
import copy
import json
import math
import sqlite3
import threading
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
    def __init__(self, path, initial_cash=PAPER_CAPITAL):
        initial_cash = positive(initial_cash)
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

    def cycle(self, prices, histories, fx, date, stamp, symbols=None):
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
                    proceeds = p['qty'] * prices[symbol] * (1-PRICE_ALLOWANCE) * fx * (1-FX_ALLOWANCE)
                    s['cash'] += proceeds
                    del s['positions'][symbol]
                    # A stopped-out stock cannot be re-entered on the same day.
                    s['last_buy'][symbol] = date
                    messages.append(f'PAPER SELL {symbol}: {p["qty"]:.6f} shares, £{proceeds:.2f} proceeds after modelled costs.')
                elif (not p and signals[symbol] == 'buy' and not s['buy_block']
                      and len(s['positions']) < MAX_OPEN
                      and not s['risk_latched'] and not s['day_block']
                      and s['last_buy'].get(symbol) != date):
                    unit_cost = prices[symbol] * (1+PRICE_ALLOWANCE) * fx * (1+FX_ALLOWANCE)
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
                        entry_usd=prices[symbol]*(1+PRICE_ALLOWANCE), opened=stamp)
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
