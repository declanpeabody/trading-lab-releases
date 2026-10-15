# Release 2026.10.15.1 — five-holding free-plan monitoring

Pending review/release; not installed automatically.

Adds £9,000 simulated funding once (£10,000 contributed capital), preserves holdings/history/losses and risk controls, and sets a £1,000 purchase cap with FIVE holding slots (supersedes the earlier 20-slot proposal). Existing positions are not topped up or forcibly sold.

Worker waits five minutes after processing. Holdings and FX are checked first with the unchanged 120-second freshness ceiling. Once holdings exist, optional candidates, paper comparisons and history preparation run at most every 30 minutes; histories are limited to five per optional batch. Comparisons receive fewer observations. The 300-stock universe warms up more slowly. With no holdings, small history batches may run each cycle to prepare initial entries.

Request spacing remains eight seconds, normal daily ceiling 500, holdings ceiling 700, and research subcap 50 outside regular hours. All optional requests additionally preserve six credits per remaining five-minute regular-session slot plus 30 recovery credits, including before the market opens. These are conservative local estimates, not the provider balance; another app using the key or repeated failures can still exhaust the allowance. No subscription change. Checks take processing time in addition to the five-minute wait.

Legacy research simulations still use their own original capital, capacity and cadence; they do not replay this new monitoring schedule. Paper only. Publication requires explicit release approval.

## Observation-only news and forum research

Adds independently enabled BBC Business and Stockaholics RSS/Atom headline observations to the News panel. Matching covers explicit supported tickers and a small documented company-alias list. Source links, publication/first-seen timestamps, keyword catalyst flags, unverified-discussion labels and deduplicated named-author counts are shown. Not a full-article analyser, verified event calendar or statistical mention-spike detector. Reddit is not connected. Forum availability is unverified on the iMac; restricted sources remain visibly unavailable, without bypass attempts.

Uses two fixed public feeds at most every 30 minutes, including across restarts; no market-data credits or paid AI calls. Response sizes, XML declarations and outbound/link hosts are bounded. Network/storage errors are isolated from trading. Existing SEC news settings and evidence remain separate. Enable the new feeds in News after installation.

Records opportunistic 1h+/1d+ USD changes from the first subsequent fresh saved account quote; timestamps disclose delayed sampling. Missing symbols remain pending, with no additional price requests. These are unadjusted descriptive observations, not proof of predictive value. No rule rewriting, ranking changes or trade triggers. Retention 30 days / 5,000 observations; latest 100 displayed/exported in the feed section.
