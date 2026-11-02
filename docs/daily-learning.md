# Daily learning and bounded candidate generation

The Daily Learning panel runs from a separate local worker once per weekday after 16:15 America/New_York, when the app is running (21:15 Europe/London on 2 November 2026; the offset differs during daylight-saving transition weeks). It reviews the available research journal and detailed execution records. On startup, the worker recovers up to five missing evidence dates per minute from the last 366 days. It uses saved signal, decision or execution dates only; days with no saved evidence remain unknown. Each recovered report has its original 16:15 New York evidence cutoff and its actual completion time. Outcomes after the cutoff are excluded. Recovered reports cannot select or advance a candidate; the current review freezes any new candidate at the actual processing time. Early-close sessions wait until 16:15 too, avoiding premature reviews without a paid calendar. US holidays may produce a no-new-data review. Restarting preserves completed reviews. Review failure is visible and does not interrupt the trading worker.

## Evidence and scorecards

Each source/model has recorded, measured, pending, missing, positive and negative BUY counts. The measured horizon is 120 elapsed hours, with the existing journal's maximum 72-hour sampling delay. BUY averages use the journal's modelled round-trip costs. News remains nondirectional context: an observed rise is not a successful news prediction. Differently worded duplicate reports, overlapping horizons and scanner selection bias mean records are not independent observations. These are descriptive scorecards, not evidence of causal or benchmark-adjusted predictive value.

A positive BUY suggestion that the recorded active action did not buy is labelled a missed BUY observation, not forgone profit. Unexamined/unknown decisions are excluded. No cash, capacity or execution feasibility is inferred. Negative skipped suggestions are retained too. Daily trade review includes detailed recorded closes and modelled execution costs, not invented legacy accounting. Costs already reflected in realised P&L must not be subtracted again.

## Controlled model selection

Exactly two predefined models are considered: 20/50 trend (reference) and 10/30 trend (challenger). For each common stock/day snapshot, a BUY scores its observed 120-hour hypothetical long return after costs; HOLD/SELL scores zero cash exposure. This is a signal comparison, NOT a portfolio simulation. Within each observation date the scores are equal weighted. Both models must share the same baseline and have measured outcomes. Missing outcomes never score zero.

Only observations at least 193 hours old enter evaluation, allowing the full measurement window to close. Gates require 30 distinct observation dates, 20 BUY observations per model, at least 90% paired coverage, a positive challenger average, an advantage above 0.1 percentage points and a worst daily score no worse than the reference. These are minimum screening gates, not proof of significance or portfolio drawdown control.

If development gates pass, the selection time, evidence summary and generated source hash are frozen. Only signals first seen AFTER selection enter the prospective test. The same gates apply to that later sample. A completed failure remains rejected, preventing automatic repeated searches on the same holdout. Passing creates a review proposal, not an order or active rule change. Portfolio-level validation with position constraints, drawdown and separate approval is still required.

## Scope of code rewriting

The bot generates a versioned Python candidate file in its private data/learning-candidates directory, using a validated fixed template for the selected model. Syntax is checked and subsequent tests verify its SHA-256. Production evaluation uses the equivalent, predefined journal model; tests verify template equivalence. Generated Python is not dynamically imported or executed by the trading worker. Arbitrary LLM-authored code, novel indicators, model-provider subscriptions and unrestricted self-modification are NOT implemented.

Active source, ledger, risk controls, credentials and updater are never modified by this learner. Existing purchase-size experiments remain separate. The active signal queue is capped at 10,000 records. Completed or expired observations move to a local archive without deletion; exports and model scorecards include both stores. Unresolved windows are never evicted to make space. A full pending queue still pauses new records. Archival cannot recover previously unrecorded evidence. The archive grows on disk and should be backed up with the data directory. Reviews retain the last 366 dates. Daily review and recovery make no market-data requests.


## Follow-up coverage

After the mandatory five-holding check and within the existing optional-stage cadence, one due signal symbol may occupy an unused optional quote slot. The queue prioritises the earliest expiring outcome window. This quote uses normal credits, never reserved holding credits, and needs no new history. It only resolves evidence; it cannot become an active entry or seed new model signals. Shared active quotes remain reusable. Missing/stale prices stay unmeasured; this bounded queue cannot promise complete coverage.

## Paired portfolio test

Two new forward paper books start with the same £10,000 cash, five slots, £1,000 cap, engine trend/stop exits, user buy block, independently evaluated drawdown controls and 0.25% price/0.15% FX allowances. Only the replacement rule differs. This tests the replacement rule, not the separate generated 10/30 model. No active holdings are copied or altered, no deposits are added and no historical results are invented.

The books use the same fresh scanner prices, FX, completed histories and candidate opportunities. All required holdings in both books must be present; otherwise neither book advances and the gap is counted. Both books and the audit snapshot commit in one transaction. One comparison holding may use an unused optional slot after active candidates; along with follow-up quotes, the overall six optional-stock ceiling and existing quota reserve still apply. Missing additional divergent holdings freeze both books rather than increasing the budget. The stream remains conditioned on the active scanner; it is not an unbiased whole-market experiment.

Returns use marked liquidation equity including estimated exit costs. Daily curves retain the latest 1,000 dates and detailed snapshots the latest 5,000; cumulative counts remain. Coverage means completed / attempted comparison snapshots, not uptime or full-session coverage. Pauses and Mac downtime are not fabricated observations. The daily SPY quote is reused when still fresh; no additional benchmark request is made. The cash-aware benchmark applies the previous observed baseline exposure to the next GBP SPY price move, with zero cash interest, no dividends and no benchmark dealing costs. Portfolio returns over those exact benchmark endpoints are shown separately. A short sample cannot establish profitability; neither model is automatically promoted.

## Read-only diagnostics

The dashboard and monitor expose the latest review, ten review summaries, persistent review-worker attempt/success/error timestamps, candidate hash/phase, archive counts and comparison coverage. Monitor responses cap closed-trade detail at 20 with a truncation flag; the local review retains its full recorded detail. Accounting reports flag a truncated 100,000-execution read. Missing stores are explicitly unavailable. Review health is separate from trading-worker progress; monitor and saved worker versions remain separate. An update release does not prove installation or a running worker.
