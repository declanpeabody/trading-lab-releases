# Automatic installation — version 2026.10.12.2

Install this version once from **Software Updates → Check for updates → Install
reviewed update**. Then click **Enable automatic updates** in that panel.
The setting starts off and persists when enabled. Future signed releases are
checked shortly after startup and daily, then installed automatically at weekends
or between 5pm and 8am New York time on weekdays. Keep the Mac awake, online and
the service running. Disabling automatic mode keeps manual checks/installations.

Automatic mode authorises future signed releases, including strategy changes.
Passing tests and a valid signature are not a separate human review. Existing
backup, service restart, version/health check and attempted rollback remain in
place. An attempted version cannot automatically retry, even after restarting;
review a failure in Software Updates and manually retry after resolving it.
An uncertain launch is held for manual inspection. Power/disk failure can still
require manual recovery. The 300-stock scanner, paper funding and purchase limits
are unchanged by this update.

---

# Expanded paper screening — 2026.10.12.1

Install from Software Updates → Check for updates → Install reviewed update.
Versioned changes to GitHub main now publish automatically after the tests pass;
you no longer need to run the release workflow yourself.

The live scanner targets 300 USD-denominated US common stocks supported by the
provider on NASDAQ/NYSE. It keeps the original 30 when listed, then fills the rest
by stable SHA-256 ordering of catalogue tickers. This is a reproducible sample,
not the largest 300 companies, a market index or a stock recommendation.
Catalogue changes are picked up approximately weekly. If fewer stocks are
returned, the dashboard shows the real count instead of claiming 300.

Each cycle checks holdings and up to six candidates using histories prepared
today. It then downloads up to 20 additional histories, with a three-minute batch
time budget checked between requests. Those histories become eligible on the
NEXT cycle. Each cycle is followed by a 15-minute wait; API processing increases
the interval. At least 15 batches are needed for 300 stocks. Quota limits, pausing,
outages and Mac sleep may extend coverage across sessions. A persistent cursor
resumes where it left off so the end of the list is not starved. Each new day
clears candidate histories, but retains the cursor. Failed histories retry next day.

Only valid same-day downloaded histories on a common completed-session grid are
ranked. Missing stocks are skipped individually. Held histories, quotes and FX
must all validate before decisions. Volume eligibility is for entries only;
missing volume does not stop exits. The first scan after installation usually
prepares data without entering trades. The dashboard separates cached histories
from histories used for the latest decisions.

Local daily ceilings remain 500 total attempts for normal screening/research and
700 for priority holdings checks. These counters cannot see other apps using the
key. Research competes for the budget; run it outside trading hours. No paid
subscription is required by the code, but data access depends on your entitlement.

The journal records the universe plus holdings. Its latest 200 rows prioritise
visibility of held/selected decisions. Storage grows faster at this size.
Histories downloaded after decisions are not retroactively added to their evidence.
Forward outcomes are best-effort when later history retains the reference.

Historical/walk-forward reports remain a FIXED 30-stock baseline. They do not
simulate the expanded universe, batching or partial coverage and cannot establish
its profitability. Paper capital (£1,000), maximum purchase (£200), five-position
cap, existing risk rules, account history and news observation-only mode are kept.

---
Earlier release notes follow; the current behaviour is described above.

# Research and decision journal — version 2026.10.11.2

Install this download using Mac_Setup.command → option 8, then open
**Research & Journal** from the dashboard. The GitHub repository is now available at
https://github.com/declanpeabody/trading-lab-releases. Follow its README to
configure the signing secret, publish a release and connect the update channel.

## What is new

- The decision journal records all 30 watchlist stocks and any extra holdings on
  each active regular-hours scan: indicators, rank, selection status, available
  quote, account controls and the actual BUY / SELL / HOLD / SKIP reason.
  Failed checks are labelled NOT_EVALUATED. Missing data is not fabricated.
- Live journal outcomes appear as completed future bars arrive through normal
  scans. No additional data-provider requests are made for this tracking.
  The +1, +5 and +20 observations measure cost-free USD movement from the prior
  completed close to that many session closes after the observation date.
  They are not trade P/L or executable missed opportunities. They exclude FX,
  costs and dividends. The reference is rebased using the same adjusted history
  as the future price, to accommodate stock splits.
- In Research mode, choose **Walk-forward evaluation**. Use at least 315 trading
  sessions (roughly 15 months), plus the automatically requested indicator warm-up.
  An initial 126 sessions train the selection; subsequent forward windows have
  up to 63 sessions. The final 63 sessions stay outside development and are
  evaluated only after the final purchase size is selected.
- The experiment compares only £100, £150 and £200 purchase caps with the same
  fixed entry/exit rules. The predeclared training score is return percentage
  minus half the maximum drawdown percentage. Ties choose the smaller purchase.
  This is parameter selection, not a trained machine-learning model.
- The comparison panel shows forward windows and the final reserved period,
  alongside the fixed £200 strategy, alphabetical selection, equal-weight hold
  and cash. The JSON report includes all variant metrics and training scores.

## How to use the evidence

Run historical downloads outside trading hours: they share the Basic API budget.
Data access and date coverage depend on your provider entitlement. The first
request still needs roughly four minutes for data; research reuses cached prices.
The journal starts collecting after installation while paper trading is active.
No past journal data is invented. Refresh the journal to see new observations.
It displays the latest 200 entries and exports the latest 10,000; older entries
remain in the local decisions.sqlite3 database. That database grows over time.

Every testing window resets to £1,000 cash and liquidates at its end. Do not
compound those fold returns as if they were one continuous portfolio. Compare
returns, drawdowns, trade counts and consistency, not only the best result.
The main historical chart remains the full-period fixed-rule comparison and
is not a graph of the walk-forward selected portfolio.

Once you inspect the reserved period it is no longer an untouched test for
future strategy design. Preserve reports, avoid repeatedly tuning to it, and
use genuinely later paper observations for further evaluation. Intraday
observations overlap and do not constitute independent samples.

Paper capital, the £200 live purchase cap, pause state, news observation-only
behaviour and trading rules are unchanged. Research never promotes parameters
or changes the paper account. Journal writes are best-effort; a write failure is
shown in scanner notes and does not alter trades. A crash between a trade and
journal write can leave a missing observation. Outcome fields remain blank when
later history is missing or the bot was offline too long to retain the reference.

This release adds no new application filenames, paid dependencies or signing
keys. It is compatible with the previous signed updater's file allowlist, but
requires a configured channel and a separately published signed release for
in-dashboard installation. Mac launchd and real market feeds remain untested
in this development environment.

---

# Signed dashboard updates — version 2026.10.11.1

This package adds an Updates panel. Install it once using Mac_Setup.command,
option 8, from this extracted download. That bootstraps the updater; future
compatible releases can be installed through the dashboard after a release
channel has been configured. Existing settings, news observations and paper
history are retained. The account remains paper only, with £1,000 contributed
capital and a £200 maximum purchase.

**Release hosting is not configured in this download.** No repository or release
was published. Open Dashboard → Updates to see the setup fields. You need a
public release repository and the publisher's trusted RSA public key. Do not
paste any private signing key, API key or GitHub token into this panel.

Once connected, checks run daily while the service is running. Read the release
notes, then click Install reviewed update. Signature and file hashes are checked
before replacing files. The independent Mac updater waits for the old service
to stop, backs up the complete data folder and application, and restarts it.
Paper decisions are held until the version/health check passes. A caught startup
failure triggers restoration of the prior application and data snapshot.

Power loss, a forced process kill, or disk failure can interrupt recovery. If the
panel stays on “installation in progress” or paper decisions remain held, keep
`~/Library/Application Support/DeclanTradingLab/updates` intact and request
recovery assistance. Do not delete the data folder or the update_hold marker
without first checking which version and backup are complete. Backups contain
private paper history and should remain local. These interruption scenarios
require manual recovery; automatic rollback is not guaranteed.

The update channel accepts only the existing application file allowlist. A new
release requiring additional files or Python dependencies needs a fresh setup
package. Docker installations continue to use image rebuilds. Actual launchd
installation, live GitHub downloads and Mac recovery still require a Mac test.
See RELEASE_PUBLISHING.md for publisher setup and signing instructions.

---

# Ranked watchlist and matching settings tester — 11 October 2026

Install using the newly downloaded Mac_Setup.command → **8 — Upgrade installed
bot**. This preserves credentials, holdings, history and funding. The previous
£900 funding increase is still one-time only. News stays observation-only.

The running bot now evaluates a fixed 30-stock research sample instead of
rotating alphabetically through thousands of stocks:
AAPL, MSFT, AMZN, GOOGL, META, NVDA, AMD, AVGO, ORCL, CRM, COST, PEP, JPM,
BAC, V, MA, UNH, JNJ, ABBV, MRK, XOM, CVX, CAT, DE, GE, HON, WMT, HD, PG, KO.
This is not a recommended investment portfolio. Today's selection introduces
selection/survivorship bias when tested historically. Provider catalogue data
resolves Nasdaq/NYSE identities; missing or ambiguous identities are rejected.

Every US trading day, completed bars for all 30 stocks are fetched and cached
locally across restarts. The first daily check can take five or more minutes.
Eligibility uses the prior close above SMA20 above SMA50, price >= $1, and
20-session average dollar volume >= $1m. Score = 20-session percentage return
divided by daily return volatility, with a 1% volatility floor. This simple
research rule is unproven and its parameters have not been optimised.

Each cycle checks fresh prices for held stocks plus the six highest-ranked
unheld eligible stocks. The price must still pass the existing trend rule to
buy. At most five positions are open; purchases remain <= £200 including costs,
from £1,000 contributed capital. New entries are blocked if any watchlist
history is missing or sessions do not align. Held-stock monitoring continues
where valid data is available. A delisted, halted or stale held stock still
prevents a valid full-portfolio cycle; exits are not guaranteed. Old holdings
outside this sample remain managed, not automatically sold or increased.

Open **Scanner coverage → Daily candidate ranking** to see scores. Rankings
are daily, not a real-time whole-market ranking. Quotes and current-price entry
checks remain intraday. No sector cap, volatility-based position sizing or new
exit strategy has been introduced by this update.

**Historical Testing** now downloads the same 30 stocks plus USD/GBP (32
requests including the catalogue) and uses shared strategy settings/ranking:
£1,000 starting cash, £200 per purchase, five positions and the same exit/loss
thresholds. It compares ranked selection with an alphabetical-selection
baseline on the same sample, equal-weight hold across all 30 names and cash.
The alphabetical baseline is NOT an exact replay of the previous giant rotating
watchlist. Equal-weight hold is not restricted to five positions.

This is still a DAILY APPROXIMATION: prior-session signals fill at the next
open. Intraday 15-minute decisions, intraday drawdowns and the 3% intraday loss
block are not replayed. No guarantee of executable fills, dividends or taxes
is included. Tests begin from empty positions and do not replay your actual
funding or trade history. Full warnings and shared settings are in each report.
Older reports remain readable and labelled legacy; run a NEW test for this
version. Reports do not alter the running paper account.

Historical downloads take at least four minutes, depending on provider access
and quota. Prefer running outside US trading hours. The free API budget remains
shared with paper monitoring, with a reserve for held-stock checks. Missing
historical volume, sessions or warm-up data produces an explicit failure.
Choose a development period and reserve later dates for evaluation before
changing parameters; these tools do not automatically optimise or train a model.

## Previous funding update — unchanged

Install this download using Mac_Setup.command → **8 — Upgrade installed bot**.
Existing £100-funded accounts receive a one-time £900 simulated cash addition.
Trade history, holdings, previous profit/loss, credentials and control flags are
preserved. Reinstalling or restarting does not repeat the addition. New accounts
start with £1,000. Actual available cash depends on existing holdings and P/L.

New purchases are capped at £200 including modelled costs, with at most five
open positions. Existing positions are not automatically increased. Profit/loss
excludes the cash addition; the 10% account loss threshold uses £1,000 funding
and the current day's starting equity is adjusted for the deposit. Existing
latched loss blocks remain in force. News remains observation-only.

Historical Testing keeps its previous £100/£20 daily model; these changes apply
to the running paper account. No real funds or broker orders are involved.

## News observer upgrade — existing Mac installation

1. Download and unzip this package into a new folder.
2. Open its Mac_Setup.command and choose **8 — Upgrade installed bot**.
3. Open the dashboard and select **OPEN NEWS PANEL**, or visit
   http://127.0.0.1:8765/news on the same Mac.
4. Enter your contact email locally and select **Save email & enable**. The SEC
   receives this email in the automated request User-Agent; it is saved privately
   on your Mac, not displayed back in the panel or included in exports. No SEC
   account, paid subscription or AI API key is needed.
5. Allow a few minutes for the first check. Inspect coverage and any error. SEC
   access from your Mac has not been verified here. A 403/429 is reported without
   trying to bypass restrictions. There may legitimately be no recent filings.

News is OBSERVATION ONLY and initially disabled. It cannot change purchases,
sales, risk controls or the historical tester. Its separate worker checks held
stocks plus the latest six scanner candidates (up to 12 symbols total) every
30 minutes, including outside trading hours while the service is running. With
no holdings/candidates it uses AAPL, MSFT and AMZN. Manual checks have a 5-minute
cooldown. News has its own enable/disable switch; pausing paper trading does NOT
disable news. Disable takes effect after any current request completes.

This first free version is a rules-based SEC FILING METADATA analyser, not a
general-media feed, full-document summariser, sentiment AI or price predictor.
It labels selected 8-K item codes, annual/quarterly reports, foreign issuer
reports and selected registration/prospectus forms. Labels flag a review topic;
they do not establish earnings surprises, contract size, financing completion,
guidance direction or future returns. Missing/ambiguous ticker-to-CIK mappings
are skipped, never guessed. Not all US-listed firms use the same filing forms.

Every poll considers filings dated within the previous seven days. Earlier
filings found on setup are clearly dated and must not be interpreted as breaking
news. SEC acceptance times and first-received UTC times are kept separately.
No event means no matching observed filing, not proof of no material news.

Source links, forms/item codes, metadata hashes, analyser version and the
available baseline price-signal snapshot are saved in data/news/events.sqlite3.
This snapshot can be stale and its timestamp is shown. Duplicates are keyed by
stock and accession; the first captured evidence is never silently rewritten.
The panel shows 100 recent observations; Download observation log exports all
stored records as JSON. Neither returns your SEC contact email or API keys.
No news-aware performance or hypothetical trade returns are calculated yet.

The observer uses no Twelve Data credits, has no broker access, sends no data
to an LLM, and will never open arbitrary links found inside filings. SEC requests
are limited to known JSON endpoints, use timeouts and a response-size limit,
and run at no more than one request per 1.1 seconds. News failures are shown
separately and do not suppress price-based exits.

## Previous Free Basic scanner upgrade — unchanged

This update changes the RUNNING PAPER BOT, not the historical tester. Download
and unzip the new package, open its Mac_Setup.command, and select option 8.
Settings, credentials, paper history and enabled/paused status are preserved.
No paid subscription is enabled and no real brokerage orders are possible.

The bot now rotates through Twelve Data's US-dollar common-stock catalogue for
Nasdaq and NYSE, excluding ambiguous duplicate symbols. It checks held positions
plus six candidate stocks per cycle. New entries need a price of at least $1,
50 completed daily candles and at least $1m average daily dollar volume over
20 completed days. These are conservative filters, not evidence of profit or
achievable execution. Stock availability on Trading 212 is NOT verified.

Regular session only: 09:30–16:00 America/New_York, weekdays, with a fresh quote
and provider market-open flag required. No extended-hours, overnight, foreign
exchange stock listings, ETFs or OTC stocks are enabled. The Mac must be awake
and logged in. An open dashboard is not required. The next cycle starts about
15 minutes after the previous scan ends; a scan can take several minutes.

This is a slow alphabetical round-robin, NOT whole-market live surveillance
or a strongest-signal ranking. With thousands of listings a complete sweep can
take weeks. The cursor survives restarts and the catalogue refreshes weekly.
See Scanner coverage for catalogue size, candidate selections, valid quotes
and skipped-stock reasons. Initial authenticated catalogue/data access remains
to be verified on your Mac; no fabricated scan results are included.

The £100 starting account, £20 maximum purchase, 5% observed-price exit and
drawdown blocks are retained. Open positions are capped at five. Exits are
processed before candidate entries. All held-stock prices and history must be
valid before any decision; stale or unavailable held data blocks the cycle,
so exit timing is NOT guaranteed. Illiquid candidates are skipped individually.

API attempts are metered persistently and shared with historical research:
500/day for non-priority requests; 700/day total with the remainder reserved
for held-stock checks and their FX conversion. Counts reset by UTC date, not
by restarting. This is NOT the provider's balance: earlier usage or other apps
using your key may cause the provider to block data sooner. All requests are
spaced by at least eight seconds. Prefer running historical tests outside US
market hours to avoid contending with paper monitoring.

Historical Testing still uses the original AAPL/MSFT/AMZN daily approximation.
It does not backtest this rotating scanner. API keys and passwords remain private.

## Historical testing — unchanged

1. Unzip this updated package into a new folder.
2. Open **Mac_Setup.command** from that new folder.
3. Choose **8 — Upgrade installed bot** (do not choose Install again).
4. Sign in with your existing dashboard username/password. Open **Historical
   testing**, or visit http://127.0.0.1:8765/research on the same Mac.
5. Start with 1 January 2023 through yesterday, leaving costs at their defaults,
   then select **Run historical test**. Your existing saved Twelve Data key is
   used privately. No new subscription is purchased by the updater.

The updater stops the local service and waits up to 30 seconds for it to
unregister and release the worker lock before backing up old app files and the paper
SQLite database inside the private application folder, replaces application
files, then restarts. It preserves config.json, the API key, password and paper
history. It refuses to update while another process holds the paper database
lock. Re-running this downloaded updater is allowed; data is never reset.

The strategy's existing enabled/paused status is preserved. Backtesting uses
separate files and never places orders or changes the running strategy. If you
want paper decisions paused during setup, pause them in the dashboard first.

## Reading the reports

Reports compare £100 starting portfolios: the fixed strategy; £20 in each stock
plus £40 cash; £100 divided equally across the three; and £100 cash with no
interest. They include returns, daily maximum drawdown, closed trades, win rate,
modelled transaction costs, calendar-year returns and a portfolio-value chart.
Export daily values or trade records as CSV, the full report as JSON, or use
Print / save as PDF. No result is shown until a real data download succeeds.

100 basis points = 1%. Defaults are 25 bps adverse price movement and 15 bps FX
allowance on each buy and sell. These are assumptions, not broker fee quotes.
Change costs to assess sensitivity, not to engineer a profitable result.

Daily approximation: signals use completed prior daily bars and execute at the
next session open. The 5% exit is checked using the prior close. It does not
replicate the 15-minute bot, its 10:00 New York entry window or 3% intraday loss
rule. It includes a persistent 10% account drawdown block on new buys. No
intraday high/low trigger or exact stop fill is assumed. A gap can worsen losses.
Both benchmarks and strategy close all holdings at the final close with costs.

All prices are split-adjusted; dividends, interest, tax and corporate-action
cash flows are excluded. FX uses the most recent daily close strictly before
the stock day, rather than pretending the day's final FX rate was known at its
open. Rates older than four calendar days are rejected. Missing/unequal stock
dates, inadequate warm-up and truncated requested periods cause a visible error.

Using today's AAPL/MSFT/AMZN universe introduces selection/survivorship bias.
This module does not optimise parameters or train machine learning. Repeatedly
choosing dates or parameters because results look good is overfitting. Reserve
an untouched later period before assessing a candidate strategy.

## Downloads and access

The initial historical run uses four daily-series requests (three stocks and
USD/GBP) and usually takes around a minute. Historical coverage depends on your
Twelve Data account; an API error must be resolved before results are available.
Research and paper requests share a process-wide rate limiter. Cached historical
prices are reused for identical date ranges; use Refresh cached prices to fetch
again. Reports include retrieval timestamps and data hashes for reproducibility.

Research files live alongside, but separately from, the active paper ledger at
`~/Library/Application Support/DeclanTradingLab/data/research`. Only the newest
report is displayed; earlier report JSON files remain on disk. Credentials are
not included in reports or exports. The dashboard remains local to your Mac.

Tests cover simulation math, next-session execution, costs, missing data,
look-ahead protection, report exports and preservation during update. They use
synthetic fixtures, not a claimed profitable backtest. Actual Mac update and
Twelve Data access must be verified on your Mac. No real Trading 212 execution
is implemented.

---

Original installation instructions for a new Mac follow below.

# Run the trading lab on your Mac

This Mac edition cannot submit real Trading 212 orders. No broker credentials
are needed. There is no cloud-hosting bill; electricity and any market-data
subscription are separate. The strategy is unproven.

## Install

1. Install a current stable Python 3 release (3.12 or newer) from
   https://www.python.org/downloads/macos/ if needed. In Applications →
   Python [version], run `Install Certificates.command` to enable HTTPS
   verification for market-data connections.
2. Unzip this package. Double-click `Mac_Setup.command` and choose **1**.
   Alternatively open Terminal, type `bash `, drag `Mac_Setup.command`
   into the window and press Return. Review downloaded scripts before running
   them; do not disable macOS security globally.
3. Enter your Twelve Data API key privately in that window, or press Enter
   to add it later. Without a key, you can see the dashboard but no market
   data or simulated trades. Data access/entitlements may have separate costs.
4. Save the generated dashboard password privately. The browser opens
   http://127.0.0.1:8765 . Sign in as **declan** with that password.
5. With data configured, select **Start paper trading**. The strategy starts
   paused. The first check may take up to 15 minutes.

## Keep your Mac running

Leave it powered, connected to the internet, awake and logged in during
trading hours. In macOS Battery/Energy settings, enable the option to prevent
automatic sleeping when the display is off (on power adapter for laptops).
The screen can turn off. Keep a MacBook plugged in with its lid open.
The installer does not change your power settings.

You can close Terminal and the browser after setup. The service starts at
login and restarts if its process exits. You must log in after a reboot.
Logging out, sleep, lid closure, shutdown or loss of internet interrupts checks.
The strategy checks every 15 minutes within 10:00–15:45 New York time,
usually 15:00–20:45 UK time, with differences during DST transition weeks.
The provider's market-open flag also prevents trading on closed markets.

## Installed files and privacy

Files go to `~/Library/Application Support/DeclanTradingLab`.
`config.json` contains your market-data key and local dashboard password,
protected with owner-only permissions (not encrypted Keychain storage).
The `data` folder holds the simulated account history. Never send these
files or credential screenshots to chat. The login service is
`~/Library/LaunchAgents/uk.declan.tradinglab.plist`.
The dashboard listens only on 127.0.0.1 and is accessible on this Mac only.
No administrator privileges, Docker, port forwarding or public hosting needed.

## Controls

Reopen `Mac_Setup.command`:
- **2:** Open dashboard.
- **3:** Start service and restore startup at login.
- **4:** Stop service until next login.
- **5:** Change market-data key and restart.
- **6:** Stop and remove login startup, keeping data and credentials.
- **7:** Show the dashboard password locally if forgotten.

Dashboard **Pause all activity** stops simulation decisions but leaves the
dashboard service running. **Block new buys** leaves exit monitoring active.
Existing paused/running strategy state is retained on restart.
If Python's executable moves after an update, the login service needs repair.
Do not delete the data folder to resolve a startup error.

## Status and limitations

Core simulation and HTTP authentication tests were run in Linux using
synthetic inputs, alongside Mac installer configuration tests. macOS launchd,
actual Mac installation and authenticated market-data access have not been
tested on your Mac. The existing README documents the trading rules and
limitations; follow this file for Mac setup instead of its cloud instructions.
The 5% exit threshold is periodically checked, not a guaranteed loss limit.

Real-money execution is unfinished: account isolation, instrument resolution,
currency conversion, order intent/fill reconciliation and broker demo testing
are still needed. Hosting this prototype does not enable broker trades.

Official references:
- https://docs.python.org/3/using/mac.html
- https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html
- https://support.apple.com/guide/mac-help/set-sleep-and-wake-settings-mchle41a6ccd/mac
