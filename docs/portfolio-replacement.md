# Portfolio replacement — paper only

This rule addresses full portfolios that retain weaker holdings indefinitely while stronger price-trend candidates exist. It is a predefined, unproven momentum rule, not an autonomous learning promotion, fundamental valuation decision or forecast of profit.

The normal holdings check and stop/trend exits run first. For the expanded £10,000 paper account / £1,000 purchase-cap profile, the optional scanner stage can check replacements even when all five slots are full. It retains the existing 30-minute optional-work interval, quote freshness checks, API reserve and maximum six optional quote slots shared with experiments. With experiments off, one leading candidate quote is requested. Daily history preparation and all five holdings retain their existing priorities. No paid data is required.

A replacement requires:

- At least 30 rankable histories and identical grids of 50 completed daily bars for the candidate and every holding. Partial coverage is disclosed; this is not a claim to have screened the whole market.
- All account controls allow buying, and all holdings have passed the normal exit check.
- The strongest freshly checked, eligible candidate exceeds the weakest holding's volatility-adjusted momentum score by at least 2. Its historical 20-session momentum advantage must exceed or equal the greater of 3 percentage points or twice the modelled switching-cost percentage.
- The same pair must qualify on two adjacent completed-session grids. Repeated intraday observations do not count as another session. A changed pair, failed qualifying review or skipped grid restarts confirmation. State survives restarts. No pre-installation confirmation is inferred.
- The outgoing holding has at least three completed sessions after its entry date. At least three completed sessions after the previous portfolio replacement must pass. A replaced-out stock cannot be bought again until five completed sessions after its sale, including through normal entries.
- All holdings, candidate and FX quotes are validated together, under the existing 120-second limit, during the regular session. Missing, invalid, expired or unavailable candidate data cannot cause a discretionary sale.

The sell and replacement buy are persisted in one SQLite transaction, with linked execution IDs and the comparison evidence. The purchase is capped at the outgoing holding's net sale proceeds, the account purchase limit and any research cap. It does not use idle cash to enlarge the position. If a complete executable pair cannot be formed, neither replacement leg commits. Normal risk exits remain independent and are never held up waiting for a replacement.

Costs use existing allowances on both legs. The switching friction is `1 - ((1-price_cost)*(1-fx_cost))/((1+price_cost)*(1+fx_cost))`. Comparing historical strength with this allowance is a conservative heuristic, not evidence that future returns will cover the costs.

The Scanner panel shows the latest replacement reason, pair, timestamp, coverage, momentum advantage and cost estimate. Execution accounting retains both legs and their evidence. The read-only monitor exposes the latest review and last replacement date after its process is restarted onto this version.

Validation covers deterministic qualifying/nonqualifying paths, two-session confirmation, restart persistence, cost and cash reconciliation, controls, normal-exit priority, minimum holding age, cooldowns, mismatched/future bars, partial coverage, nonexecutable size, stale/missing candidate data, and real scanner integration with bounded optional requests. These tests establish implementation behaviour; they do not establish investment performance. Historical portfolio tests that do not explicitly enable this new rule must not be presented as its performance record. Observe forward paper results against the previous rules before making profitability claims.
