"""Read-only Twelve Data transport. Network errors fail closed."""
import math
import json
import os
import time
import threading
import sqlite3
import socket
import ssl
from urllib.error import HTTPError, URLError
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from datetime import datetime, timedelta
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

    @staticmethod
    def history_headroom(now=None):
        now=now or datetime.now(ZoneInfo('America/New_York'))
        if now.weekday()>=5 or now.hour*60+now.minute>=960:return 0
        minutes=max(570,now.hour*60+now.minute)
        return math.ceil((960-minutes)/15)*7

    @staticmethod
    def monitoring_headroom(now=None):
        """Reserve five stock quotes + FX per five-minute slot, plus recovery slack."""
        now=now or datetime.now(ZoneInfo('America/New_York'))
        if now.weekday()>=5 or now.hour*60+now.minute>=960:return 0
        minutes=max(570,now.hour*60+now.minute)
        return math.ceil((960-minutes)/5)*6+30

    def state(self):
        used=self.used();now=datetime.now(ZoneInfo('UTC'))
        reset=(now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
        headroom=self.history_headroom();monitoring=self.monitoring_headroom();normal=max(0,min(500,700-monitoring)-used)
        return dict(used=used,normal_remaining=normal,total_remaining=max(0,700-used),
            history_available=max(0,min(normal,500-used-headroom)),candidate_reserve=headroom,monitoring_reserve=monitoring,
            reset_at=reset.isoformat(),research_daily_cap=50,
            note='Local attempts only; other apps and provider credit weights are not included. Optional requests preserve six requests per remaining five-minute weekday slot plus 30 recovery credits for holdings. Actual coverage is not guaranteed.')

    def reserve(self, priority=False, purpose='trading'):
        if purpose not in ('trading','history','research','diagnostic'):raise ValueError('Unknown request purpose')
        if priority and purpose!='trading':raise ValueError('Reserved credits are for active holdings only')
        if purpose=='research' and session_window(datetime.now(ZoneInfo('America/New_York'))):
            raise BudgetDeferred('Research downloads deferred during US regular hours. Run again after the session; existing downloads are cached.')
        day = datetime.now(ZoneInfo('UTC')).date().isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY, n INTEGER)')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT n FROM usage WHERE day=?', (day,)).fetchone()
            used = row[0] if row else 0
            if used >= (700 if priority else 500):
                raise ValueError('Local daily API budget reached; candidate scans/research stop at 500, held-position checks at 700. No subscription was changed.')
            if not priority and used>=700-self.monitoring_headroom():
                raise BudgetDeferred('Optional requests deferred to preserve remaining five-minute holdings checks and recovery credits.')
            if purpose=='history' and used>=500-self.history_headroom():
                raise BudgetDeferred('History preparation deferred to preserve credits for later candidate checks.')
            if purpose=='research':
                db.execute('CREATE TABLE IF NOT EXISTS research_usage(day TEXT PRIMARY KEY, n INTEGER)')
                row=db.execute('SELECT n FROM research_usage WHERE day=?',(day,)).fetchone()
                count=row[0] if row else 0
                if count>=50:raise BudgetDeferred('Research daily allowance reached (50 requests within the shared 500); cached downloads are retained.')
                db.execute('INSERT OR REPLACE INTO research_usage VALUES (?,?)',(day,count+1))
            db.execute('INSERT OR REPLACE INTO usage VALUES (?,?)', (day, used + 1))

    def used(self):
        if not self.path.exists(): return 0
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY, n INTEGER)')
            row = db.execute('SELECT n FROM usage WHERE day=?',
                             (datetime.now(ZoneInfo('UTC')).date().isoformat(),)).fetchone()
            return row[0] if row else 0


class BudgetDeferred(ValueError):
    """A scheduling deferral; do not mark a stock unavailable."""


class MarketUnavailable(ValueError):
    """A shared transport/account failure, not an individual stock rejection."""


def validate_key(value, allow_empty=False):
    key=value.strip()
    if not key:
        if allow_empty:return key
        raise MarketUnavailable('Setup needed: add the market-data API key using the Mac setup tool.')
    if any(not 33<=ord(character)<=126 for character in key):
        raise MarketUnavailable('Market data: the saved API key contains a space, line break or unsupported hidden character. Copy only the key, then use Mac setup option 5 to replace it. No request was sent.')
    return key


def response_error(code):
    # Never display response bodies, request URLs, exception text or credentials.
    try:code=int(code)
    except (TypeError,ValueError):code=0
    if code==401:return MarketUnavailable('Market data: authentication rejected (401). Re-enter the Twelve Data key in Mac setup.')
    if code==403:return MarketUnavailable('Market data: access denied (403). Check the key and endpoint/data entitlement in Twelve Data.')
    if code==429:return MarketUnavailable('Market data: provider rate or credit limit reached (429). Wait for the provider allowance to recover.')
    if code in (400,404,422):return ValueError(f'Market data: request rejected ({code}); check the stock, exchange and endpoint parameters.')
    if 500<=code<=599:return MarketUnavailable(f'Market data: provider service error ({code}). Retry at the next scheduled check.')
    if 100<=code<=599:return MarketUnavailable(f'Market data: unexpected HTTP/API status ({code}). Check the provider service.')
    return MarketUnavailable('Market data: provider returned an error without a recognised status code.')


def connection_error(exc):
    reason=exc.reason if isinstance(exc,URLError) else exc
    if isinstance(reason,ssl.SSLCertVerificationError):
        return MarketUnavailable('Market data: TLS certificate verification failed. Repair the certificates for the Python installation used by the bot; do not disable verification.')
    if isinstance(reason,ssl.SSLError):return MarketUnavailable('Market data: secure TLS connection failed. Check the Mac clock and network security settings.')
    if isinstance(reason,socket.gaierror):return MarketUnavailable('Market data: DNS lookup failed for api.twelvedata.com. Check the Mac internet/DNS connection.')
    if isinstance(reason,TimeoutError):return MarketUnavailable('Market data: connection timed out. Check the Mac internet connection and provider availability.')
    return MarketUnavailable('Market data: network connection failed. Check internet access, firewall, VPN or proxy settings on the Mac.')


class Market:
    request_lock = threading.Lock()
    last_global_request = 0.0
    blocked_until = 0.0
    blocked_message = ""
    def __init__(self):
        self.cache = {}
        self.last_request = 0

    def get(self, endpoint, *, priority=False, purpose="trading", **params):
        key = validate_key(os.environ.get('TWELVE_DATA_API_KEY', ''))
        if endpoint not in ('quote', 'time_series', 'stocks'):
            raise ValueError('Endpoint has no configured API credit weight')
        # Share spacing and outage protection with research; never retry inside a request.
        with Market.request_lock:
            if time.monotonic()<Market.blocked_until:
                raise MarketUnavailable(Market.blocked_message+' Requests paused for up to 15 minutes after the failure; no additional API credit used.')
            ApiBudget(os.getenv('DATA_DIR', '/data')).reserve(priority, purpose)
            time.sleep(max(0, 8 - (time.monotonic() - Market.last_global_request)))
            Market.last_global_request = time.monotonic()
            url = 'https://api.twelvedata.com/' + endpoint + '?' + urlencode(params)
            failure=None
            try:
                request = Request(url, headers={'Authorization': 'apikey ' + key, 'Accept': 'application/json'})
                with urlopen(request, timeout=20) as response:
                    try:data = json.load(response)
                    except (json.JSONDecodeError,UnicodeDecodeError):
                        failure=MarketUnavailable('Market data: an HTTP response was received but its body was not valid JSON. Check provider availability or a network proxy.')
                if failure:pass
                elif not isinstance(data,dict):
                    failure=MarketUnavailable('Market data: unexpected response format; expected a JSON object.')
                elif data.get('status')=='error':failure=response_error(data.get('code'))
            except HTTPError as exc:
                failure=response_error(exc.code)
                exc.close()
            except (URLError,OSError) as exc:
                failure=connection_error(exc)
            except ValueError:
                # urllib may reject a malformed header before any HTTP response.
                # Never expose its exception text: it can contain the API key.
                failure=MarketUnavailable('Market data: Python rejected the request or response handling before JSON decoding. Check the saved key and Mac proxy configuration. No provider response could be decoded.')
            if failure:
                if isinstance(failure,MarketUnavailable):
                    Market.blocked_until=time.monotonic()+900
                    Market.blocked_message=str(failure)
                raise failure from None
            Market.blocked_until=0;Market.blocked_message=''
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


class ConnectionTest:
    """Explicit, rate-limited read-only diagnostic; account state is untouched."""
    def __init__(self):
        self.lock=threading.Lock();self.last_started=None
        self.result={'running':False,'at':None,'ok':None,'message':'No connection test since this service started.'}

    def state(self):
        with self.lock:return dict(self.result)

    def start(self):
        with self.lock:
            if self.result['running']:raise ValueError('A connection test is already running.')
            if self.last_started is not None and time.monotonic()-self.last_started<60:
                raise ValueError('Wait one minute between connection tests.')
            self.last_started=time.monotonic()
            self.result={'running':True,'at':datetime.now(ZoneInfo('UTC')).isoformat(),'ok':None,'message':'Testing one AAPL daily-history request…'}
        threading.Thread(target=self._run,daemon=True).start()

    def _run(self):
        ok=False
        try:
            data=Market().get('time_series',purpose='diagnostic',symbol='AAPL',exchange='NASDAQ',interval='1day',outputsize=1)
            try:
                meta=data['meta'];row=data['values'][0];value=float(row['close'])
                datetime.strptime(row['datetime'][:10],'%Y-%m-%d')
                valid=meta['symbol']=='AAPL' and meta['exchange']=='NASDAQ' and meta['currency']=='USD' and math.isfinite(value) and value>0
            except (KeyError,TypeError,ValueError,IndexError):valid=False
            if not valid:raise MarketUnavailable('Connection returned JSON, but the AAPL daily-history data was incomplete or invalid.')
            ok=True;message='Connection passed: authenticated AAPL daily-history data received. This does not verify fresh trading quotes, catalogue access or a successful account check.'
        except ValueError as exc:message=str(exc)
        except Exception:message='Connection test could not finish. Check local service storage and network availability.'
        with self.lock:self.result={'running':False,'at':datetime.now(ZoneInfo('UTC')).isoformat(),'ok':ok,'message':message}
