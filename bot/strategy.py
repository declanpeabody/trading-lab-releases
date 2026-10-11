"""Shared, versioned research rules; parameter choices are not optimised."""
import math
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
