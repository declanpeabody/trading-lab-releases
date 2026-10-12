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
    if symbol in WATCHLIST and meta.get('currency')!='USD':raise ValueError('Historical stock currency must be USD')
    out={}
    for row in data.get('values',[]):
        day=date.fromisoformat(row['datetime'][:10]).isoformat()
        if day in out:raise ValueError('Duplicate historical date')
        value={'open':positive(row['open']),'close':positive(row['close'])}
        if symbol in WATCHLIST:
            volume=float(row['volume'])
            if not math.isfinite(volume) or volume<0:raise ValueError('Invalid historical volume')
            value['volume']=volume
        out[day]=value
    if not out:raise ValueError('No historical bars returned')
    return out

def run(series,start,end,slippage_bps=25,fx_fee_bps=15,purchase_limit=MAX_POSITION):
    if purchase_limit not in (100,150,200):raise ValueError("Research purchase size must be £100, £150 or £200")
    slip,fee=float(slippage_bps)/10000,float(fx_fee_bps)/10000
    if not all(math.isfinite(x) and 0<=x<=.02 for x in (slip,fee)):raise ValueError('Cost allowances must be 0–200 bps')
    ordered,selected,rates=prepare(series,start,end,WATCHLIST)
    rankings={};histories={}
    for day in selected:
        i=ordered.index(day)
        bars={s:[series[s][d] for d in ordered[i-50:i]] for s in WATCHLIST}
        rankings[day]=rank_histories(bars)
        histories[day]={s:[r['close'] for r in rows] for s,rows in bars.items()}
    comparisons={}
    for name in LABELS:
        cash=PAPER_CAPITAL;positions={};trades=[];curve=[];locked=False
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
            opening=cash+sum(p['qty']*series[s][day]['open']*fx for s,p in positions.items())
            if opening<=PAPER_CAPITAL*(1-LOSS_FRACTION):locked=True
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
                for s in WATCHLIST:buy(s,day,PAPER_CAPITAL/len(WATCHLIST),'Equal-weight benchmark')
            equity=cash+sum(p['qty']*series[s][day]['close']*fx for s,p in positions.items())
            if equity<=PAPER_CAPITAL*(1-LOSS_FRACTION):locked=True
            if day==selected[-1]:
                for s in list(positions):sell(s,day,series[s][day]['close'],'End-of-test liquidation')
                equity=cash
            curve.append({'date':day,'equity':equity,'cash':cash,'positions':len(positions)})
        comparisons[name]={'metrics':metrics(curve,trades,selected[0],selected[-1],PAPER_CAPITAL),'curve':curve,'trades':trades}
    return dict(schema_version=2,strategy_settings=settings()|{"max_purchase":purchase_limit},labels=LABELS,
        requested_start=start,requested_end=end,start=selected[0],end=selected[-1],sessions=len(selected),
        symbols=list(WATCHLIST),initial_gbp=PAPER_CAPITAL,slippage_bps=slippage_bps,fx_fee_bps=fx_fee_bps,
        warnings=WARNINGS,comparisons=comparisons)


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
