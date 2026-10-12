# Trading Lab 2026.10.12.1

- Expanded live screening to a target of 300 supported NASDAQ/NYSE US common stocks.
- Keeps the original 30 where listed, then fills the target using a stable
  catalogue sample. It is not a largest-300 index or the entire market.
- Checks holdings and up to six prepared candidates before downloading the next
  batch of up to 20 histories. New histories enter the following scan's ranking.
- Only same-day downloads with matching completed-session grids are ranked.
  Missing histories skip individual stocks instead of blocking all new entries.
- Retains local 500 normal / 700 priority request ceilings and the £1,000 paper
  capital, £200 purchase cap and five-position limit.
- Dashboard shows actual universe, downloaded, ranked, pending and unavailable
  counts, plus tickers. Journal includes the expanded universe.
- Historical/walk-forward research is explicitly labelled as a fixed 30-stock
  baseline, not a replay or validation of the expanded scanner.
- Future versioned changes to main publish automatically after tests pass.

A full scan needs at least 15 history batches. Quota, missing data, service uptime
and request duration affect coverage. Each cycle waits 15 minutes after processing.
Stocks not yet prepared cannot be bought. Live provider data and the expanded scan
have not been tested on the user's Mac. This remains paper-only.
