# Stock Research Desk — 2026.10.20.4

This release implements seven research improvements. It does not promote a strategy, change the active paper account, connect a broker, add a paid subscription, or expand generated-code execution.

| Improvement | Implementation | Boundaries |
|---|---|---|
| Company fundamentals | SEC Company Facts: annual revenue growth, net margin, operating cash less capital expenditure, two long-term debt components, cash and year-on-year share count | Standard US-GAAP USD tags only. Unsupported/custom/IFRS facts remain unknown. These are annual figures, not TTM. Debt does not claim all lease/pension/other obligations. |
| Valuation | Annual P/E, price/sales and FCF yield; comparable SIC-group median where at least three peers have suitable periods; prospective own-stock P/E history with at least three distinct observed price dates | Latest completed close; recent filed shares required for market cap. Loss-making EPS does not produce a misleading negative P/E. No fair value or target price. Share-class/split basis requires review. |
| Market/industry context | 20-session relative price return, sample breadth, rising/falling/mixed trend; exact aligned dates | Cached scanner stocks, not a broad-market index. At least 20 comparison stocks; at least three industry peers. Two-digit SIC groups are rough industry groups, not formal GICS sectors. |
| Earnings/catalysts | Source-linked, user-verified event dates; event edits/revisions; deduplicated SEC accessions; comparison of guidance keyword excerpts | Future dates are user supplied and user-attested. No automatic earnings calendar or consensus feed. Text differences are not directional guidance changes or earnings surprises. Existing legacy calendar remains independent. |
| Portfolio selection support | Account-mark industry exposure, illustrative purchase exposure, available slots and aligned return correlations against holdings | Research flags only. Missing classifications/correlations remain explicit. 40% industry and 0.8 correlation flags are illustrative, not new active risk limits. No trading orders. |
| Evidence-backed reports | Per-stock supporting/opposing evidence, missing data, reassessment conditions, source links, valuation peers and filing revisions | Deterministic summaries, not a language-model analyst. No confidence percentage or claim of an independently proven edge. |
| Realistic evaluation | New portfolio stress mode: configurable capital/cap, full-spread assumption, adverse slippage and FX; three fixed cost scenarios; SPY, cash and existing baselines; overnight gaps; optional dividend cash/receivable accounting | Next-open daily approximation, not intraday execution or partial fills. Current fixed 30-stock universe has selection/survivorship bias. Historical access depends on provider entitlement; no facts from today enter historical signals. |

## Access and use

Install through Software Updates. Open **Stock Research Desk** in the navigation or Research & Journal. No comparison reset is required.

Company facts use the existing SEC contact configured in News. With SEC monitoring enabled, each poll attempts no more than two Company Facts requests across the currently held/selected stocks (at most 12). Each stock is attempted no more than once per 24 hours, including failures. The existing SEC request throttle applies. Other scanner names remain visible with missing company facts until they enter that coverage. There are no new Twelve Data requests for the stock reports.

Facts snapshots are append-only until retention: two years, at most 10,000 snapshots. Each stores the received time, raw-payload hash, chosen fact tags, units, periods, filing dates and accessions. Same-day filings without acceptance times wait until the next day. Revised values never overwrite previously collected evidence. This is prospective evidence collection, not a full historical point-in-time dataset. A source failure retains older facts with a visible error; refreshes older than seven days disable valuation calculations.

The new event form accepts a source URL, timezone-qualified date and verification attestation. It never fetches arbitrary URLs. Editing resets the UI verification checkbox and records the prior date, revision and update time. Up to 1,000 events and 10,000 revisions are retained. No record means unknown coverage. Existing manually entered events in the legacy calendar are separate and do not become verified automatically.

Stock reports use completed split-adjusted cached sessions. Duplicate dates, invalid prices, fewer than 50 sessions, stale last dates or >40% discontinuities make the price analysis unavailable pending review. Partial coverage is displayed. The original scanner's trading validation is unchanged.

## Portfolio stress mode

Select **Portfolio stress test**, set dates and assumptions, then run it. Defaults are £10,000 capital, £1,000 purchases, five positions, 10 bps full spread, 25 bps adverse slippage and 15 bps FX conversion allowance. Half the assumed full spread applies each way. Additional scenarios add 25 and 75 bps to the base slippage (capped at 200), without choosing a winner or changing a strategy.

An uncached run uses the existing stock catalogue plus 30 stock histories and FX, and one additional SPY history request (33 requests). Requests use the existing research quota; run outside market hours. Histories are cached. SPY identity, currency, exchange and session coverage are checked. Price-only SPY and fully invested equal-weight portfolios differ in exposure from the capped five-stock strategy.

Optional dividend JSON (100 records maximum) contains:

```json
[{"symbol":"AAPL","ex_date":"2025-02-10","payment_date":"2025-02-13","amount_usd":0.25,"source":"https://example.com/issuer-dividend-announcement"}]
```

This is an illustrative schema, not verified dividend data. Supply the net USD amount per share on the same split-adjusted basis as historical prices. Entitlement is fixed from holdings before ex-date trading; payment arrives on the first observed session on/after the payment date. Until then it is a receivable valued with the available prior daily FX rate, including the modelled FX allowance. Receivables still outstanding at the final date remain in equity, not cash. Dividend completeness, tax treatment and share-basis verification remain the researcher's responsibility. Blank input is prominently labelled price-return-only, not total return. Trade win rates exclude separate dividend income.

Stress-test reports retain dataset hashes, acquisition timestamps, assumptions, scenarios, largest overnight gaps, curves and individual transactions. Three scenarios share the same data; they are not independent or an untouched holdout. The existing daily-learning development/forward-test process remains separate.

## Verification

Tests cover period alignment, future-data exclusion, identity and non-finite input rejection, append-only restatements, request limits, missing evidence, negative-EPS handling, aligned peer comparisons, account immutability, event revisions, authentication/CSRF, source URL safety, spread accounting, dividend entitlement/payment/receivables, future-price isolation, SPY coverage and the complete cached research job. Browser checks cover mobile layout, event editing, escaping untrusted issuer text, source-link filtering and stress-table rendering.
