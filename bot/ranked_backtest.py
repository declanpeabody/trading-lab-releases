"""Daily approximation of the shared ranked strategy; separate from paper state."""
from datetime import date
import math
from backtest import dates, prepare, metrics
from engine import positive, signal
from strategy import (WATCHLIST, PAPER_CAPITAL, MAX_POSITION, MAX_OPEN,
                      STOP_FRACTION, LOSS_FRACTION, rank_histories, candidates, settings)

LABELS={'strategy':'Ranked strategy','unranked':'Alphabetical baseline',
        'equal_weight':'Equal-weight hold (30 stocks)','cash':'Cash'}
WARNINGS=[
    "Fixed 30-stock historical baseline only: this does not replay the live 300-stock target universe, batch timing or partial data coverage. It cannot establish performance of the expanded scanner.",
    'Daily approximation: ranking and entry/exit signals use completed prior-session bars; fills occur at the next open. Intraday 15-minute decisions, intraday lows and the 3% intraday loss block are NOT replayed.',
    'The 30-stock watchlist is selected today. Selection/survivorship bias remains; the alphabetical baseline isolates ordering within this same sample, not performance of the old whole-market scanner.',
    'Holdings start empty with £1,000. Existing paper holdings, funding history and latched control flags are not replayed.',
    'All stock histories must cover identical sessions and provide 50 prior-session bars. Missing data is rejected, not silently dropped.',
    'Daily split-adjusted prices and volumes are used. Dividends, interest, tax, borrowing, corporate-action cash flows, provider delays and infrastructure costs are excluded. Returned volume must be compatible with the supplied adjusted prices.',
    'FX uses the latest available daily close strictly before each session. This is an approximation, not a simultaneous executable rate.',
    'Costs are configurable assumptions, not verified spreads or fill prices. All portfolios are liquidated at the final close with costs.',
    'Equal-weight hold invests across all 30 names and does not have the strategy five-position cap. Cash earns no interest.',
    'News remains observation-only and is not part of these signals. No optimisation, machine learning or proof of profitability is included. Reserve later dates for evaluation before tuning rules.',
]

def parse_series(data,symbol):
    meta=data.get('meta',{})
    if meta.get('symbol')!=symbol:raise ValueError('Historical symbol mismatch')
    if symbol!='USD/GBP' and meta.get('currency')!='USD':raise ValueError('Historical stock currency must be USD')
    out={}
    for row in data.get('values',[]):
        day=date.fromisoformat(row['datetime'][:10]).isoformat()
        if day in out:raise ValueError('Duplicate historical date')
        value={'open':positive(row['open']),'close':positive(row['close'])}
        if symbol!='USD/GBP':
            volume=float(row['volume'])
            if not math.isfinite(volume) or volume<0:raise ValueError('Invalid historical volume')
            value['volume']=volume
        out[day]=value
    if not out:raise ValueError('No historical bars returned')
    return out

def run(series,start,end,slippage_bps=25,fx_fee_bps=15,purchase_limit=MAX_POSITION, *, initial_gbp=PAPER_CAPITAL, spread_bps=0, dividends=None, benchmark=False):
    if purchase_limit not in (100,150,200,1000):raise ValueError("Research purchase size must be £100, £150, £200 or £1,000")
    initial_gbp=positive(initial_gbp)
    if initial_gbp>1000000 or purchase_limit>initial_gbp:raise ValueError("Invalid research capital")
    spread=float(spread_bps)/10000
    if not math.isfinite(spread) or not 0<=spread<=.02:raise ValueError("Spread must be 0–200 bps")
    slip,fee=float(slippage_bps)/10000,float(fx_fee_bps)/10000
    if not all(math.isfinite(x) and 0<=x<=.02 for x in (slip,fee)):raise ValueError('Cost allowances must be 0–200 bps')
    slip+=spread/2
    ordered,selected,rates=prepare(series,start,end,WATCHLIST)
    dividend_rows=validate_dividends(dividends or [],start,end,series)
    if benchmark and any(day not in series.get('SPY',{}) for day in selected):raise ValueError('SPY benchmark sessions incomplete')
    labels=LABELS|({'spy':'SPY buy-and-hold'} if benchmark else {})
    rankings={};histories={}
    for day in selected:
        i=ordered.index(day)
        bars={s:[series[s][d] for d in ordered[i-50:i]] for s in WATCHLIST}
        rankings[day]=rank_histories(bars)
        histories[day]={s:[r['close'] for r in rows] for s,rows in bars.items()}
    comparisons={}
    for name in labels:
        cash=initial_gbp;positions={};trades=[];curve=[];locked=False;receivables=[]
        def buy(s,day,budget,reason):
            nonlocal cash
            price=positive(series[s][day]['open']);fx=rates[day]
            if price<1 and name in ('strategy','unranked'):return
            unit=price*(1+slip)*fx*(1+fee)
            qty=math.floor(min(budget,cash)/unit*1e6)/1e6;spent=qty*unit
            if qty<=0 or spent<1:return
            if spent>cash+1e-8 or spent>budget+1e-8:raise ValueError('Budget exceeded')
            cash-=spent;positions[s]={'qty':qty,'cost':spent,'entry':price*(1+slip)}
            trades.append(dict(date=day,symbol=s,side='BUY',quantity=qty,price_usd=price*(1+slip),
                fx_gbp_per_usd=fx,net_gbp=-spent,cost_gbp=spent-qty*price*fx,pnl_gbp=None,reason=reason))
        def sell(s,day,price,reason):
            nonlocal cash
            p=positions.pop(s);fx=rates[day];gross=p['qty']*price*fx;net=gross*(1-slip)*(1-fee)
            cash+=net
            trades.append(dict(date=day,symbol=s,side='SELL',quantity=p['qty'],price_usd=price*(1-slip),
                fx_gbp_per_usd=fx,net_gbp=net,cost_gbp=gross-net,pnl_gbp=net-p['cost'],reason=reason))
        for day in selected:
            fx=rates[day];past=histories[day]
            for event in dividend_rows:
                if event['ex_date']==day and event['symbol'] in positions:
                    receivables.append(dict(symbol=event['symbol'],payment_date=event['payment_date'],usd=positions[event['symbol']]['qty']*event['amount_usd']))
            for payment in list(receivables):
                if payment['payment_date']<=day:
                    net=payment['usd']*fx*(1-fee);cash+=net;receivables.remove(payment)
                    trades.append(dict(date=day,symbol=payment['symbol'],side='DIVIDEND',quantity=0,price_usd=0,fx_gbp_per_usd=fx,net_gbp=net,cost_gbp=payment['usd']*fx-net,pnl_gbp=None,reason='User-supplied dividend, net amount per adjusted share'))
            accrued=sum(p['usd'] for p in receivables)*fx*(1-fee)
            opening=cash+accrued+sum(p['qty']*series[s][day]['open']*fx for s,p in positions.items())
            if opening<=initial_gbp*(1-LOSS_FRACTION):locked=True
            if name in ('strategy','unranked'):
                ranking=rankings[day]
                if name=='unranked':ranking=sorted(ranking,key=lambda r:r['symbol'])
                # Same pre-exit candidate selection as the running worker.
                selection=candidates(ranking,positions);exited=set()
                for s,p in list(positions.items()):
                    stopped=past[s][-1]<=p['entry']*(1-STOP_FRACTION)
                    if stopped or signal(past[s],past[s][-1])=='sell':
                        sell(s,day,series[s][day]['open'],'Prior-close loss exit' if stopped else 'Prior-close trend exit');exited.add(s)
                for s in selection:
                    if not locked and len(positions)<MAX_OPEN and s not in exited:
                        buy(s,day,purchase_limit,'Ranked entry' if name=='strategy' else 'Alphabetical entry')
            elif name=='equal_weight' and day==selected[0]:
                for s in WATCHLIST:buy(s,day,initial_gbp/len(WATCHLIST),'Equal-weight benchmark')
            elif name=='spy' and day==selected[0]:buy('SPY',day,initial_gbp,'SPY benchmark')
            equity=cash+accrued+sum(p['qty']*series[s][day]['close']*fx for s,p in positions.items())
            if equity<=initial_gbp*(1-LOSS_FRACTION):locked=True
            if day==selected[-1]:
                for s in list(positions):sell(s,day,series[s][day]['close'],'End-of-test liquidation')
                equity=cash+accrued
            curve.append({'date':day,'equity':equity,'cash':cash,'positions':len(positions),'dividend_receivable_gbp':accrued})
        comparisons[name]={'metrics':metrics(curve,trades,selected[0],selected[-1],initial_gbp),'curve':curve,'trades':trades}
    return dict(schema_version=2,strategy_settings=settings()|{"max_purchase":purchase_limit},labels=labels,
        requested_start=start,requested_end=end,start=selected[0],end=selected[-1],sessions=len(selected),
        symbols=list(WATCHLIST),initial_gbp=initial_gbp,slippage_bps=slippage_bps,fx_fee_bps=fx_fee_bps,
        warnings=WARNINGS,comparisons=comparisons,spread_bps=spread_bps,dividend_inputs=dividend_rows)


def walk_forward(series,start,end,slippage_bps=25,fx_fee_bps=15):
    """Expanding past-only selection, followed by a separate final 63-session test.

    Three predeclared sizes, no random split and no changes to live strategy.
    Each window starts from cash and liquidates; results are not a continuous fund.
    """
    _,days,_=prepare(series,start,end,WATCHLIST)
    train_n,test_n,hold_n=126,63,63
    if len(days)<train_n+2*test_n+hold_n:
        raise ValueError('Walk-forward testing needs at least 315 sessions plus 50 earlier warm-up sessions.')
    dev=days[:-hold_n];hold=days[-hold_n:];sizes=(100,150,200)
    def evaluate(window):
        reports={size:run(series,window[0],window[-1],slippage_bps,fx_fee_bps,purchase_limit=size) for size in sizes}
        variants={str(size):reports[size]['comparisons']['strategy'] for size in sizes}
        baseline=reports[200]['comparisons']
        return variants,{k:baseline[k] for k in ('strategy','unranked','equal_weight','cash')}
    def choose(variants):
        # Stable tie break favours the smaller purchase. No testing-period inputs.
        scores={size:variants[str(size)]['metrics']['return_pct']-.5*variants[str(size)]['metrics']['max_drawdown_pct'] for size in sizes}
        chosen=max(sizes,key=lambda size:(scores[size],-size))
        return chosen,{str(k):v for k,v in scores.items()}
    folds=[];offset=train_n
    while offset+20<=len(dev):
        stop=min(offset+test_n,len(dev))
        training=dev[:offset];testing=dev[offset:stop]
        train,_=evaluate(training);chosen,scores=choose(train)
        tested,baseline=evaluate(testing)
        folds.append(dict(train_start=training[0],train_end=training[-1],test_start=testing[0],test_end=testing[-1],
            train_sessions=len(training),test_sessions=len(testing),selected_purchase=chosen,training_scores=scores,
            training_metrics={k:v['metrics'] for k,v in train.items()},
            selected_metrics=tested[str(chosen)]['metrics'],variant_metrics={k:v['metrics'] for k,v in tested.items()},
            baseline_metrics={k:v['metrics'] for k,v in baseline.items()}))
        offset=stop
    trained,_=evaluate(dev);chosen,scores=choose(trained)
    final,baseline=evaluate(hold)
    return dict(schema_version=1,method='Expanding-window purchase-size selection',
        objective='Training return_pct minus 0.5 × max_drawdown_pct; ties choose smaller purchase',
        sizes=list(sizes),train_min_sessions=train_n,test_target_sessions=test_n,holdout_sessions=hold_n,
        folds=folds,unused_development_sessions=len(dev)-offset,
        holdout=dict(train_start=dev[0],train_end=dev[-1],start=hold[0],end=hold[-1],
            selected_purchase=chosen,training_scores=scores,
            selected_metrics=final[str(chosen)]['metrics'],
            variant_metrics={k:v['metrics'] for k,v in final.items()},
            baseline_metrics={k:v['metrics'] for k,v in baseline.items()}),
        initial_gbp=PAPER_CAPITAL,slippage_bps=slippage_bps,fx_fee_bps=fx_fee_bps,
        warnings=[
            'Research only: the running bot remains capped at £200 per purchase. No settings are automatically promoted.',
            'Each train/test/holdout window resets to £1,000 cash, empty positions and fresh risk flags, then liquidates. Fold returns cannot be concatenated into a continuous traded result.',
            'The final 63 sessions are excluded from every development fold and final selection. Once viewed they are no longer untouched for future strategy design or reruns.',
            'Three predeclared purchase sizes are tested. Training uses net return minus half the maximum drawdown, not maximum raw profit.',
            'Forward-test variants share observations. Comparisons are correlated; a winning window or a small number of trades is weak evidence.',
            'Costs, warm-up, current-watchlist bias and daily approximations are identical to the ordinary historical test. No machine learning is trained.',
        ])


def run_batched(series,mapping,start,end,slippage_bps=25,fx_fee_bps=15,normal_limit=500,priority_limit=700):
    """Run the actual scanner and paper ledger against an explicit synthetic tape.

    Daily OHLC cannot reconstruct intraday quotes. Every synthetic check uses that
    session's open and prior FX close; the report names this limitation prominently.
    No network requests, global clock patches or writes to the live account.
    """
    import tempfile
    from pathlib import Path
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from engine import Ledger
    from ranked_scanner import RankedScanner
    from strategy import screening_universe, LIVE_VERSION
    from market import session_window, ApiBudget, BudgetDeferred
    dates(start,end)
    universe=screening_universe(mapping)
    if not universe:raise ValueError('No supported stocks for scanner replay')
    if not 0<=normal_limit<=priority_limit<=700:raise ValueError('Invalid replay request limits')
    days=sorted({d for s in universe for d in series.get(s,{}) if start<=d<=end and date.fromisoformat(d).weekday()<5})
    if len(days)<20:raise ValueError('Scanner replay needs at least 20 sessions')
    fxseries=series.get('USD/GBP',{})
    class Tape:
        def __init__(self):self.current=None;self.count=0
        def now(self,zone):return self.current.astimezone(ZoneInfo(zone))
        def timestamp(self):return self.current.timestamp()
        def used(self):return self.count
        def get(self,endpoint,priority=False,purpose="trading",**params):
            if self.count>=(priority_limit if priority else normal_limit):
                raise ValueError('Local daily API budget reached in replay')
            if purpose=='history' and not priority and self.count>=normal_limit-ApiBudget.history_headroom(self.now('America/New_York')):
                raise BudgetDeferred('History preparation deferred to preserve candidate credits in replay')
            self.count+=1;self.current+=timedelta(seconds=8)
            day=self.now('America/New_York').date().isoformat();symbol=params.get('symbol')
            if endpoint=='stocks':
                return {'data':[{'symbol':s,'exchange':mapping[s],'country':'United States','currency':'USD','type':'Common Stock'} for s in mapping]}
            data=series.get(symbol,{})
            past=sorted(d for d in data if d<day)
            if endpoint=='time_series':
                return {'meta':{'symbol':symbol,'exchange':mapping[symbol],'currency':'USD'},
                    'values':[{'datetime':d,**data[d]} for d in past[-60:]]}
            if endpoint=='quote':
                if symbol=='USD/GBP':
                    if not past or (date.fromisoformat(day)-date.fromisoformat(past[-1])).days>4:raise ValueError('Prior FX history missing or stale')
                    price=data[past[-1]]['close']
                else:
                    if day not in data:raise ValueError('Synthetic quote unavailable: missing daily bar')
                    price=data[day]['open']
                return {'symbol':symbol,'exchange':mapping.get(symbol),'currency':'USD',
                    'close':price,'last_quote_at':self.timestamp(),'is_market_open':session_window(self.current),
                    'is_extended_hours':False}
            raise ValueError('Unsupported replay endpoint')
    tape=Tape();trades=[];curve=[];coverage=[];failures=[]
    slip=float(slippage_bps)/10000;fee=float(fx_fee_bps)/10000
    with tempfile.TemporaryDirectory(prefix='paper-scanner-replay-') as folder:
        ledger=Ledger(Path(folder)/'paper.sqlite3',price_allowance=slip,fx_allowance=fee)
        try:
            ledger.control('start');scanner=RankedScanner(folder,tape,clock=tape,budget=tape)
            for day in days:
                tape.current=datetime.fromisoformat(day+'T09:30:00').replace(tzinfo=ZoneInfo('America/New_York'));tape.count=0
                scans=0;ready=0;blocked=0
                while session_window(tape.current):
                    before=ledger.read();scanner.audit={'bars':{},'prices':{},'decisions':[],'before':before}
                    try:scanner._cycle(ledger)
                    except (ValueError,KeyError,TypeError) as exc:
                        ledger.fail(str(exc));blocked+=1
                        if len(failures)<100:failures.append({'date':day,'scan':scans+1,'reason':str(exc)})
                    after=ledger.read();prices=scanner.audit['prices'];fx=after['fx']
                    for action in scanner.audit['decisions']:
                        symbol=action['symbol'];side=action['action']
                        if side=='NOT_EVALUATED':blocked+=1
                        if side not in ('BUY','SELL'):continue
                        price=prices[symbol]
                        p=(after if side=='BUY' else before)['positions'][symbol];qty=p['qty']
                        gross=qty*price*fx
                        net=-p['cost_gbp'] if side=='BUY' else gross*(1-slip)*(1-fee)
                        trades.append(dict(date=day,symbol=symbol,side=side,quantity=qty,
                            price_usd=price*(1+slip if side=='BUY' else 1-slip),fx_gbp_per_usd=fx,net_gbp=net,
                            cost_gbp=(-net-gross if side=='BUY' else gross-net),
                            pnl_gbp=None if side=='BUY' else net-p['cost_gbp'],reason=action['reason']))
                    ready=max(ready,scanner.status.get('ranked_histories',0));scans+=1
                    tape.current+=timedelta(seconds=900)
                state=ledger.read();prior=sorted(d for d in fxseries if d<day)
                if not prior or (date.fromisoformat(day)-date.fromisoformat(prior[-1])).days>4:
                    raise ValueError('Missing/stale prior-session FX for portfolio valuation')
                fx=positive(fxseries[prior[-1]]['close']);equity=state['cash'];missing=[]
                for symbol,p in state['positions'].items():
                    if day not in series.get(symbol,{}):missing.append(symbol)
                    price=series.get(symbol,{}).get(day,{}).get('close',state['marks'].get(symbol))
                    if price is None:raise ValueError('Held-stock valuation unavailable')
                    equity+=p['qty']*price*fx
                    if day==days[-1]:
                        gross=p['qty']*price*fx;net=gross*(1-slip)*(1-fee);equity-=gross-net
                        trades.append(dict(date=day,symbol=symbol,side='SELL',quantity=p['qty'],price_usd=price*(1-slip),
                            fx_gbp_per_usd=fx,net_gbp=net,cost_gbp=gross-net,pnl_gbp=net-p['cost_gbp'],reason='End-of-test liquidation'))
                curve.append({'date':day,'equity':equity,'cash':equity if day==days[-1] else state['cash'],
                    'positions':0 if day==days[-1] else len(state['positions'])})
                coverage.append({'date':day,'checks':scans,'api_attempts':tape.count,'ranked_max':ready,
                    'prepared':scanner.status.get('histories_ready',0),'blocked_checks_or_positions':blocked,'stale_valuation_symbols':missing})
        finally:ledger.db.close()
    cashcurve=[{'date':d,'equity':PAPER_CAPITAL,'cash':PAPER_CAPITAL,'positions':0} for d in days]
    return dict(schema_version=3,research_mode='scanner_replay',labels={'strategy':'Batched scanner · synthetic open quotes','cash':'Cash'},
        strategy_settings=settings(),strategy_version=LIVE_VERSION,requested_start=start,requested_end=end,
        start=days[0],end=days[-1],sessions=len(days),symbols=universe,initial_gbp=PAPER_CAPITAL,
        slippage_bps=slippage_bps,fx_fee_bps=fx_fee_bps,coverage=coverage,replay_errors=failures,
        replay_assumptions={'quote':'same session open at every synthetic check','fx':'prior daily close',
            'request_seconds':8,'wait_seconds':900,'normal_limit':normal_limit,'priority_limit':priority_limit,'initial_cursor':0,'candidate_reserve_per_remaining_slot':7},
        comparisons={'strategy':{'metrics':metrics(curve,trades,days[0],days[-1],PAPER_CAPITAL),'curve':curve,'trades':trades},
            'cash':{'metrics':metrics(cashcurve,[],days[0],days[-1],PAPER_CAPITAL),'curve':cashcurve,'trades':[]}},
        warnings=[
            'Synthetic daily-price replay, not an intraday backtest or evidence of live profitability. Every check uses the SAME session open; real price paths, spreads and quote delays are unknown.',
            'Runs the current scanner and paper ledger: stable up-to-300 universe, prior-session ranking, partial coverage, persistent batch cursor, 20-history batches, eight seconds per request, 15-minute waits and daily request budgets. Histories prepared after decisions enter the next check only.',
            'Universe uses the catalogue selected today: survivorship and selection bias remain. Missing stock histories are retained as unavailable candidates, not removed from the universe.',
            'Starts at £1,000 with empty holdings and cache. Research downloads consume real shared credits; simulated requests do not. Other apps and research usage are not included in the simulated daily budget.',
            'Fresh synthetic quotes are assumed when a daily bar exists. Provider outages, real request durations, holidays without bars and quota changes cannot be reconstructed. Coverage and blocked checks are included in the JSON export.',
            'Uses split-adjusted bars consistently, excludes dividends/tax/corporate-action cash flows, and holds any corporate-action verification flags unresolved. Live account reconciliation is not simulated.',
            'Daily closing valuation uses the prior FX close; missing held bars retain the previous mark and are identified in coverage. Last session liquidates with modelled costs.',
            'This replay uses the fixed £200 purchase setting. Prospective experiment accounts and automatic promotions are not replayed; this report cannot qualify a promotion.',
        ])


def validate_dividends(rows,start,end,series=None):
    from urllib.parse import urlsplit
    if not isinstance(rows,list) or len(rows)>100:raise ValueError('Provide at most 100 dividend records')
    out=[];seen=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Invalid dividend record')
        symbol=row.get('symbol');ex=date.fromisoformat(row.get('ex_date',''));pay=date.fromisoformat(row.get('payment_date',''))
        amount=float(row.get('amount_usd',-1));source=str(row.get('source',''));url=urlsplit(source)
        if symbol not in (*WATCHLIST,'SPY') or not start<=ex.isoformat()<=end or pay<ex or (pay-ex).days>366:raise ValueError('Invalid dividend symbol or dates')
        if not math.isfinite(amount) or not 0<amount<=1000 or url.scheme!='https' or not url.hostname or url.username or url.password or len(source)>500:raise ValueError('Dividend requires a positive net USD amount and HTTPS source')
        if (symbol,ex) in seen:raise ValueError('Duplicate dividend event')
        if series is not None and ex.isoformat() not in series.get(symbol,{}):raise ValueError('Dividend ex-date has no price bar')
        seen.add((symbol,ex));out.append(dict(symbol=symbol,ex_date=ex.isoformat(),payment_date=pay.isoformat(),amount_usd=amount,source=source))
    return out


def stress_test(series,start,end,slippage_bps=25,fx_fee_bps=15,initial_gbp=10000,purchase_limit=1000,spread_bps=10,dividends=None):
    """Fixed cost sensitivity tests, not parameter optimisation or promotion."""
    report=run(series,start,end,slippage_bps,fx_fee_bps,purchase_limit,initial_gbp=initial_gbp,
        spread_bps=spread_bps,dividends=dividends,benchmark=True)
    base=report['comparisons']['strategy']['metrics'];cases=[]
    for extra in (0,25,75):
        slip=min(200,float(slippage_bps)+extra)
        tested=report if extra==0 else run(series,start,end,slip,fx_fee_bps,purchase_limit,initial_gbp=initial_gbp,
            spread_bps=spread_bps,dividends=dividends,benchmark=True)
        m=tested['comparisons']['strategy']['metrics']
        cases.append(dict(slippage_bps=slip,spread_bps=spread_bps,return_pct=m['return_pct'],max_drawdown_pct=m['max_drawdown_pct'],
            costs_gbp=m['modelled_costs_gbp'],closed_trades=m['closed_trades']))
    gaps=[]
    for symbol in WATCHLIST:
        days=sorted(series[symbol])
        for before,day in zip(days,days[1:]):
            if start<=day<=end:gaps.append(dict(symbol=symbol,date=day,gap_pct=100*(series[symbol][day]['open']/series[symbol][before]['close']-1)))
    report['stress']=dict(cases=cases,largest_gaps=sorted(gaps,key=lambda x:abs(x['gap_pct']),reverse=True)[:10],
        strategy_minus_spy_pct=base['return_pct']-report['comparisons']['spy']['metrics']['return_pct'],
        dividend_coverage='User-supplied events only; completeness and split-adjusted share basis unverified' if dividends else 'No dividend events supplied: price-return comparison only',
        method='Three predeclared cost scenarios; same data, five-position strategy, next-open fills. Not an unused holdout.')
    report['warnings']=[
        'Observation only. Fixed current 30-stock sample has survivorship/selection bias and is not a replay of the 300-stock scanner.',
        f'Capital £{initial_gbp:,.0f}, purchase cap £{purchase_limit:,.0f}, five positions. SPY and equal-weight benchmarks invest full capital and have different exposures.',
        'Signals use prior completed sessions; orders fill at the next open, including observed overnight gaps. Intraday stops, order-book liquidity and partial fills are not reconstructed.',
        'Full bid/ask spread is an assumption: half is charged each way in addition to adverse slippage and FX conversion costs. These are not verified executable quotes.',
        'Dividends require net USD per split-adjusted share, ex-date, payment date and source. Entitlement uses holdings before ex-date trades; cash arrives on/after payment date. Outstanding receivables remain in final equity. No dividend completeness guarantee, interest, tax calculation or corporate-action reconciliation.',
        'Provider split-adjusted historical data can be revised. Company fundamentals collected today are never injected into these historical signals.',
        'Scenarios are descriptive, correlated and not statistically independent. Results do not select or deploy a new strategy.',
    ]
    return report
