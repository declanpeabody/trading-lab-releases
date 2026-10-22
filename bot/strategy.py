"""Shared, versioned research rules; parameter choices are not optimised."""
import math
import hashlib
import statistics

VERSION = 'ranked-daily-v1'
PAPER_CAPITAL = 1000.0
MAX_POSITION = 200.0
MAX_OPEN = 5
CANDIDATES = 6
STOP_FRACTION = .05
LOSS_FRACTION = .10
DAILY_LOSS_FRACTION = .03
# Fixed research sample, not stock recommendations or historically unbiased selection.
WATCHLIST = (
    'AAPL','MSFT','AMZN','GOOGL','META','NVDA','AMD','AVGO','ORCL','CRM',
    'COST','PEP','JPM','BAC','V','MA','UNH','JNJ','ABBV','MRK','XOM','CVX',
    'CAT','DE','GE','HON','WMT','HD','PG','KO')

def assess(rows):
    """Explain the same fixed eligibility rules used by the paper ranker."""
    if len(rows)!=50:raise ValueError('Ranking requires 50 completed bars')
    closes=[float(r['close']) for r in rows]
    volumes=[float(r['volume']) for r in rows[-20:]]
    if not all(math.isfinite(x) and x>0 for x in closes):raise ValueError('Invalid ranking price')
    if not all(math.isfinite(x) and x>=0 for x in volumes):raise ValueError('Invalid ranking volume')
    avg=sum(c*v for c,v in zip(closes[-20:],volumes))/20
    fast,slow=sum(closes[-20:])/20,sum(closes)/50
    vol=statistics.pstdev([closes[i]/closes[i-1]-1 for i in range(30,50)])
    momentum=closes[-1]/closes[-21]-1
    reasons=[]
    if closes[-1]<1:reasons.append('Prior close below $1')
    if not closes[-1]>fast>slow:reasons.append('Trend filter failed')
    if avg<1_000_000:reasons.append('Dollar volume below $1m')
    return dict(prior_close=closes[-1],sma20=fast,sma50=slow,
        score=momentum/max(vol,.01),momentum_pct=momentum*100,
        volatility_pct=vol*100,avg_dollar_volume=avg,eligible=not reasons,reasons=reasons)

def rank_histories(bars):
    ranked=[]
    for symbol,rows in bars.items():
        f=assess(rows)
        if f['eligible']:
            ranked.append(dict(symbol=symbol,**{k:f[k] for k in
                ('score','momentum_pct','volatility_pct','avg_dollar_volume')}))
    return sorted(ranked,key=lambda r:(-r['score'],r['symbol']))

def candidates(ranked, held):
    return [r['symbol'] for r in ranked if r['symbol'] not in held][:CANDIDATES]

def settings():
    return dict(version=VERSION,capital=PAPER_CAPITAL,max_purchase=MAX_POSITION,
        max_positions=MAX_OPEN,candidate_count=CANDIDATES,stop_fraction=STOP_FRACTION,
        loss_fraction=LOSS_FRACTION,daily_loss_fraction=DAILY_LOSS_FRACTION,
        watchlist=WATCHLIST,ranking='20-session return / max(daily volatility, 1%)')


# Live screening is separate from the fixed 30-stock research baseline.
LIVE_VERSION='ranked-batched-300-v1'
UNIVERSE_TARGET=300
HISTORY_BATCH=20

def screening_universe(mapping):
    """Stable catalogue sample, not the largest 300 stocks or an index."""
    seeds=[s for s in WATCHLIST if s in mapping]
    others=sorted((s for s in mapping if s not in seeds),
        key=lambda s:(hashlib.sha256(('trading-lab-300-v1:'+s).encode()).hexdigest(),s))
    return (seeds+others)[:UNIVERSE_TARGET]



REPLACEMENT_VERSION = 'persistent-momentum-v1'

def replacement_review(state, bars, prices, signals, day, candidates, coverage,
                       max_positions, price_cost, fx_cost):
    """Pure, conservative rotation proposal. Historical strength is NOT expected return.

    Confirm the same pair on adjacent completed-session grids, after installation.
    The caller validates fresh quotes and executes both legs atomically.
    """
    result = dict(version=REPLACEMENT_VERSION, day=day, eligible=False,
                  reason='No qualifying replacement', coverage=coverage,
                  note='Historical momentum comparison, not a forecast; partial universe coverage.')
    def stop(reason):
        result['reason'] = reason
        return result
    held=state['positions']
    if any(state.get(k) for k in ('paused','buy_block','risk_latched','day_block')):
        return stop('Trading control blocks replacement')
    if len(held)!=max_positions:return stop('Vacant slots: use normal entries first')
    if coverage<30:return stop('Need at least 30 comparable histories')
    if any(signals.get(s)=='sell' or prices[s]<=p['entry_usd']*(1-STOP_FRACTION) for s,p in held.items()):
        return stop('Normal exit takes priority')
    try:
        symbols=list(held)+[s for s in candidates if s not in held and s in prices]
        grids={s:tuple(r['datetime'][:10] for r in bars[s]) for s in symbols}
        grid=grids[symbols[0]]
        if len(grid)!=50 or tuple(sorted(set(grid)))!=grid or grid[-1]>=day or any(g!=grid for g in grids.values()):
            return stop('Replacement histories must share 50 completed session dates')
        features={s:assess(bars[s]) for s in symbols}
        if any(not math.isfinite(v) for f in features.values() for k,v in f.items() if isinstance(v,(float,int))):
            return stop('Invalid replacement features')
        result['asof']=grid[-1]
        last=state.get('last_replacement_day')
        if last and sum(d>last for d in grid)<3:return stop('Three-session portfolio replacement cooldown')
        available=[s for s in symbols if s not in held and features[s]['eligible'] and signals.get(s)=='buy'
                   and prices[s]>=1 and state.get('last_buy',{}).get(s)!=day
                   and (not state.get('replacement_exits',{}).get(s) or
                        sum(d>state['replacement_exits'][s] for d in grid)>=5)]
        if not available:return stop('No fresh eligible candidate outside re-entry cooldown')
        outgoing=min(held,key=lambda s:(features[s]['score'],s))
        incoming=min(available,key=lambda s:(-features[s]['score'],s))
        opened=held[outgoing]['opened'][:10]
        if sum(d>opened for d in grid)<3:return stop('Weakest holding has fewer than three completed sessions since entry')
        score_gap=features[incoming]['score']-features[outgoing]['score']
        momentum_gap=features[incoming]['momentum_pct']-features[outgoing]['momentum_pct']
        friction=100*(1-(1-price_cost)*(1-fx_cost)/((1+price_cost)*(1+fx_cost)))
        required=max(3.0,2*friction)
        result.update(outgoing=outgoing,incoming=incoming,score_gap=score_gap,
                      momentum_gap_pct=momentum_gap,estimated_switch_cost_pct=friction,
                      required_momentum_gap_pct=required)
        if score_gap<2 or momentum_gap<required:
            return stop('Advantage below score gap 2 or cost-buffered momentum threshold')
        previous=state.get('replacement_review',{})
        first=previous.get('first_asof') if (previous.get('version')==REPLACEMENT_VERSION and
            previous.get('outgoing')==outgoing and previous.get('incoming')==incoming and
            previous.get('qualified') and previous.get('asof') in (grid[-2],grid[-1])) else grid[-1]
        result.update(qualified=True,first_asof=first)
        if first==grid[-1]:return stop('Waiting for same pair to qualify on a second session')
        result.update(eligible=True,reason='Persistent advantage clears historical strength and cost gates')
        return result
    except (KeyError,ValueError,TypeError,IndexError,OverflowError):
        return stop('Incomplete or invalid replacement evidence')
