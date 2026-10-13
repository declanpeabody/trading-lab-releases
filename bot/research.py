"""Background research jobs; separate files from the active paper account."""
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import threading
from ranked_backtest import dates, parse_series, run, walk_forward, run_batched
from strategy import WATCHLIST, screening_universe
from scanner import catalogue
from market import Market, MarketUnavailable
from mac_update import prune_files

class Research:
    def __init__(self, directory):
        self.directory=Path(directory)/'research';self.directory.mkdir(parents=True,exist_ok=True)
        self.lock=threading.Lock();self.busy=False;self.error=None
        self.latest=None
        path=self.directory/'latest.json'
        if path.exists():
            try:self.latest=json.loads(path.read_text())
            except (OSError,ValueError):self.error='Previous report could not be read; run a new test.'

    def state(self):
        with self.lock:return {'running':self.busy,'error':self.error,'report':self.latest}

    def start(self, options):
        if not isinstance(options,dict):raise ValueError('Invalid research options.')
        start,end=str(options.get('start','')),str(options.get('end',''))
        mode=options.get('mode','historical')
        if mode not in ('historical','walk_forward','scanner_replay'):raise ValueError('Unknown research mode')
        dates(start,end)
        slip=float(options.get('slippage_bps',25));fee=float(options.get('fx_fee_bps',15))
        if not (0<=slip<=200 and 0<=fee<=200):raise ValueError('Cost allowances must be 0–200 basis points.')
        with self.lock:
            if self.busy:raise ValueError('A historical test is already running.')
            self.busy=True;self.error=None
        threading.Thread(target=self._job,args=(start,end,slip,fee,options.get('refresh') is True,mode),daemon=True).start()

    def _job(self,start,end,slip,fee,refresh,mode="historical"):
        try:
            a,b=dates(start,end);warm=(a-timedelta(days=150)).isoformat()
            data={};provenance={};market=Market()
            mapping=catalogue(market.get('stocks',country='United States',type='Common Stock')['data'])
            if mode!='scanner_replay' and not all(s in mapping for s in WATCHLIST):raise ValueError('Watchlist exchange mappings incomplete; no test run.')
            symbols=screening_universe(mapping) if mode=='scanner_replay' else WATCHLIST
            unavailable={}
            for symbol in (*symbols,'USD/GBP'):
                token=hashlib.sha256(f'{symbol}|{mapping.get(symbol)}|{warm}|{end}|splits|rank-v1'.encode()).hexdigest()
                cache=self.directory/(token+'.cache.json')
                try:
                    if cache.exists() and not refresh:
                        bundle=json.loads(cache.read_text())
                    else:
                        extra={'exchange':mapping[symbol]} if symbol in mapping else {}
                        raw=market.get('time_series',symbol=symbol,**extra,interval='1day',start_date=warm,
                            end_date=end,outputsize=5000,adjust='splits',order='asc')
                        parse_series(raw,symbol)
                        if symbol in mapping and raw.get('meta',{}).get('exchange')!=mapping[symbol]:raise ValueError('Historical exchange mismatch')
                        bundle={'retrieved_at':datetime.now(timezone.utc).isoformat(),'raw':raw}
                        self._save(cache,bundle)
                    data[symbol]=parse_series(bundle['raw'],symbol)
                    provenance[symbol]={'retrieved_at':bundle['retrieved_at'],
                        'sha256':hashlib.sha256(json.dumps(bundle['raw'],sort_keys=True).encode()).hexdigest()}
                except MarketUnavailable:raise
                except (ValueError,KeyError,TypeError) as exc:
                    if mode!='scanner_replay' or symbol=='USD/GBP' or 'Local daily API budget' in str(exc):raise
                    data[symbol]={};unavailable[symbol]=str(exc)[:200]
            report=run_batched(data,mapping,start,end,slip,fee) if mode=='scanner_replay' else run(data,start,end,slip,fee)
            if mode=='scanner_replay':report['unavailable_histories']=unavailable
            if mode=="walk_forward":
                report["walk_forward"]=walk_forward(data,start,end,slip,fee)
                report['warnings']=[w for w in report['warnings'] if 'No optimisation' not in w]
                report['warnings'].append('The main chart is the fixed-rule full-period comparison. Purchase-size selection and reserved-test results are reported separately above; they do not alter the main curve or paper strategy.')
            report["research_mode"]=mode
            report.update(created_at=datetime.now(timezone.utc).isoformat(),data_source='Twelve Data daily bars',provenance=provenance)
            self._save(self.directory/'latest.json',report)
            self._save(self.directory/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.report.json'),report)
            with self.lock:self.latest=report
        except (ValueError,KeyError,TypeError) as exc:
            # Only our validation errors are shown; raw API bodies/URLs are never exposed.
            message=str(exc) if isinstance(exc,ValueError) else 'Historical data format is incomplete or unexpected.'
            with self.lock:self.error=message[:300]
        except Exception:
            with self.lock:self.error='Research failed; check the data connection and available disk space. Paper account unchanged.'
        finally:
            try:
                prune_files(self.directory,'*.report.json',10,50_000_000)
                prune_files(self.directory,'*.cache.json',400,200_000_000)
            except OSError:pass
            with self.lock:self.busy=False

    @staticmethod
    def _save(path,value):
        temp=path.with_suffix(path.suffix+'.tmp')
        temp.write_text(json.dumps(value,allow_nan=False));temp.chmod(0o600);temp.replace(path)

    def export(self,kind):
        with self.lock:r=self.latest
        if r is None:raise ValueError('Run a historical test first.')
        if kind=='json':return json.dumps(r,indent=2,allow_nan=False),'application/json'
        output=io.StringIO();writer=csv.writer(output)
        if kind=='equity':
            names=list(r['comparisons'])
            writer.writerow(['date']+[n+'_gbp' for n in names])
            for i,p in enumerate(r['comparisons']['strategy']['curve']):
                writer.writerow([p['date']]+[r['comparisons'][n]['curve'][i]['equity'] for n in names])
        elif kind=='trades':
            fields=['date','symbol','side','quantity','price_usd','fx_gbp_per_usd','net_gbp','cost_gbp','pnl_gbp','reason']
            writer.writerow(['portfolio']+fields)
            for name,portfolio in r['comparisons'].items():
                for t in portfolio['trades']:writer.writerow([name]+[t[k] for k in fields])
        else:raise ValueError('Unknown export format.')
        return output.getvalue(),'text/csv; charset=utf-8'
