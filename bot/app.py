"""Single-process cloud service: authenticated dashboard + background scheduler."""
import base64
import fcntl
import hmac
import json
import os
import secrets
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from engine import Ledger
from market import Market, session_window
from research import Research
from ranked_scanner import RankedScanner, Journal
from news import News
from updater import Updates
from version import VERSION

ROOT = Path(__file__).parent
STOP = threading.Event()


def worker(ledger):
    scanner = RankedScanner(os.getenv('DATA_DIR', '/data'))
    while not STOP.is_set():
        if not (Path(os.getenv('DATA_DIR','/data'))/'update_hold').exists() and not ledger.read()['paused'] and session_window(datetime.now(ZoneInfo('America/New_York'))):
            try:
                scanner.cycle(ledger)
            except ValueError as exc:
                ledger.fail(str(exc))
            except Exception:
                ledger.fail('Unexpected data or processing error; no new decisions until the next valid check.')
            finally:
                scanner.record_status(ledger)
        STOP.wait(900)


def handler_factory(ledger, username, password, thread, research=None, news=None, updates=None, journal=None):
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
                self.respond(200 if thread.is_alive() else 503, json.dumps({'running':thread.is_alive(),'version':VERSION}))
                return
            if not self.authorized(): return
            if self.path == '/api/state':
                state = ledger.read()
                state['csrf'] = csrf
                state['data_configured'] = bool(os.getenv('TWELVE_DATA_API_KEY'))
                self.respond(200, json.dumps(state, allow_nan=False))
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
                body = (ROOT/({'/research':'research.html','/news':'news.html','/updates':'updates.html'}.get(self.path,'dashboard.html'))).read_text().replace('__NONCE__',nonce)
                self.respond(200, body, 'text/html; charset=utf-8', {
                    'Content-Security-Policy':f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; img-src data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
            else:
                self.respond(404, '{"error":"Not found"}')

        def do_POST(self):
            if not self.authorized(): return
            if self.path not in ('/api/control', '/api/research', '/api/news', '/api/updates'):
                self.respond(404, '{"error":"Not found"}')
                return
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),csrf):
                self.respond(403, '{"error":"Refresh the page and try again"}')
                return
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= (16000 if self.path=='/api/updates' else 1024): raise ValueError()
                payload = json.loads(self.rfile.read(length))
                if self.path == '/api/research' and research:
                    research.start(payload)
                elif self.path == '/api/news' and news:
                    news.configure(payload)
                elif self.path == '/api/updates' and updates:
                    if payload.get('action')=='check':updates.check()
                    elif payload.get('action')=='install':updates.install(payload['version'])
                    elif payload.get('action')=='configure':updates.configure(payload['repo'],payload['public_key'])
                    else:raise ValueError('Unknown update control')
                elif self.path == '/api/control':
                    ledger.control(payload['action'])
                else:
                    raise ValueError('Research unavailable')
            except (ValueError, KeyError, TypeError) as exc:
                self.respond(400, json.dumps({'error':str(exc) if self.path in ('/api/news','/api/updates') else 'Invalid control'}))
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
        handler_factory(ledger,os.getenv('DASHBOARD_USER','declan'),password,thread,Research(directory),news,updates,Journal(directory)))
    print('Paper-trading dashboard ready; real-money execution is not implemented.', flush=True)
    try: server.serve_forever()
    finally:
        STOP.set()
        news.wake.set()
        server.server_close()
        lock.close()


if __name__ == '__main__': main()
