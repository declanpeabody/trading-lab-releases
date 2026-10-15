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
        try:self.feeds=FeedResearch(self.directory)
        except Exception:self.feeds=None

    def configure(self, payload):
        with self.lock:
            if payload.get('action') in ('feeds_enable','feeds_disable'):
                if self.feeds is None:raise ValueError('Feed storage unavailable; existing files preserved')
                self.config['feeds']=payload['action']=='feeds_enable'
            elif payload.get('action') == 'refresh':
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
                self.config.update(enabled=True,contact=contact,documents=documents)
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
                'documents':self.config.get('documents',False),'feeds_enabled':self.config.get('feeds',False),
                'feeds':self.feeds.state() if self.feeds else {'error':'Feed storage unavailable; existing files preserved','observations':[],'sources':[]},
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
            try:
                if self.feeds and self.config.get('feeds',False):
                    self.feeds.poll(ledger.read(),lambda:self.config.get('feeds',False) and not stop.is_set())
            except Exception:
                with self.lock:self.info['error']='Feed observer error; trading unaffected.'
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

# Fixed public feed endpoints only: no arbitrary URL fetching or paid API calls.
FEED_SOURCES = {
    'bbc': ('BBC Business', 'news', 'https://feeds.bbci.co.uk/news/business/rss.xml'),
    'stockaholics': ('Stockaholics', 'forum', 'https://stockaholics.net/forums/-/index.rss'),
}
COMPANY_ALIASES = {'AAPL':'Apple','MSFT':'Microsoft','AMZN':'Amazon','GOOGL':'Alphabet',
    'META':'Meta','NVDA':'Nvidia','TSLA':'Tesla','ABBV':'AbbVie','ORCL':'Oracle',
    'WMT':'Walmart','NFLX':'Netflix','INTC':'Intel','AMD':'Advanced Micro Devices'}
CATALYST_TERMS = {'Earnings / guidance':r'\b(earnings|guidance|profit warning|results)\b',
    'Deal / contract':r'\b(acquisition|merger|contract|partnership|takeover)\b',
    'Regulatory / legal':r'\b(FDA|approval|regulator|lawsuit|investigation)\b',
    'Financing / dilution':r'\b(offering|financing|dilution|bankruptcy)\b',
    'Product / operations':r'\b(launch|recall|outage|production)\b'}


def feed_link(url):
    from urllib.parse import urlsplit, urlunsplit
    try:
        p=urlsplit(url)
        hosts=('bbc.com','bbc.co.uk','stockaholics.net')
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443):return None
        if not any(p.hostname==h or (p.hostname or '').endswith('.'+h) for h in hosts):return None
        return urlunsplit((p.scheme,p.netloc,p.path,p.query,''))
    except ValueError:return None


def feed_items(body, source, symbols, now):
    """Headlines only. Feed content is data, never executable instructions."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    from html import unescape
    if len(body)>1_000_000 or b'\x00' in body or b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise ValueError('Feed size or XML declaration rejected')
    root=ET.fromstring(body)
    if root.tag not in ('rss','{http://www.w3.org/2005/Atom}feed'):raise ValueError('Expected RSS or Atom')
    rows=[];name,kind,_=FEED_SOURCES[source]
    for item in list(root.findall('./channel/item'))[:100]+list(root.findall('{http://www.w3.org/2005/Atom}entry'))[:100]:
        fields={child.tag.split('}')[-1]:child for child in item}
        def value(key):return ''.join(fields[key].itertext()).strip() if key in fields else ''
        title=' '.join(unescape(re.sub('<[^>]+>',' ',value('title'))).split())[:400]
        url=feed_link(value('link') or (fields['link'].get('href','') if 'link' in fields else ''))
        if not title or not url:continue
        date=value('pubDate') or value('published') or value('updated')
        try:
            try:published=parsedate_to_datetime(date)
            except (ValueError,TypeError):published=datetime.fromisoformat(date.replace('Z','+00:00'))
            if published.tzinfo is None:continue
            published=published.astimezone(timezone.utc)
            if not now-timedelta(days=7)<=published<=now+timedelta(minutes=5):continue
        except (ValueError,TypeError,OverflowError):continue
        matched=[]
        for symbol in sorted(symbols):
            explicit=re.search(r'(?:\$|\b(?:NASDAQ|NYSE)\s*:\s*)'+re.escape(symbol)+r'(?![A-Z0-9.])',title,re.I)
            named=COMPANY_ALIASES.get(symbol)
            # Bare tickers are ambiguous: require $/exchange notation, a thread-title
            # prefix, or an explicit company alias. Matching is not identity proof.
            prefix=kind=='forum' and re.match(re.escape(symbol)+r'\s*[-–:]\s',title)
            if explicit or prefix or (named and re.search(r'\b'+re.escape(named)+r'\b',title,re.I)):matched.append(symbol)
        if len(matched)>5 or re.search(r'guaranteed|1000%|join.{0,20}(telegram|discord)|risk.free',title,re.I):continue
        categories=[label for label,pattern in CATALYST_TERMS.items() if re.search(pattern,title,re.I)]
        for symbol in matched:
            key=hashlib.sha256((symbol+':'+re.sub(r'\W+','',title.lower())).encode()).hexdigest()
            rows.append(dict(id=key,symbol=symbol,title=title,source=name,kind=kind,source_url=url,
                author=(value('creator') or value('author'))[:100] or None,
                published_at=published.isoformat(),first_seen=now.isoformat(),
                categories=categories or ['Discussion / general report'],
                confidence='Low: unverified forum discussion' if kind=='forum' else 'Headline report: claim not independently verified',
                timing='Upcoming-event wording; date not verified' if re.search(r'\b(upcoming|next week|tomorrow|scheduled|will report)\b',title,re.I) else 'Event timing unverified',
                baseline=None,outcomes={},analyser_version='feed-observer-v1'))
    return rows


class FeedResearch:
    """Independent observation store; never calls Market or writes the paper ledger."""
    def __init__(self,directory):
        self.lock=threading.RLock()
        self.db=sqlite3.connect(Path(directory)/'feed-observations.sqlite3',check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.commit()

    def fetch(self,source):
        req=Request(FEED_SOURCES[source][2],headers={'User-Agent':'DeclanTradingLab/1.0 RSS reader','Accept':'application/rss+xml, application/atom+xml, text/xml'})
        try:
            with build_opener(NoRedirect()).open(req,timeout=15) as response:body=response.read(1_000_001)
            if len(body)>1_000_000:raise ValueError('Feed too large')
            return body
        except HTTPError as exc:
            code=exc.code;exc.close()
            raise ValueError(f'HTTP {code}: source unavailable or access restricted; no bypass attempted') from None
        except OSError:raise ValueError('Feed connection unavailable') from None

    def poll(self,account,enabled=lambda:True):
        now=datetime.now(timezone.utc)
        scanner=account.get('scanner',{})
        symbols=set(scanner.get('universe_symbols',[]))|set(account.get('positions',{}))|set(scanner.get('selected',[]))|set(COMPANY_ALIASES)
        symbols={s for s in symbols if isinstance(s,str) and re.fullmatch(r'[A-Z][A-Z0-9.]{0,9}',s)}
        for source in FEED_SOURCES:
            if not enabled():break
            with self.lock:
                row=self.db.execute('SELECT body FROM sources WHERE id=?',(source,)).fetchone()
                info=json.loads(row[0]) if row else {}
                if info.get('last_attempt') and (now-datetime.fromisoformat(info['last_attempt'])).total_seconds()<1800:continue
                info.update(last_attempt=now.isoformat(),error=None)
                with self.db:self.db.execute('INSERT OR REPLACE INTO sources VALUES (?,?)',(source,json.dumps(info)))
            try:
                rows=feed_items(self.fetch(source),source,symbols,now)
                if not enabled():break
                with self.lock,self.db:
                    for row in rows:self.db.execute('INSERT OR IGNORE INTO observations VALUES (?,?)',(row['id'],json.dumps(row)))
                info.update(last_success=now.isoformat(),matched_items=len(rows))
            except Exception as exc:
                info['error']=str(exc) if isinstance(exc,ValueError) else 'Feed could not be parsed; observations retained'
            with self.lock,self.db:
                self.db.execute('INSERT OR REPLACE INTO sources VALUES (?,?)',(source,json.dumps(info)))
        with self.lock,self.db:
            self.db.execute("DELETE FROM observations WHERE json_extract(body,'$.first_seen')<?",((now-timedelta(days=30)).isoformat(),))
            self.db.execute('DELETE FROM observations WHERE rowid NOT IN (SELECT rowid FROM observations ORDER BY rowid DESC LIMIT 5000)')
            # Opportunistic outcomes use existing fresh account marks only. Missing
            # coverage remains pending; no extra quotes or invented returns.
            try:stamp=datetime.fromisoformat(account.get('last_success','').replace('Z','+00:00'))
            except (ValueError,AttributeError,TypeError):return
            if stamp.tzinfo is None or account.get('error') or not 0<=(now-stamp).total_seconds()<=120:return
            import math
            for ident,body in self.db.execute('SELECT id,body FROM observations').fetchall():
                event=json.loads(body);price=account.get('marks',{}).get(event['symbol'])
                if type(price) not in (int,float) or not math.isfinite(price) or price<=0:continue
                seen=datetime.fromisoformat(event['first_seen'])
                if stamp<seen:continue
                if event['baseline'] is None:event['baseline']={'at':stamp.isoformat(),'price_usd':price}
                else:
                    base=event['baseline'];start=datetime.fromisoformat(base['at'])
                    for label,seconds in [('1h',3600),('1d',86400)]:
                        if label not in event['outcomes'] and (stamp-start).total_seconds()>=seconds:
                            event['outcomes'][label]={'at':stamp.isoformat(),'price_usd':price,'change_pct':100*(price/base['price_usd']-1)}
                self.db.execute('UPDATE observations SET body=? WHERE id=?',(json.dumps(event),ident))

    def state(self):
        with self.lock:
            rows=[json.loads(r[0]) for r in self.db.execute('SELECT body FROM observations ORDER BY rowid DESC LIMIT 5000')]
            statuses={k:json.loads(v) for k,v in self.db.execute('SELECT id,body FROM sources')}
        now=datetime.now(timezone.utc);counts={}
        for e in rows:
            if e['kind']!='forum' or datetime.fromisoformat(e['published_at'])<now-timedelta(days=1):continue
            c=counts.setdefault(e['symbol'],{'posts':0,'authors':set()});c['posts']+=1
            if e['author']:c['authors'].add(e['author'])
        discussions=[{'symbol':s,'posts':c['posts'],'authors':len(c['authors']),
            'label':'Multiple-author discussion; independence unverified' if c['posts']>=3 and len(c['authors'])>=3 else 'Limited discussion evidence'} for s,c in counts.items()]
        return {'sources':[{'id':k,'name':v[0],'kind':v[1],**statuses.get(k,{})} for k,v in FEED_SOURCES.items()],
            'observations':rows[:100],'discussion':discussions,
            'note':'Partial headline/feed coverage, not all articles or forum comments. Counts are unique matched headlines in received feeds, not market-wide mention spikes. Outcomes use the first fresh saved USD mark after discovery and later available marks at least 1h/1d afterwards; they may be delayed, are unadjusted for corporate actions/costs, and do not establish predictive value.'}
