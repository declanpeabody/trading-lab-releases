# Release 2026.10.15.1 — five-holding free-plan monitoring

Pending review/release; not installed automatically.

Adds £9,000 simulated funding once (£10,000 contributed capital), preserves holdings/history/losses and risk controls, and sets a £1,000 purchase cap with FIVE holding slots (supersedes the earlier 20-slot proposal). Existing positions are not topped up or forcibly sold.

Worker waits five minutes after processing. Holdings and FX are checked first with the unchanged 120-second freshness ceiling. Once holdings exist, optional candidates, paper comparisons and history preparation run at most every 30 minutes; histories are limited to five per optional batch. Comparisons receive fewer observations. The 300-stock universe warms up more slowly. With no holdings, small history batches may run each cycle to prepare initial entries.

Request spacing remains eight seconds, normal daily ceiling 500, holdings ceiling 700, and research subcap 50 outside regular hours. All optional requests additionally preserve six credits per remaining five-minute regular-session slot plus 30 recovery credits, including before the market opens. These are conservative local estimates, not the provider balance; another app using the key or repeated failures can still exhaust the allowance. No subscription change. Checks take processing time in addition to the five-minute wait.

Legacy research simulations still use their own original capital, capacity and cadence; they do not replay this new monitoring schedule. Paper only. Publication requires explicit release approval.
