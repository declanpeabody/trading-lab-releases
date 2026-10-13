# 2026.10.13.5 — Connection diagnostics and data scheduling

- Overview now includes an authenticated, CSRF-protected Test data connection button.
  It performs at most one AAPL daily-history request, including outside market
  hours, using the normal budget and existing outage cooldown. Repeated clicks
  are limited to one test per minute. Results are timestamped and separate from
  the trading ledger; successful history access does not prove quote/catalogue
  access or clear a previous account error. Diagnostic state resets on restart.
- Why no trade? lists current controls, the last recorded data error, history
  coverage and per-stock decisions from the latest check. Partial coverage is
  correctly distinguished from a total block. Old scan results remain dated.
- History downloads preserve seven normal requests per remaining 15-minute
  weekday session slot for candidate checks. Deferred histories remain pending
  and retain their cursor rather than being labelled unavailable.
- The synthetic scanner replay applies the same history reservation rule.
- Research downloads are rejected during the weekday regular-hours window and
  capped at 50 attempts per UTC day within the existing shared 500 normal/700
  total limits. Run research again later to use its retained downloads; research
  is not automatically queued. Held-position credits remain exclusive to active
  holdings. No provider subscription or quota is changed.
- Overview shows local usage, remaining normal/total requests, estimated candidate
  reserve, history allowance and local UTC reset time. Other apps using the key
  are not counted, and coverage is not guaranteed. Exchange holidays/early closes
  are still checked through quote flags; reservations use a weekday clock.

Paper balance, positions, £200 purchase ceiling, trading rules and experiment
promotion requirements are preserved. No real-money execution exists.
