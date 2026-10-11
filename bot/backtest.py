"""Fixed-rule DAILY approximation; never submits broker orders or touches paper state."""
import bisect
from datetime import date, timedelta
import math
from engine import SYMBOLS, positive, signal

WARNINGS = [
 'Daily approximation, not an exact replay of the 15-minute bot: prior close signals execute at the next session open. The intraday 3% daily-loss rule is not modelled.',
 'The 5% price exit is assessed at the prior close and executed next open; gaps can exceed it. A 10% account drawdown blocks new buys permanently within this run.',
 'Split-adjusted prices; dividends, taxes, interest, corporate-action cash flows and partial fills are excluded.',
 'GBP conversion uses the latest FX daily close strictly before the stock date (maximum four calendar days old), not a simultaneous executable rate.',
 'AAPL, MSFT and AMZN were selected today: this is a fixed, concentrated universe with selection/survivorship bias.',
 'No model training or parameter optimisation is performed. These historical results are not independent evidence of a profitable strategy or a forecast.',
 'Transaction-cost allowances are assumptions, not verified broker charges. End-of-test holdings are liquidated with the same cost model.',
]

def dates(start, end):
    a,b=date.fromisoformat(start),date.fromisoformat(end)
    if a>=b or (b-a).days>3660: raise ValueError('Choose an increasing date range of at most ten years.')
    if b>=date.today(): raise ValueError('End date must be before today; incomplete daily bars are excluded.')
    return a,b

def parse_series(data, symbol):
    meta=data.get('meta',{})
    if meta.get('symbol')!=symbol: raise ValueError('Historical symbol mismatch: '+symbol)
    if symbol in SYMBOLS and meta.get('currency')!='USD': raise ValueError('Historical stock currency must be USD.')
    result={}
    for r in data.get('values',[]):
        d=date.fromisoformat(r['datetime'][:10]).isoformat()
        if d in result: raise ValueError('Duplicate historical date for '+symbol)
        o,c=positive(r['open']),positive(r['close'])
        result[d]={'open':o,'close':c}
    if not result: raise ValueError('No historical data returned for '+symbol)
    return result

def prepare(series,start,end,symbols=SYMBOLS):
    dates(start,end)
    stocks={s:set(series[s]) for s in symbols}
    union=set.union(*stocks.values())
    if any(stocks[s]!=union for s in symbols):
        raise ValueError('Stock histories have missing or unequal session dates; refusing a silently shortened test.')
    ordered=sorted(union)
    selected=[d for d in ordered if start<=d<=end]
    if len(selected)<20: raise ValueError('At least 20 stock sessions are required.')
    if (date.fromisoformat(selected[0])-date.fromisoformat(start)).days>4 or (date.fromisoformat(end)-date.fromisoformat(selected[-1])).days>4:
        raise ValueError('Data does not cover the requested period; check your historical-data entitlement.')
    if ordered.index(selected[0])<50: raise ValueError('Missing 50-session indicator warm-up before the start date.')
    fxdates=sorted(series['USD/GBP'])
    rates={}
    for d in selected:
        index=bisect.bisect_left(fxdates,d)-1
        if index<0 or (date.fromisoformat(d)-date.fromisoformat(fxdates[index])).days>4:
            raise ValueError('Missing prior-day FX rate for '+d)
        rates[d]=positive(series['USD/GBP'][fxdates[index]]['close'])
    for s in symbols:
        for d in ordered:
            positive(series[s][d]['open']);positive(series[s][d]['close'])
    return ordered,selected,rates

def metrics(curve,trades,start,end,initial=100.):
    peak=initial;dd=0.
    for p in curve:
        peak=max(peak,p['equity']);dd=max(dd,(peak-p['equity'])/peak)
    value=curve[-1]['equity'];days=(date.fromisoformat(end)-date.fromisoformat(start)).days
    closed=[t for t in trades if t['side']=='SELL']
    wins=[t['pnl_gbp'] for t in closed if t['pnl_gbp']>0]
    losses=[t['pnl_gbp'] for t in closed if t['pnl_gbp']<0]
    yearly={};base=initial
    for point in curve: yearly[point['date'][:4]]=point['equity']
    annual=[]
    for year,equity in sorted(yearly.items()):
        annual.append({'year':year,'return_pct':(equity/base-1)*100});base=equity
    return dict(final_gbp=value,net_profit_gbp=value-initial,return_pct=(value/initial-1)*100,
        max_drawdown_pct=dd*100,closed_trades=len(closed),
        win_rate_pct=len(wins)/len(closed)*100 if closed else None,
        profit_factor=sum(wins)/abs(sum(losses)) if losses else None,
        cagr_pct=((value/initial)**(365.25/days)-1)*100 if days>=365 else None,
        modelled_costs_gbp=sum(t['cost_gbp'] for t in trades),annual=annual)

def run(series,start,end,slippage_bps=25,fx_fee_bps=15):
    slip=float(slippage_bps)/10000;fee=float(fx_fee_bps)/10000
    if not all(math.isfinite(v) and 0<=v<=.02 for v in (slip,fee)):
        raise ValueError('Each cost allowance must be between 0 and 200 basis points.')
    ordered,selected,rates=prepare(series,start,end)
    comparisons={}
    for name,allocation in [('strategy',20),('hold_60',20),('hold_100',100/3),('cash',0)]:
        cash=100.;positions={};trades=[];curve=[];locked=False
        def buy(symbol,price,fx,day,budget):
            nonlocal cash
            unit=price*(1+slip)*fx*(1+fee)
            qty=math.floor(min(budget,cash)/unit*1e6)/1e6
            spent=qty*unit
            if qty<=0 or spent<1:return
            if spent>cash+1e-8 or spent>budget+1e-8:raise ValueError('Purchase budget exceeded')
            cash-=spent
            positions[symbol]={'qty':qty,'cost':spent,'entry':price*(1+slip)}
            trades.append(dict(date=day,symbol=symbol,side='BUY',quantity=qty,
                price_usd=price*(1+slip),fx_gbp_per_usd=fx,net_gbp=-spent,
                cost_gbp=spent-qty*price*fx,pnl_gbp=None,reason='Trend signal' if name=='strategy' else 'Benchmark allocation'))
        def sell(symbol,price,fx,day,reason):
            nonlocal cash
            p=positions.pop(symbol);gross=p['qty']*price*fx
            net=gross*(1-slip)*(1-fee);cash+=net
            trades.append(dict(date=day,symbol=symbol,side='SELL',quantity=p['qty'],
                price_usd=price*(1-slip),fx_gbp_per_usd=fx,net_gbp=net,
                cost_gbp=gross-net,pnl_gbp=net-p['cost'],reason=reason))
        for day in selected:
            fx=rates[day];idx=ordered.index(day)
            equity_open=cash+sum(p['qty']*series[s][day]['open']*fx for s,p in positions.items())
            if equity_open<=90:locked=True
            if name=='strategy':
                for s in SYMBOLS:
                    past=[series[s][d]['close'] for d in ordered[idx-50:idx]]
                    decision=signal(past,past[-1]);p=positions.get(s)
                    stopped=p is not None and past[-1]<=p['entry']*.95
                    if p and (decision=='sell' or stopped):
                        sell(s,series[s][day]['open'],fx,day,'Prior-close 5% exit' if stopped else 'Prior-close trend exit')
                    elif not p and decision=='buy' and not locked:
                        buy(s,series[s][day]['open'],fx,day,allocation)
            elif name.startswith('hold') and day==selected[0]:
                for s in SYMBOLS:buy(s,series[s][day]['open'],fx,day,allocation)
            equity=cash+sum(p['qty']*series[s][day]['close']*fx for s,p in positions.items())
            if equity<=90:locked=True
            if day==selected[-1]:
                for s in list(positions):sell(s,series[s][day]['close'],fx,day,'End-of-test liquidation')
                equity=cash
            curve.append({'date':day,'equity':equity,'cash':cash})
        comparisons[name]={'metrics':metrics(curve,trades,selected[0],selected[-1]),'curve':curve,'trades':trades}
    return dict(schema_version=1,requested_start=start,requested_end=end,start=selected[0],end=selected[-1],sessions=len(selected),
        symbols=list(SYMBOLS),initial_gbp=100,slippage_bps=slippage_bps,fx_fee_bps=fx_fee_bps,warnings=WARNINGS,comparisons=comparisons)
