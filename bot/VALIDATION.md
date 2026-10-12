# Automatic-update validation — 2026.10.12.2

20 focused updater tests passed locally. Coverage includes signed release and
hash rejection, backup rollback, opt-in persistence, New York window boundaries
including daylight-saving time, failed/uncertain launch latching, restart
persistence, manual retry, installation holds and exact staged-version checks.
JavaScript syntax and Python compilation passed. The complete regression suite
also gates publication through GitHub Actions.

Launchd, an actual unattended Mac restart and recovery after power loss cannot
be exercised in this Linux environment. No live paper account was accessed.

---

# Expanded-universe validation — 2026.10.12.1

117 automated tests passed locally with synthetic data.

New synthetic checks cover a 300-symbol target from a larger filtered catalogue,
20-history batches, deferred candidate eligibility, holdings checks before batch
downloads, full coverage across 15 batches, cache/cursor persistence, per-symbol
failures, missing held histories, daily cache reset, normal-budget exhaustion,
common-session ranking and expanded journal records. Existing research/backtest
and account tests remain part of the full regression suite.

Historical research remains the original 30-stock baseline and does not validate
the expanded scanner's returns. No live provider key or live Mac account was used
for these tests. JavaScript syntax and Python compilation were checked locally.
Automatic publication is gated on the complete GitHub test suite. The release
check rejects incomplete existing releases and downgrades; successful existing
versions are skipped without overwriting their assets.

# Research validation — 2026.10.11.2

108 automated tests passed with synthetic data on Linux. Added coverage includes:
- Past-only purchase-size selection and separation of the final reserved period.
- Future / reserved-price perturbations cannot change earlier selections.
- £100 research purchase caps and cash-flow reconciliation.
- Completed research-job persistence, provenance and unchanged paper-account state.
- Full-watchlist decision records, actual buys and position-limit rejections.
- Outcomes withheld until completed later bars arrive; forward-return arithmetic.
- Missing history and failed scans labelled explicitly; journal failure isolation.
- Authenticated journal access/export and research page controls.

All Python modules compile. Every inline JavaScript block passes node --check.
No browser visual test, live data download, Mac installation or launchd restart
was performed. Synthetic test performance is not evidence of investment returns.
No live account settings were changed, no ML model was trained, and no GitHub
release was published. See START_HERE_MAC.md for the experiment's limitations.

# Updater validation — 2026.10.11.1

94 automated tests passed on Linux with synthetic data. Added tests cover signed
release verification, manifest/archive tampering, per-file hashes, version
rollback rejection, ZIP traversal rejection, pinned channel configuration,
private-key rejection, HTTPS host restrictions, authenticated/CSRF-protected
update requests, failed-startup restoration, and incomplete-backup preservation.
All Python modules compile and all inline JavaScript passes node --check.

Mac launchd execution, live GitHub release downloads and a complete Mac update /
restart cycle have NOT been tested in this environment. The release channel is
not configured or published. Recovery tests mock service stop/start; they do not
simulate power loss, forced termination or disk failure during restoration.
There was no live market-data test and no visual browser validation.

# Ranked strategy upgrade — 11 October 2026

82 automated tests pass in Linux. New coverage includes shared ranker selection
in the live paper scanner and daily tester, daily cache restart, incomplete
coverage blocking new entries while preserving held-stock checks, position and
cash limits, cost/P&L reconciliation, next-open fills, future-price/volume
isolation, prior-day FX, missing history/volume rejection, and new-report exports.
Old £100 reports remain readable/exportable. Tests use synthetic fixtures.

The tester shares versioned strategy settings, ranking and exit thresholds with
the running bot but is explicitly a DAILY approximation, not exact intraday
replay. All 30 names were chosen today and create historical selection bias.
Authenticated provider access, actual Mac installation and profitability remain
unverified. Browser visual verification is unavailable in this environment.

## Previous funding update — 11 October 2026

71 tests passed. A legacy account receives exactly one £900 addition and
preserves holdings, historic events, P/L and control flags across restart.
New accounts start at £1,000. Updated trading tests verify £200 purchase limits,
five-position limits, cash reconciliation and loss blocks. Historical research
remains on its previous £100 model. Actual Mac installation is unverified.

## News observer — 9 October 2026

69 automated tests pass in Linux. New tests cover observation-only isolation,
form/item classification, source identity, malformed/unsafe document names,
lookback dates, no fabricated events, deduplication, first-received persistence,
captured baseline context, failure isolation, private contact storage, refresh
cooldown, skipped mappings, endpoint allowlisting, authentication, CSRF and
safe text rendering. All earlier scanner, trading, backtest and updater tests
remain included. Test inputs are synthetic, not profitability evidence.

The analyser classifies SEC metadata only. No full-text NLP, general-news API,
LLM usage or news-driven order control has been implemented. No authenticated
or user-identified SEC request has been made from this build environment. Actual
SEC connectivity and macOS installation must be verified on the user's Mac.
Python compilation, all three dashboard JavaScript syntax checks and the Mac
shell wrapper syntax passed. HTTP routes were exercised against a local fixture
server. Visual browser checks could not run because the environment has no
installed Chromium executable; no visual QA success is claimed.

## Previous Free Basic scanner — 9 October 2026

53 automated tests pass in Linux, including 15 new tests for catalogue filtering,
ambiguous symbols, cursor restart, dynamic portfolio caps, stale/unavailable
holdings, candidate errors, pause during fetch, extended-hours rejection,
regular-session boundaries, volume filters, shared daily quota persistence and
priority reserves. Tests use synthetic data and mocked network calls.

The running worker now uses scanner.py. Historical research retains its original
three-stock universe. Prior shutdown-wait fixes are included. Python compilation,
dashboard JavaScript parsing and shell syntax are checked for this package.
Live Twelve Data catalogue/schema/entitlement compatibility, provider quota
behaviour, Mac installation and browser visual rendering remain unverified.
No profitability claim or authenticated external data test is made.

## Previous historical testing upgrade — validation

34 automated tests pass in the Linux build environment. Coverage includes prior-close/next-open timing, future-data isolation, prior-day FX, missing data rejection, trading costs and cash reconciliation, portfolio limits, maximum drawdown, report exports and persistence, authenticated research routes and CSRF protection, plus upgrade backups, preserved credentials/history and rollback on a failed file copy. Existing paper-bot and Mac configuration tests also pass. Python compilation and both dashboard JavaScript syntax checks passed.

No authenticated Twelve Data download was performed here: the API key remains on the user's Mac. Run the first historical test locally to verify the account's historical data entitlement. No profitability claim is made. macOS LaunchAgent execution and visual browser rendering of this upgrade were not verified in this Linux environment.

The backtest is a fixed-rule daily approximation, not an exact replay of the 15-minute paper worker. It excludes the intraday daily-loss rule, dividends, taxes and infrastructure costs. The report lists these limits and its dataset provenance. No machine-learning training, optimisation, or real-money broker execution is included.

## Earlier package validation

## Mac edition — 8 October 2026

20 tests passed, including the original 17 tests and three Mac configuration tests. Shell syntax check passed. Tests ran on Linux; launchctl, Mac installation and real market-data access remain unverified.

# Verification record — 3 October 2026

`python -m unittest discover -s tests -v`: **17 tests passed**.

The first attempt could not open local sockets in the restricted environment.
The same unchanged test suite passed when run with localhost socket access.
Tests did not call Trading 212, Twelve Data or any other external service.

Verified:
- paper purchase cost caps, nonnegative cash and modelled transaction costs;
- no duplicate purchases on repeated evaluations;
- SQLite persistence across a ledger restart;
- pause, buying blocks, total drawdown latch and continued exits;
- rejection of incomplete, nonfinite, stale or mismatched input;
- exclusion of today's incomplete candle from historical averages;
- dashboard authentication, CSRF protection and control persistence;
- clear paper-only labelling and response security headers.

Not verified:
- authenticated market-data or Trading 212 connectivity;
- Docker image build or a real cloud deployment;
- real-money execution (not implemented);
- historical profitability, live market fills or investment performance;
- browser-based visual inspection or mobile-device testing.

The dashboard shows initial £100 simulated cash and no trades; it contains no
fabricated historical performance or prepopulated investment results.
