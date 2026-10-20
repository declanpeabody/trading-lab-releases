# Daily learning and bounded candidate generation

The Daily Learning panel runs from a separate local worker once per weekday after 18:00 America/New_York, when the app is running. It reviews the available research journal and detailed execution records. Missed days are not reconstructed. US holidays may produce a no-new-data review. Restarting preserves completed reviews. Review failure is visible and does not interrupt the trading worker.

## Evidence and scorecards

Each source/model has recorded, measured, pending, missing, positive and negative BUY counts. The measured horizon is 120 elapsed hours, with the existing journal's maximum 72-hour sampling delay. BUY averages use the journal's modelled round-trip costs. News remains nondirectional context: an observed rise is not a successful news prediction. Differently worded duplicate reports, overlapping horizons and scanner selection bias mean records are not independent observations. These are descriptive scorecards, not evidence of causal or benchmark-adjusted predictive value.

A positive BUY suggestion that the recorded active action did not buy is labelled a missed BUY observation, not forgone profit. Unexamined/unknown decisions are excluded. No cash, capacity or execution feasibility is inferred. Negative skipped suggestions are retained too. Daily trade review includes detailed recorded closes and modelled execution costs, not invented legacy accounting. Costs already reflected in realised P&L must not be subtracted again.

## Controlled model selection

Exactly two predefined models are considered: 20/50 trend (reference) and 10/30 trend (challenger). For each common stock/day snapshot, a BUY scores its observed 120-hour hypothetical long return after costs; HOLD/SELL scores zero cash exposure. This is a signal comparison, NOT a portfolio simulation. Within each observation date the scores are equal weighted. Both models must share the same baseline and have measured outcomes. Missing outcomes never score zero.

Only observations at least 193 hours old enter evaluation, allowing the full measurement window to close. Gates require 30 distinct observation dates, 20 BUY observations per model, at least 90% paired coverage, a positive challenger average, an advantage above 0.1 percentage points and a worst daily score no worse than the reference. These are minimum screening gates, not proof of significance or portfolio drawdown control.

If development gates pass, the selection time, evidence summary and generated source hash are frozen. Only signals first seen AFTER selection enter the prospective test. The same gates apply to that later sample. A completed failure remains rejected, preventing automatic repeated searches on the same holdout. Passing creates a review proposal, not an order or active rule change. Portfolio-level validation with position constraints, drawdown and separate approval is still required.

## Scope of code rewriting

The bot generates a versioned Python candidate file in its private data/learning-candidates directory, using a validated fixed template for the selected model. Syntax is checked and subsequent tests verify its SHA-256. Production evaluation uses the equivalent, predefined journal model; tests verify template equivalence. Generated Python is not dynamically imported or executed by the trading worker. Arbitrary LLM-authored code, novel indicators, model-provider subscriptions and unrestricted self-modification are NOT implemented.

Active source, ledger, risk controls, credentials and updater are never modified by this learner. Existing purchase-size experiments remain separate. Source journal retention is still 10,000 records; this panel reports when it is full. Reviews retain the last 366 dates. The feature adds no network or market-data calls.
