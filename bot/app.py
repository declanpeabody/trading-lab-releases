"""Single-process cloud service: authenticated dashboard + background scheduler."""
import base64
import fcntl
import hmac
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from engine import Ledger
from market import Market, session_window, validate_key, MarketUnavailable, ApiBudget, ConnectionTest
from research import Research, Experiments
from ranked_scanner import RankedScanner, Journal
from news import News
from updater import Updates
from version import VERSION

ROOT = Path(__file__).parent
STOP = threading.Event()
WORKER = {'state':'starting','message':'Worker starting','heartbeat':time.time(),'due':time.time()+900}


def worker_health(thread):
    info=dict(WORKER);alive=thread.is_alive()
    overdue=time.time()>info['due']+900
    return info|{'alive':alive,'healthy':alive and not overdue and info['state']!='failed',
        'message':('Trading worker stopped; restart the service.' if not alive else
                   'Trading worker overdue; check the Mac service.' if overdue else info['message'])}


def worker(ledger):
    global WORKER
    scanner=None
    while not STOP.is_set():
        delay=900
        WORKER={'state':'checking','message':'Checking worker and account','heartbeat':time.time(),'due':time.time()}
        try:
            if scanner is None:
                directory=os.getenv('DATA_DIR','/data')
                scanner=RankedScanner(directory,experiments=Experiments(directory))
            if not (Path(os.getenv('DATA_DIR','/data'))/'update_hold').exists() and not ledger.read()['paused'] and session_window(datetime.now(ZoneInfo('America/New_York'))):
                try:scanner.cycle(ledger)
                except ValueError as exc:ledger.fail(str(exc))
                scanner.record_status(ledger)
            WORKER={'state':'waiting','message':'Worker active; waiting for the next scheduled check','heartbeat':time.time(),'due':time.time()+delay}
        except Exception:
            # Disk/database/init/status errors must not escape and silently kill this thread.
            delay=60;scanner=None
            message='Worker processing or storage failure. Decisions stopped; retrying in one minute. Check disk space if this persists.'
            WORKER={'state':'failed','message':message,'heartbeat':time.time(),'due':time.time()+delay}
            try:ledger.fail(message)
            except Exception:pass  # Health remains available in memory even when disk is full.
        STOP.wait(delay)


def top_navigation(path,nonce):
    links=[('/#overview','Overview'),('/#positions','Positions'),('/#scanner','Scanner'),('/#markets','Markets'),
           ('/research','Research & Journal'),('/research#experiments','Strategy Experiments'),('/news','News'),('/#rules','Rules & Limits'),('/updates','Software Updates')]
    style='<style nonce="'+nonce+'">.top-nav{display:flex;flex-wrap:wrap;gap:8px;margin:20px 0;padding:0 0 16px;border-bottom:1px solid #2b3545}.top-nav a{display:inline-flex;align-items:center;min-height:44px;padding:10px 14px;border:1px solid #3f4d63;border-radius:7px;color:#c6d0df;background:#111925;text-decoration:none;font:600 13px system-ui}.top-nav a[aria-current]{color:#101620;background:#d8f36a;border-color:#d8f36a}.top-nav a:focus-visible{outline:3px solid #a8ceff;outline-offset:3px}</style>'
    return style+'<nav class="top-nav" aria-label="Dashboard navigation">'+''.join(
        '<a href="'+href+'"'+(' aria-current="page"' if href==path else '')+'>'+label.replace('&','&amp;')+'</a>' for href,label in links)+'</nav>'


def no_trade_reasons(state):
    reasons=[];scan=state.get('scanner',{})
    if not state.get('worker',{}).get('healthy',True):reasons.append('Worker needs attention: '+state['worker']['message'])
    if state.get('paused'):reasons.append('Paper trading is paused.')
    if not session_window(datetime.now(ZoneInfo('America/New_York'))):reasons.append('Outside the weekday 09:30–16:00 New York trading window. Provider market-open checks also apply.')
    if not state.get('data_configured'):reasons.append('Market-data key is missing.')
    if state.get('error'):reasons.append('Last account/data error: '+state['error'])
    for key,message in [('buy_block','New buys are blocked by your control.'),('risk_latched','Total drawdown buying lock is active.'),('day_block','Daily drawdown buying block is recorded; it is reassessed on the next successful session check.')]:
        if state.get(key):reasons.append(message)
    if any(p.get('reconciliation') for p in state.get('positions',{}).values()):reasons.append('Corporate-action verification is required before account decisions resume.')
    if len(state.get('positions',{}))>=5:reasons.append('All five position slots are occupied.')
    if state.get('cash',0)<=0:reasons.append('No cash is available for new purchases.')
    if not scan:reasons.append('No scanner result has been recorded yet.')
    elif not scan.get('ranked_histories'):reasons.append('Latest decision check had no rankable histories. Prepared histories enter the next check.')
    elif not scan.get('selected'):reasons.append('Latest check selected no new-entry candidates from the available histories.')
    elif not set(scan.get('selected',[])).intersection(scan.get('checked',[])):reasons.append('Latest check had no valid fresh candidate quotes.')
    if scan and scan.get('ranked_histories',0)<scan.get('universe_count',0):reasons.append('Ranking coverage is partial: only ready stocks can enter; missing coverage alone does not block all entries.')
    if not reasons:reasons.append('No global block identified. See the latest per-stock decisions below; an open market does not guarantee a buy signal.')
    return {'reasons':reasons,'at':scan.get('decision_at',scan.get('updated_at')),'decisions':scan.get('decisions',[])}


def handler_factory(ledger, username, password, thread, research=None, news=None, updates=None, journal=None, experiments=None):
    connection_test=ConnectionTest()
    csrf = secrets.token_urlsafe(32)
    auth = 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No credential, URL or account logging.

        def respond(self, status, body, mime='application/json', extra=None):
            encoded = body.encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            for key,value in (extra or {}).items(): self.send_header(key, value)
            self.end_headers()
            self.wfile.write(encoded)

        def authorized(self):
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), auth.encode()):
                self.respond(401, '{"error":"Sign in required"}', extra={'WWW-Authenticate':'Basic realm="Trading lab", charset="UTF-8"'})
                return False
            return True

        def do_GET(self):
            if self.path == '/healthz':
                healthy=worker_health(thread)['healthy']
                self.respond(200 if healthy else 503, json.dumps({'running':healthy,'version':VERSION}))
                return
            if not self.authorized(): return
            if self.path == '/api/state':
                try:state = ledger.read()
                except Exception:
                    self.respond(503,json.dumps({'error':'Account storage unavailable','worker':worker_health(thread)}));return
                state['worker']=worker_health(thread)
                if experiments:
                    try:state['active_purchase_limit']=experiments.active_limit()
                    except Exception:state['experiment_error']='Experiment settings unavailable; check Strategy Experiments.'
                state['csrf'] = csrf
                state['data_configured'] = bool(os.getenv('TWELVE_DATA_API_KEY'))
                # Pure local validation also works outside trading hours. Never
                # include the key or make a provider request during page loads.
                if state['data_configured']:
                    try:validate_key(os.environ['TWELVE_DATA_API_KEY'])
                    except MarketUnavailable as exc:state['error']=str(exc)
                state['connection_test']=connection_test.state()
                try:state['request_budget']=ApiBudget(os.getenv('DATA_DIR','/data')).state()
                except (OSError,sqlite3.Error):state['request_budget']={'error':'Local request counter unavailable; check storage. No allowance estimate is available.'}
                state['no_trade']=no_trade_reasons(state)
                self.respond(200, json.dumps(state, allow_nan=False))
            elif self.path in ('/api/experiments','/api/experiments/export') and experiments:
                extra={'Content-Disposition':'attachment; filename="strategy-experiments.json"'} if self.path.endswith('/export') else None
                self.respond(200,json.dumps(experiments.state(),allow_nan=False),extra=extra)
            elif self.path == '/api/research' and research:
                self.respond(200, json.dumps(research.state(), allow_nan=False))
            elif self.path == '/api/news' and news:
                self.respond(200, json.dumps(news.state(), allow_nan=False))
            elif self.path in ('/api/journal','/api/journal/export') and journal:
                limit=10000 if self.path.endswith('/export') else 200
                extra={'Content-Disposition':'attachment; filename="decision-journal.json"'} if limit==10000 else None
                self.respond(200,json.dumps(journal.state(limit),allow_nan=False),extra=extra)
            elif self.path == '/api/updates' and updates:
                self.respond(200, json.dumps(updates.state()))
            elif self.path == '/api/news/export' and news:
                self.respond(200, json.dumps(news.state(all_events=True), allow_nan=False),
                             extra={'Content-Disposition':'attachment; filename="news-observations.json"'})
            elif self.path.startswith('/api/export/') and research:
                kind = self.path.removeprefix('/api/export/')
                try:
                    body, mime = research.export(kind)
                    filename = {'json':'backtest.json','equity':'equity.csv','trades':'trades.csv'}[kind]
                    self.respond(200, body, mime, {'Content-Disposition':'attachment; filename="'+filename+'"'})
                except (ValueError, KeyError):
                    self.respond(400, '{"error":"Export unavailable"}')
            elif self.path in ('/', '/research', '/news', '/updates'):
                nonce = secrets.token_urlsafe(24)
                body = (ROOT/({'/research':'research.html','/news':'news.html','/updates':'updates.html'}.get(self.path,'dashboard.html'))).read_text().replace('__NONCE__',nonce).replace('__TOP_NAV__',top_navigation(self.path,nonce))
                self.respond(200, body, 'text/html; charset=utf-8', {
                    'Content-Security-Policy':f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; frame-src https://www.tradingview-widget.com; img-src data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
            else:
                self.respond(404, '{"error":"Not found"}')

        def do_POST(self):
            if not self.authorized(): return
            if self.path not in ('/api/control', '/api/research', '/api/news', '/api/updates', '/api/experiments', '/api/connection-test'):
                self.respond(404, '{"error":"Not found"}')
                return
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),csrf):
                self.respond(403, '{"error":"Refresh the page and try again"}')
                return
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= (16000 if self.path=='/api/updates' else 1024): raise ValueError()
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload,dict):raise ValueError('Expected an object')
                if self.path == '/api/connection-test':
                    connection_test.start()
                elif self.path == '/api/experiments' and experiments:
                    experiments.control(payload['action'],payload.get('enabled'))
                elif self.path == '/api/research' and research:
                    research.start(payload)
                elif self.path == '/api/news' and news:
                    news.configure(payload)
                elif self.path == '/api/updates' and updates:
                    if payload.get('action')=='check':updates.check()
                    elif payload.get('action')=='install':updates.install(payload['version'])
                    elif payload.get('action')=='automatic':updates.set_automatic(payload['enabled'])
                    elif payload.get('action')=='configure':updates.configure(payload['repo'],payload['public_key'])
                    else:raise ValueError('Unknown update control')
                elif self.path == '/api/control':
                    if payload['action']=='reconcile':
                        ledger.reconcile(payload['symbol'],payload['id'],payload['ratio'],payload['source'])
                    else:ledger.control(payload['action'])
                else:
                    raise ValueError('Research unavailable')
            except (ValueError, KeyError, TypeError) as exc:
                self.respond(400, json.dumps({'error':str(exc) if self.path in ('/api/news','/api/updates','/api/connection-test') else 'Invalid control'}))
                return
            self.respond(200, '{"ok":true}')

    return Handler


def main():
    password = os.environ.get('DASHBOARD_PASSWORD','')
    if len(password) < 20 or password.startswith('REPLACE'):
        raise SystemExit('Set a unique DASHBOARD_PASSWORD of at least 20 characters in your local configuration.')
    directory = Path(os.getenv('DATA_DIR', '/data'))
    directory.mkdir(parents=True, exist_ok=True)
    lock = (directory/'worker.lock').open('w')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('Another worker is already using this data disk.')
    ledger = Ledger(directory/'paper.sqlite3')
    news = News(directory)
    news_thread = threading.Thread(target=news.worker, args=(ledger, STOP), daemon=True)
    news_thread.start()
    updates=Updates(ROOT) if sys.platform=='darwin' else None
    if updates:threading.Thread(target=updates.worker,args=(STOP,),daemon=True).start()
    thread = threading.Thread(target=worker, args=(ledger,), daemon=True)
    thread.start()
    server = ThreadingHTTPServer((os.getenv('BIND_HOST', '127.0.0.1'), int(os.getenv('PORT','8080'))),
        handler_factory(ledger,os.getenv('DASHBOARD_USER','declan'),password,thread,Research(directory),news,updates,Journal(directory),Experiments(directory)))
    print('Paper-trading dashboard ready; real-money execution is not implemented.', flush=True)
    try: server.serve_forever()
    finally:
        STOP.set()
        news.wake.set()
        server.server_close()
        lock.close()


if __name__ == '__main__': main()
