# Trading Lab 2026.10.22.1 — tested paper portfolio replacement

Full portfolios can now replace a weaker holding when a stronger checked candidate qualifies on two adjacent completed-session grids. The rule requires a score gap of 2 and historical momentum advantage of at least 3 percentage points and twice modelled switching costs. It is an unproven screening heuristic, not a forecast of returns.

- Minimum three completed sessions since entry; three-session portfolio replacement cooldown; five-session re-entry cooldown for a replaced-out stock.
- Normal stop-loss and trend exits run first. Account controls, corporate-action checks, regular-hours and quote freshness limits remain enforced.
- Both replacement legs commit atomically, with linked execution evidence. New size uses net sale proceeds, capped by existing purchase limits; idle cash is not used to enlarge a replacement.
- Optional replacement checks retain the free-plan 30-minute cadence and existing request reserves. With experiments off, one candidate quote is requested. Require at least 30 comparable histories; partial coverage remains explicit.
- Scanner displays the latest hold/replace reason and comparison. Read-only monitor includes replacement review after its process is restarted on this release.

Deterministic tests cover execution, cash/cost reconciliation, transaction rollback, controls, confirmation and cooldowns, data failures, and scanner request bounds. Desktop/mobile browser checks cover the new display. These checks validate software behaviour, not profitability. No historical or forward investment-performance advantage is claimed.

Install using Software Updates. Installation and running worker version must be verified locally; publication alone does not install the update.
