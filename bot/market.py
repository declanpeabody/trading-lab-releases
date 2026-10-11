"""Read-only Twelve Data transport. Network errors fail closed."""
import json
import os
import time
import threading
import sqlite3
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from datetime import datetime
from zoneinfo import ZoneInfo
from engine import SYMBOLS, quote, history


class ApiBudget:
    """Conservative one-credit endpoint budget, shared with historical research.

    Persist attempts before requests, including failures. This is a local count,
    not the provider balance: other apps using the key are not visible here.
    """
    def __init__(self, directory):
        self.path = Path(directory) / 'api_budget.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def reserve(self, priority=False):
        day = datetime.now(ZoneInfo('UTC')).date().isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY, n INTEGER)')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT n FROM usage WHERE day=?', (day,)).fetchone()
            used = row[0] if row else 0
            if used >= (700 if priority else 500):
                raise ValueError('Local daily API budget reached; candidate scans/research stop at 500, held-position checks at 700. No subscription was changed.')
            db.execute('INSERT OR REPLACE INTO usage VALUES (?,?)', (day, used + 1))

    def used(self):
        if not self.path.exists(): return 0
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY, n INTEGER)')
            row = db.execute('SELECT n FROM usage WHERE day=?',
                             (datetime.now(ZoneInfo('UTC')).date().isoformat(),)).fetchone()
            return row[0] if row else 0


class Market:
    request_lock = threading.Lock()
    last_global_request = 0.0
    def __init__(self):
        self.cache = {}
        self.last_request = 0

    def get(self, endpoint, *, priority=False, **params):
        key = os.environ.get('TWELVE_DATA_API_KEY', '')
        if not key:
            raise ValueError('Setup needed: add the market-data API key using the Mac setup tool.')
        # Stay below eight calls/minute. No automatic retries and no URL logging.
        with Market.request_lock:
            if endpoint not in ('quote', 'time_series', 'stocks'):
                raise ValueError('Endpoint has no configured API credit weight')
            ApiBudget(os.getenv('DATA_DIR', '/data')).reserve(priority)
            time.sleep(max(0, 8 - (time.monotonic() - Market.last_global_request)))
            Market.last_global_request = time.monotonic()
        url = 'https://api.twelvedata.com/' + endpoint + '?' + urlencode(params)
        request = Request(url, headers={'Authorization': 'apikey ' + key, 'Accept': 'application/json'})
        try:
            with urlopen(request, timeout=20) as response:
                data = json.load(response)
        except Exception:
            raise ValueError('Market-data connection failed; no trades made.') from None
        if data.get('status') == 'error':
            raise ValueError('Market-data request rejected; check key, quota and data entitlement.')
        return data

    def snapshot(self):
        now = datetime.now(ZoneInfo('America/New_York'))
        date = now.date().isoformat()
        histories = {}
        for symbol in SYMBOLS:
            cache_key = (date, symbol)
            if cache_key not in self.cache:
                result = self.get('time_series', symbol=symbol, interval='1day',
                                  outputsize=60, adjust='splits', order='asc')
                self.cache[cache_key] = history(result, symbol, date)
            histories[symbol] = self.cache[cache_key]
        self.cache = {k:v for k,v in self.cache.items() if k[0] == date}
        # Quotes last, so the history requests cannot make them stale.
        data = {s:self.get('quote', symbol=s) for s in SYMBOLS}
        fx_data = self.get('quote', symbol='USD/GBP')
        stamp = time.time()
        prices = {s:quote(data[s], s, stamp, 'USD') for s in SYMBOLS}
        fx = quote(fx_data, 'USD/GBP', stamp)
        return prices, histories, fx, date, datetime.now(ZoneInfo('UTC')).isoformat()


def session_window(now):
    # Provider's market-open flag also excludes holidays and early closes.
    minutes = now.hour * 60 + now.minute
    return now.weekday() < 5 and 570 <= minutes < 960
