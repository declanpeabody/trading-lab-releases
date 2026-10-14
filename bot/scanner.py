"""Bounded US regular-hours scanner; no broker connection or paid data.

Six rotating candidates per check, held positions always included. A complete
sweep takes many sessions; this is not a real-time whole-market screener.
"""
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from engine import history, positive, quote
from market import Market, ApiBudget, session_window

BATCH = 6
LEGACY = {s: 'NASDAQ' for s in ('AAPL', 'MSFT', 'AMZN')}


def common_stock_candidate(symbol, name=''):
    # Reject explicit security suffixes/names, not every five-letter ticker.
    # A ticker alone cannot prove instrument identity; provider type still applies.
    if re.search(r'(?:[.-](?:WT|WS|W|UN|U|RT|R)|[.-]P(?:R)?[.-]?[A-Z]*)$',symbol):return False
    return not re.search(r'\b(warrants?|preferred|depositary shares|rights|units)\b',name,re.I)


def catalogue(rows):
    found, ambiguous = {}, set()
    for row in rows:
        symbol, exchange = row.get('symbol', ''), row.get('exchange')
        if (row.get('country') != 'United States' or row.get('currency') != 'USD'
                or row.get('type') != 'Common Stock' or exchange not in ('NASDAQ', 'NYSE')
                or not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,14}', symbol)
                or not common_stock_candidate(symbol,str(row.get('name','')))):
            continue
        if symbol in found and found[symbol] != exchange:
            ambiguous.add(symbol)
        found[symbol] = exchange
    return {s: found[s] for s in sorted(found) if s not in ambiguous}


def select_batch(universe, held, cursor):
    names = list(universe)
    if not names: return [], 0
    selected, visited = [], 0
    while len(selected) < BATCH and visited < len(names):
        symbol = names[(cursor + visited) % len(names)]
        if symbol not in held: selected.append(symbol)
        visited += 1
    return selected, (cursor + visited) % len(names)


def liquid_history(data, today):
    rows = sorted((r for r in data['values'] if r['datetime'][:10] < today),
                  key=lambda r: r['datetime'])[-20:]
    if len(rows) != 20: return False
    try:
        return sum(positive(r['close']) * positive(r['volume']) for r in rows) / 20 >= 1_000_000
    except (ValueError, TypeError, KeyError):
        return False


class Scanner:
    def __init__(self, directory, market=None):
        self.directory = Path(directory)
        self.market = market or Market()
        self.path = self.directory / 'scanner.json'
        self.cache = {}
        self.state = {'universe': {}, 'fetched': 0, 'cursor': 0,
                      'day': '', 'attempts_today': 0}
        if self.path.exists():
            self.state.update(json.loads(self.path.read_text()))
        self.status = {}

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.state, allow_nan=False))
        tmp.chmod(0o600)
        os.replace(tmp, self.path)

    def cycle(self, ledger):
        now = datetime.now(ZoneInfo('America/New_York'))
        if not session_window(now) or ledger.read()['paused']: return
        today = now.date().isoformat()
        held = list(ledger.read()['positions'])
        skipped = []
        self.status = {'mode': 'Free Basic · US regular hours', 'checked': [], 'skipped': []}
        # Catalogue errors must not prevent checks of already-held instruments.
        if time.time() - self.state['fetched'] > 7 * 86400:
            try:
                result = self.market.get('stocks', country='United States', type='Common Stock')
                universe = catalogue(result['data'])
                if not universe: raise ValueError('No supported stocks returned by catalogue')
                # Retain instrument mapping for positions no longer in catalogue.
                known = self.state.setdefault('known', {})
                known.update(universe)
                self.state.update(universe=universe, fetched=time.time())
                self.save()
            except (ValueError, KeyError, TypeError):
                skipped.append('Catalogue unavailable; using cached list if available. Held stocks remain prioritised.')
        universe = self.state['universe']
        candidates, cursor = select_batch(universe, held, self.state['cursor'])
        if self.state['day'] != today:
            self.state.update(day=today, attempts_today=0)
        self.state['cursor'] = cursor
        self.state['attempts_today'] += len(candidates)
        self.save()
        self.status.update(universe_count=len(universe), selected=candidates,
                           attempts_today=self.state['attempts_today'], skipped=skipped,
                           catalogue_at=self.state['fetched'])
        mapping = LEGACY | self.state.get('known', {}) | universe
        histories, raw = {}, {}
        self.cache = {k:v for k,v in self.cache.items() if k[0] == today}
        # Complete histories before fetching quotes, to avoid ageing those quotes.
        for symbol in held + candidates:
            try:
                if symbol not in mapping: raise ValueError('Unknown exchange for held stock')
                cache_key = (today, symbol, mapping[symbol])
                if cache_key not in self.cache:
                    result = self.market.get('time_series', priority=symbol in held,
                        symbol=symbol, exchange=mapping[symbol], interval='1day',
                        outputsize=60, adjust='splits', order='asc')
                    if result.get('meta', {}).get('exchange') != mapping[symbol]:
                        raise ValueError('History exchange mismatch')
                    self.cache[cache_key] = (history(result, symbol, today), liquid_history(result, today))
                closes, liquid = self.cache[cache_key]
                if symbol not in held and not liquid:
                    raise ValueError('Missing/low 20-day average dollar volume (minimum $1m)')
                histories[symbol] = closes
            except (ValueError, KeyError, TypeError) as exc:
                if symbol in held: raise ValueError(f'Held stock {symbol}: data unavailable; no trades made.') from None
                skipped.append(f'{symbol}: {exc}')
        for symbol in histories:
            try:
                q = self.market.get('quote', priority=symbol in held, symbol=symbol,
                                    exchange=mapping[symbol], prepost='false')
                if q.get('exchange') != mapping[symbol]: raise ValueError('Quote exchange mismatch')
                raw[symbol] = q
            except (ValueError, KeyError, TypeError) as exc:
                if symbol in held: raise ValueError(f'Held stock {symbol}: quote unavailable; no trades made.') from None
                skipped.append(f'{symbol}: {exc}')
        if not raw:
            raise ValueError('No valid stock data this scan. See scanner coverage and skip reasons.')
        fx_data = self.market.get('quote', priority=bool(held), symbol='USD/GBP')
        stamp = time.time()
        prices = {}
        for symbol, q in raw.items():
            try:
                price = quote(q, symbol, stamp, 'USD')
                if q.get('is_extended_hours') is True:
                    raise ValueError('Extended-hours quotes disabled on Basic')
                if symbol not in held and price < 1: raise ValueError('New entries below $1 excluded')
                prices[symbol] = price
            except (ValueError, KeyError, TypeError) as exc:
                if symbol in held: raise ValueError(f'Held stock {symbol}: stale/invalid price; no trades made.') from None
                skipped.append(f'{symbol}: {exc}')
        fx = quote(fx_data, 'USD/GBP', stamp)
        self.status['checked'] = list(prices)
        if not prices: raise ValueError('No fresh regular-hours quotes; no trades made.')
        if not session_window(datetime.now(ZoneInfo('America/New_York'))):
            raise ValueError('Regular session ended during scan; no trades made.')
        ledger.cycle(prices, histories, fx, today, datetime.now(ZoneInfo('UTC')).isoformat(),
                     symbols=list(prices))

    def record_status(self, ledger):
        with ledger.lock:
            state = ledger.read()
            state['scanner'] = self.status | {'api_used_today': ApiBudget(self.directory).used(),
                'updated_at': datetime.now(ZoneInfo('UTC')).isoformat()}
            ledger.write(state)
