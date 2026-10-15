> Version 2026.10.15.1 active profile: five holdings, £10,000 contributed paper capital, £1,000 purchase cap. Holdings checks wait five minutes after processing. Optional work runs at most every 30 minutes once holdings exist, preparing at most five histories. Free-plan rate and daily ceilings are unchanged; optional requests preserve remaining holdings credits. Older cadence/batch descriptions below describe the legacy research profile, not this active monitoring schedule.

# Mac historical testing upgrade

Existing Mac users: read **START_HERE_MAC.md**, then run the new **Mac_Setup.command** and choose **8 — Upgrade installed bot**. Your existing settings and paper account are preserved. Open **Historical Testing** from the dashboard to compare daily simulated results with buy-and-hold and cash and download performance reports.

See **VALIDATION.md** for verified behavior and remaining limitations.

> **Mac edition:** Start with START_HERE_MAC.md. Cloud instructions below are retained for reference.

# Declan's trading bot — cloud prototype

**Status: built for cloud deployment, not deployed or connected to Trading 212.**
It starts in paper mode. No real orders have been placed. Its strategy is an
unproven example, not a promise of profit. The entire £100 can be lost.

## What it does

- Runs on a cloud server independently of your phone or computer.
- Provides a password-protected mobile dashboard and pause/resume controls.
- Evaluates AAPL, MSFT and AMZN using completed daily 20/50-day moving averages.
- Buys when price > 20-day average > 50-day average; sells below the 20-day
  average or after an observed 5% decline from purchase price.
- Uses £100 initial paper cash, at most £20 per purchase, one position per
  symbol, and at most £60 invested across the three example stocks.
- Checks every 15 minutes during a restricted US regular-session window.
- Pauses new buying after a 10% decline from initial portfolio value, or a 3%
  decline from the day's first observed equity. Exit monitoring continues.
- Records state and decisions on a persistent disk, with one-process locking.
- Includes a Trading 212 **read-only connection checker**. Automated broker
  execution is deliberately not implemented in this prototype: order/fill
  reconciliation and account isolation need broker integration testing first.

The first release is a functional cloud paper-trading bot, **not a finished
real-money Trading 212 bot**. This distinction also appears in the dashboard.
It must not be advertised or used as a tested profitable strategy.

## What is needed to activate it

1. A cloud Docker service or Linux server, with an always-on process, HTTPS,
   and a persistent disk mounted at `/data`. Do not use a service that sleeps
   when nobody visits. Run one replica only. This package does not purchase
   hosting or create a cloud account.
2. A Twelve Data API key with permission to obtain the required US stock and
   GBP conversion quotes. Stale, missing, delayed or closed-market quotes are
   rejected. Data entitlements may cost money; confirm them before subscribing.
3. A strong dashboard password set privately as a hosting secret. Never paste
   account passwords or API secrets into chat or commit them to source control.
4. For read-only Trading 212 connection verification, a demo key and secret
   with account-reading permission only. They are optional for paper mode.

Hosting/data costs are separate from the £100. For scale, a hypothetical £5
monthly charge equals £60/year, or 60% of the starting capital. Use an existing
service if available; do not subscribe expecting this bot to pay for itself.

## Cloud deployment

Upload the contents to a private repository or your server. Use the included
Dockerfile. Set the variables in `.env.example` through the host's secret
settings, attach persistent storage, and expose port 8080 behind HTTPS.
The public health-check path is `/healthz`; all other paths require the
dashboard username/password. No broker or data credentials are sent to the
browser. Use a private network or access gateway where your provider supports it.

For an existing cloud Linux server with Docker Compose:

```sh
cp .env.example .env
# Edit .env privately and set a strong password and the market-data API key.
docker compose up -d --build
```

Compose binds to loopback. Configure your cloud HTTPS reverse proxy to forward
to `127.0.0.1:8080`; do not expose unencrypted Basic authentication externally.
If the host runs its own container routing, use Dockerfile directly and its
managed HTTPS endpoint instead. The app binds to `0.0.0.0:$PORT` inside Docker.

The dashboard initially says **Paused**. Select **Start paper trading** after
checking configuration. The scheduler then runs without the dashboard open.
State survives restarts if `/data` is retained. Without a data key, the app
remains usable but displays a setup error rather than inventing market prices.

## Read-only Trading 212 connection check

Set `T212_API_KEY` and `T212_API_SECRET` in a private shell/secret manager, then:

```sh
python broker_check.py
```

This calls only the official **demo** account-summary endpoint. It never
submits an order and prints no credentials or account identifier. It reports
the primary account currency and whether account-reading authentication works.
The runtime trading service does not use broker credentials.

## Controls and limitations

- **Pause all activity** stops future simulation decisions; it does not sell
  positions. The dashboard remains available. There are no broker orders to
  cancel in this release.
- **Block new buys** leaves automated exits running. An automatically triggered
  drawdown block remains latched until the process is reviewed; it cannot be
  cleared with the resume button. Day-loss blocks reset on the next US date.
- Price-based exits are checked periodically, not exchange-held stop orders.
  Gaps, missing data and outages can cause losses beyond the stated thresholds.
- Paper fills use observed quotes plus a 0.25% adverse price adjustment and a
  0.15% FX-cost allowance on each side. These are configurable modelling
  assumptions, not verified broker charges. There are no partial fills,
  dividends, corporate actions, taxes, borrow costs, or real order-book fills.
- GBP valuation uses a fresh USD/GBP quote; FX movement affects performance.
- The bot uses the provider's market-open flag and only operates 10:00–15:45
  New York time on weekdays. No order decision runs outside that window.
- This is forward paper testing, **not a historical backtest**. No fabricated
  win rate, forecast, profit figure, or backtest is included.
- Logs and credentials are not public. Keep disk backups private. Never delete
  the disk to resolve an error: that resets the paper ledger and its history.

## Before implementing real orders

Required next engineering work: isolate bot-owned positions from existing
holdings; resolve exact broker instruments; budget in GBP including fees and
reserved orders; persist order intent before submission; never blindly retry
ambiguous orders; reconcile partial fills, cancellations and expired orders;
handle rate limits and corporate actions; exercise those paths in Trading 212
demo; and validate current endpoint contracts with the actual account.

Trading 212 documents its order endpoint as non-idempotent. A network timeout
can leave a successful order with no response, so adding a simple buy/sell HTTP
call would not make this a reliable autonomous real-money system.

## Verification

```sh
python -m unittest discover -s tests -v
```

Tests cover budget constraints, transaction atomicity, duplicate daily buys,
pause/restart persistence, stale-data rejection, drawdown controls and data
validation. They use synthetic fixtures and make no network calls. Broker and
market-data integration have not been tested with authenticated accounts.

## Sources checked 3 October 2026

- https://docs.trading212.com/api/section/general-information/api-limitations
- https://docs.trading212.com/api/orders/placelimitorder
- https://docs.trading212.com/api/accounts/getaccountsummary
- https://twelvedata.com/docs/markets/market-state

Python 3.12+, standard library only. Review source before supplying secrets.
