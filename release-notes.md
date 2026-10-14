# 2026.10.14.2 — Position prices and unrealised returns

Positions now shows shares, simulated purchase price and latest recorded price
in USD, USD price change and percentage, invested GBP cost, GBP position value,
and unrealised GBP return with percentage. Positive/negative changes use explicit
signs and green/red colouring. A total summarises open holdings only, excluding
cash and previously closed trades. The table scrolls horizontally on phones.

GBP return uses the stored cost basis, including simulated purchase costs, and
the most recent account FX rate. It therefore differs from USD share-price
performance and does not include future selling costs. Confirmed stock splits
adjust entry price as before. Missing data displays placeholders, not invented
prices or returns. The last successful account-check timestamp and a blocked/
paused notice make stale values clear. No new market-data requests are made.

Trading rules, holdings, account history and risk controls are unchanged.
