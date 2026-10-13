# 2026.10.13.2 — Account safeguards, recovery and scanner replay

- Corporate-action guard: keeps a dated adjusted-price reference for each holding.
  Changed history, an unverified legacy basis or a large quote discontinuity holds
  account valuation and decisions before a false loss sale or drawdown lock.
  Positions provides an explicit, one-time verified share-ratio reconciliation;
  quantity and entry price adjust together while GBP cost basis stays unchanged.
  No paid split endpoint is called. This is a guard and manual verification flow,
  not a complete automatic corporate-action feed. Confirm with the issuer/broker;
  genuine large moves can also require verification. Other exits wait while held.
- Trading worker catches initialization, ledger and scanner-status storage errors,
  exposes in-memory health/heartbeat, and retries failed work after one minute.
  The dashboard distinguishes stopped/overdue workers and unreadable account data.
- All rejected candidate quotes now show Data blocked. Empty warm-up coverage and
  no eligible candidates are separate states. History preparation still proceeds.
- Previous histories are retained across dates and reused only after a fresh
  provider response verifies the identical completed-session grid. Holdings get
  fresh daily histories. A new completed session still requires fresh downloads;
  this does not increase or reset the existing 500/700 request allowance.
- Bounded storage: journal keeps 90 days/up to 250,000 observations; news keeps
  10,000 events; research keeps 10 dated reports/up to 50 MB and 400 cached files/up
  to 200 MB, plus its latest report. Export records you want to retain permanently.
  No-update staging is removed, and resolved update snapshots are pruned to three
  plus any protected stage. Unresolved recovery folders are always preserved.
- Disk-space checks precede update replacement. Manual setup option 8 now uses
  full application/data snapshots, startup/version checks and rollback on failed
  startup, matching the dashboard installer. Credentials and paper rules remain.
  Sudden power loss still requires local recovery; retain unresolved update files.
- Research adds a 300-stock scanner replay using the actual scanner and ledger:
  batch order, partial coverage, history availability, delays and request budgets.
  Daily opening prices supply synthetic repeated quotes, with prior-close FX;
  this is explicitly NOT a reconstruction of intraday performance. JSON includes
  coverage, blocked checks, missing histories and assumptions. No live settings
  or paper account data are changed. Initial research downloads can use up to
  302 real shared requests; cached completed downloads survive an interrupted job.

Paper-only operation, £1,000 contributed capital, £200 maximum purchases, five
positions and the free/basic data plan remain unchanged. No profitability claim.
Existing connection problems still require the specific diagnostic from the Mac.
