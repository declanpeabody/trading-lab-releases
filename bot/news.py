"""Read-only SEC metadata and opt-in document evidence. No active trading writes."""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

ITEMS = {
    '1.01': 'Material agreement', '1.02': 'Termination of material agreement',
    '1.03': 'Bankruptcy or receivership', '1.05': 'Material cybersecurity incident',
    '2.01': 'Acquisition or disposal', '2.02': 'Results / financial condition',
    '2.03': 'New financial obligation', '2.04': 'Trigger affecting financial obligations',
    '2.05': 'Exit / disposal costs', '2.06': 'Material impairment',
    '3.01': 'Listing compliance notice', '3.02': 'Unregistered equity sale',
    '4.01': 'Auditor change', '4.02': 'Non-reliance on financial statements',
    '5.02': 'Director / officer / compensation change',
    '7.01': 'Regulation FD disclosure', '8.01': 'Other event',
}
RISK_ITEMS = {'1.03', '1.05', '2.04', '2.06', '3.01', '4.02'}
FORMS = {'10-K': 'Annual report', '10-Q': 'Quarterly report',
         '20-F': 'Foreign issuer annual report', '6-K': 'Foreign issuer report',
         'S-1': 'Registration statement', 'S-3': 'Shelf registration',
         '424B5': 'Prospectus supplement'}


def analyse(form, items):
    base = form.removesuffix('/A')
    codes = re.findall(r'\b\d\.\d{2}\b', items)
    labels = [ITEMS[c] for c in codes if c in ITEMS] if base == '8-K' else []
    if not labels: labels = [FORMS.get(base, 'Current report / unspecified event')]
    flag = 'Risk review' if base == '8-K' and RISK_ITEMS.intersection(codes) else 'Information review'
    return {'categories': labels, 'flag': flag, 'direction': 'Unknown',
            'basis': f'Form {form}; SEC item codes: {", ".join(codes) or "not supplied"}.',
            'observation': 'Would flag for review only; no buy, sell or block instruction.',
            'limitation': 'Metadata classification only. Document contents, surprise, magnitude and price reaction were not analysed.',
            'analyser_version': 'sec-items-v1'}


def events_from(payload, symbol, cik, seen):
    if int(payload.get('cik', -1)) != int(cik): raise ValueError('SEC company identity mismatch')
    r = payload.get('filings', {}).get('recent', {})
    rows = []
    fields = ('accessionNumber', 'form', 'filingDate', 'primaryDocument')
    count = len(r.get('accessionNumber', []))
    if any(len(r.get(k, [])) != count for k in fields): raise ValueError('Incomplete SEC filing metadata')
    cutoff = (seen - timedelta(days=7)).date()
    for i in range(count):
        form, accession, doc = (r[k][i] for k in ('form','accessionNumber','primaryDocument'))
        base = form.removesuffix('/A')
        if base != '8-K' and base not in FORMS: continue
        date = datetime.strptime(r['filingDate'][i], '%Y-%m-%d').date()
        if not cutoff <= date <= seen.date(): continue
        if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession): raise ValueError('Invalid filing accession')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:htm|html|txt)', doc):
            continue
        items = r.get('items', [])
        accepted = r.get('acceptanceDateTime', [])
        source = {'form': form, 'items': items[i] if i < len(items) else '',
                  'filing_date': date.isoformat(), 'accepted_at': accepted[i] if i < len(accepted) else None,
                  'accession': accession, 'document': doc, 'cik': int(cik)}
        rows.append(source | {'symbol': symbol, 'company': payload.get('name',symbol),
            'source': 'SEC EDGAR', 'first_seen': seen.isoformat(),
            'source_url': f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}/{doc}',
            'metadata_sha256': hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest(),
            **analyse(form, source['items'])})
    return rows


def document_evidence(body, received_at):
    """Deterministic text extraction, not sentiment, advice or instruction execution."""
    from html.parser import HTMLParser
    class Text(HTMLParser):
        def __init__(self):super().__init__();self.parts=[];self.hidden=0
        def handle_starttag(self,tag,attrs):
            if tag in ('script','style'):self.hidden+=1
            if tag in ('p','div','br','tr'):self.parts.append('\n')
        def handle_endtag(self,tag):
            if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
        def handle_data(self,value):
            if not self.hidden:self.parts.append(value)
    parser=Text();parser.feed(body)
    text=' '.join(' '.join(parser.parts).split())
    phrases=('bankruptcy','cybersecurity','material impairment','going concern','revenue','guidance','net income','agreement')
    excerpts=[]
    for phrase in phrases:
        found=re.search(re.escape(phrase),text,re.I)
        if found:excerpts.append({'term':phrase,'excerpt':text[max(0,found.start()-100):found.end()+220]})
    return {'received_at':received_at,'sha256':hashlib.sha256(body.encode()).hexdigest(),
            'characters':len(text),'evidence':excerpts[:6],
            'summary':'Document retrieved; '+(', '.join(e['term'] for e in excerpts) or 'no configured terms found')+'.',
            'limitation':'Keyword excerpts can include boilerplate or historical references. No earnings surprise, sentiment or price prediction is inferred.'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class SecClient:
    lock = threading.Lock()
    last = 0.0

    def __init__(self, contact): self.contact = contact

    def get(self, url):
        if not (url == 'https://www.sec.gov/files/company_tickers.json' or
                re.fullmatch(r'https://data.sec.gov/submissions/CIK\d{10}\.json', url)):
            raise ValueError('SEC endpoint not permitted')
        with self.lock:
            time.sleep(max(0, 1.1 - (time.monotonic()-SecClient.last)))
            SecClient.last = time.monotonic()
        req = Request(url, headers={'User-Agent': 'DeclanTradingLab/1.0 ' + self.contact,
                                    'Accept': 'application/json'})
        try:
            with build_opener(NoRedirect()).open(req, timeout=20) as response:
                data = response.read(10_000_001)
            if len(data)>10_000_000: raise ValueError('SEC response exceeds size limit')
            return json.loads(data)
        except HTTPError as exc:
            raise ValueError(f'SEC HTTP {exc.code}; check contact settings or retry later. No access bypass attempted.') from None
        except (OSError, json.JSONDecodeError):
            raise ValueError('SEC connection or JSON response failed; paper trading is unaffected.') from None

    def document(self,url):
        if not re.fullmatch(r'https://www\.sec\.gov/Archives/edgar/data/\d+/\d+/[A-Za-z0-9_.-]+\.(?:htm|html|txt)',url):
            raise ValueError('SEC document endpoint not permitted')
        with self.lock:
            time.sleep(max(0,1.1-(time.monotonic()-SecClient.last)));SecClient.last=time.monotonic()
        req=Request(url,headers={'User-Agent':'DeclanTradingLab/1.0 '+self.contact,'Accept':'text/html,text/plain'})
        try:
            with build_opener(NoRedirect()).open(req,timeout=20) as response:body=response.read(3_000_001)
            if len(body)>3_000_000:raise ValueError('Document exceeds 3 MB limit')
            return body.decode('utf-8',errors='replace')
        except OSError:raise ValueError('SEC document unavailable; no access bypass attempted') from None


class News:
    def __init__(self, directory, client_factory=SecClient):
        self.directory = Path(directory)/'news'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config_path = self.directory/'config.json'
        self.config = {'enabled': False, 'contact': '', 'documents':False}
        if self.config_path.exists(): self.config.update(json.loads(self.config_path.read_text()))
        self.db = sqlite3.connect(self.directory/'events.sqlite3', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        row = self.db.execute('SELECT body FROM meta WHERE id=1').fetchone()
        self.info = json.loads(row[0]) if row else {'last_attempt':None,'last_success':None,'error':None,'coverage':[]}
        self.lock = threading.RLock()
        self.run_lock = threading.Lock()
        self.wake = threading.Event()
        self.busy = False
        self.mapping = {}
        self.map_time = 0
        self.client_factory = client_factory

    def configure(self, payload):
        with self.lock:
            if payload.get('action') == 'refresh':
                if not self.config['enabled']: raise ValueError('Enable news first')
                # Refresh cannot defeat the automatic polling rate.
                if self.info['last_attempt'] and (datetime.now(timezone.utc)-datetime.fromisoformat(self.info['last_attempt'])).total_seconds()<300:
                    raise ValueError('Please wait five minutes between refresh attempts')
            elif payload.get('action') == 'disable': self.config['enabled'] = False
            elif payload.get('action') == 'enable':
                contact = str(payload.get('contact','')).strip()
                if len(contact)>200 or not re.fullmatch(r'[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',contact):
                    raise ValueError('Enter a valid contact email; it will be sent to the SEC in request headers')
                documents=payload.get('documents',False)
                if type(documents) is not bool:raise ValueError('Document option must be true or false')
                self.config = {'enabled':True,'contact':contact,'documents':documents}
            else: raise ValueError('Unknown news control')
            tmp = self.config_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.config))
            tmp.chmod(0o600)
            os.replace(tmp,self.config_path)
        self.wake.set()

    def state(self, all_events=False):
        with self.lock:
            rows = self.db.execute("SELECT body FROM events ORDER BY json_extract(body,'$.first_seen') DESC, json_extract(body,'$.filing_date') DESC, rowid DESC" + ('' if all_events else ' LIMIT 100')).fetchall()
            return self.info | {'enabled':self.config['enabled'],'busy':self.busy,
                'documents':self.config.get('documents',False),
                'mode':'Observation only · SEC filings; optional document evidence',
                'events':[json.loads(row[0]) for row in rows]}

    def poll(self, account):
        if not self.run_lock.acquire(blocking=False): return
        try:
            with self.lock:
                if not self.config['enabled']: return
                if self.info['last_attempt'] and (datetime.now(timezone.utc)-datetime.fromisoformat(self.info['last_attempt'])).total_seconds()<300: return
                client = self.client_factory(self.config['contact'])
                self.busy = True
                self.info.update(last_attempt=datetime.now(timezone.utc).isoformat(),error=None,coverage=[])
            selected = list(dict.fromkeys(list(account.get('positions',{})) +
                account.get('scanner',{}).get('selected',[])))[:12]
            if not selected: selected = ['AAPL','MSFT','AMZN']
            coverage=[];document_budget=2
            try:
                if time.time()-self.map_time>86400:
                    raw = client.get('https://www.sec.gov/files/company_tickers.json')
                    mapping={}; ambiguous=set()
                    for entry in raw.values():
                        symbol,cik = entry['ticker'],int(entry['cik_str'])
                        if not 0<cik<10**10: continue
                        if symbol in mapping and mapping[symbol]!=cik: ambiguous.add(symbol)
                        mapping[symbol]=cik
                    self.mapping = {s:c for s,c in mapping.items() if s not in ambiguous}
                    self.map_time=time.time()
                for symbol in selected:
                    with self.lock:
                        if not self.config['enabled']: break
                    cik=self.mapping.get(symbol)
                    if cik is None:
                        coverage.append({'symbol':symbol,'status':'No unambiguous SEC ticker mapping; skipped'})
                        continue
                    payload=client.get(f'https://data.sec.gov/submissions/CIK{cik:010}.json')
                    seen=datetime.now(timezone.utc)
                    events=events_from(payload,symbol,cik,seen)
                    for event in events:
                        with self.lock:
                            old=self.db.execute('SELECT body FROM events WHERE id=?',(f'{symbol}:{event["accession"]}',)).fetchone()
                            enabled=self.config['enabled'] and self.config.get('documents',False)
                        if old:event=json.loads(old[0])
                        if enabled and document_budget and not event.get('document_status'):
                            document_budget-=1
                            try:
                                content=client.document(event['source_url'])
                                event['document']=document_evidence(content,datetime.now(timezone.utc).isoformat())
                                event['document_status']='retrieved'
                            except (ValueError,OSError):event['document_status']='unavailable; metadata retained'
                            if old:
                                with self.lock,self.db:self.db.execute('UPDATE events SET body=? WHERE id=?',(json.dumps(event,allow_nan=False),f'{symbol}:{event["accession"]}'))
                        if not old:
                            # Update the list entry used by the insertion below.
                            for item in events:
                                if item['accession']==event['accession']:item.update(event)
                    with self.lock, self.db:
                        for event in events:
                            event['context']={'role':'held' if symbol in account.get('positions',{}) else 'candidate',
                                'price_signal_at_poll_start':account.get('signals',{}).get(symbol),
                                'price_snapshot_at':account.get('last_success'),
                                'baseline_rule_observation':'Flag only; original trading rules unchanged'}
                            # Preserve the first received evidence and classification forever.
                            self.db.execute('INSERT OR IGNORE INTO events VALUES (?,?)',
                                (f'{symbol}:{event["accession"]}',json.dumps(event,allow_nan=False)))
                    coverage.append({'symbol':symbol,'checked_at':seen.isoformat(),'status':f'Checked; {len(events)} relevant filings in 7-day window'})
                with self.lock:
                    if self.config['enabled']:
                        self.info['last_success']=datetime.now(timezone.utc).isoformat()
            except Exception as exc:
                with self.lock:
                    self.info['error']=str(exc) if isinstance(exc,ValueError) else 'SEC response could not be processed; paper trading unaffected.'
            finally:
                with self.lock, self.db:
                    self.info['coverage']=coverage
                    self.info['requested_symbols']=selected
                    self.db.execute('DELETE FROM events WHERE rowid NOT IN (SELECT rowid FROM events ORDER BY rowid DESC LIMIT 10000)')
                    self.db.execute('INSERT OR REPLACE INTO meta VALUES (1,?)',(json.dumps(self.info),))
        finally:
            self.busy=False
            self.run_lock.release()

    def worker(self, ledger, stop):
        while not stop.is_set():
            try: self.poll(ledger.read())
            except Exception:
                with self.lock: self.info['error']='News observer error; paper trading unaffected.'
            self.wake.wait(1800)
            self.wake.clear()


class UnavailableNews:
    """Visible inert fallback; never repairs or overwrites damaged evidence."""
    def __init__(self):self.wake=threading.Event()
    def state(self,all_events=False):
        return dict(enabled=False,busy=False,documents=False,events=[],coverage=[],last_attempt=None,last_success=None,
            error='News storage could not be loaded. News is disabled; trading and dashboard remain available. Restore or repair news configuration/storage locally; existing files were preserved.')
    def configure(self,payload):raise ValueError(self.state()['error'])
    def worker(self,ledger,stop):return


def optional_news(directory):
    try:return News(directory)
    except Exception:return UnavailableNews()
