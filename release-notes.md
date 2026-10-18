2026.10.18.1 — Portfolio-first Overview

- Redesigned Overview around total account value, cash, holding count, a recorded-performance chart, allocation and a compact holdings table.
- Recent activity and Why no trade? are collapsed by default. Opening them is preserved during polling. Current errors, data age, risk warnings and trading controls stay visible.
- New Reports tab contains detailed performance, attribution, execution accounting and benchmark information. Diagnostics contains data testing, request budget and private monitoring controls.
- Return / Value chart uses only recorded daily observations. Return uses existing deposit-adjusted measurement; account value includes deposits. Empty and single-observation histories do not invent a trend. Lines connect observations, not measured intraday prices.
- Responsive dark layout, native keyboard-accessible disclosures and text labels for gains/losses. All figures remain simulated saved valuations, not streaming quotes.
- No changes to trading rules, account funding, position limits or market-data requests.

Design references: official Trading 212 portfolio overview and Interactive Brokers Client Portal overview documentation (account balances, performance, allocation and holdings).
Validation: dashboard server tests, JavaScript syntax and Python compilation; GitHub full regression and desktop/mobile browser checks required before publication. Browser fixtures are synthetic and never included as account history.
