# Controlled experiments — version 2026.10.13.4

Strategy Experiments compares £100/£150/£200 purchases in separate prospective
paper accounts. Bounded automatic promotion, a frozen forward test, fixed safety
limits and one-click restoration are included. See [release notes](release-notes.md)
for the exact gates, data limits and controls. Working market data is required.

---

# Request diagnostics — version 2026.10.13.3

Fixes local request errors being mistaken for invalid provider JSON. The dashboard
now flags hidden characters in a saved API key without a provider request, even
outside market hours. See [release notes](release-notes.md).

---

# Reliability update — version 2026.10.13.2

Install through Software Updates, or your opted-in automatic updater.
See [release notes](release-notes.md) for the seven changes, retention limits,
manual corporate-action verification and the synthetic scanner replay limits.
The release does not change the free plan or paper trading limits.

---

# Market-data recovery — version 2026.10.13.1

Install through Software Updates or automatic updates. At the next scheduled
regular-hours scan, connection failures now show a specific, safe category in bot
status and Scanner notes. Send that message for diagnosis; do not send API keys.
A shared outage stops the batch and pauses provider requests for up to 15 minutes
while the process remains running. Held-position checks also cannot obtain fresh
data during this pause. The next scheduled request after it expires probes again.
Individual symbol/request errors continue to skip just that stock.

The upgrade removes old generic connection-failure cache markers, allowing
same-day recovery without resetting the request budget or paper account. It does
not disable TLS checks or claim to fix the still-unknown underlying connection
failure on your Mac. If your normal request budget is already exhausted, it still
must reset before screening can resume.

---

# Declan's Trading Lab releases

Public source and signed updates for a **paper-only** Mac trading research bot.
It cannot submit real-money broker orders. Source version: **2026.10.13.4**. Live screening targets 300 supported US stocks.

The source, synthetic regression tests and an automatic release workflow are
included. The release channel is active. The owner has completed signing setup; future
newer versions publish automatically after changes to main pass the tests. New installations can
follow the setup instructions below.

## One-time signing setup on your Mac

Open Terminal and paste this block. It creates a private signing key on your Mac
if none exists, derives its public key, copies the PRIVATE key to your clipboard,
and opens GitHub's encrypted Actions secret form. Nothing is sent to ChatGPT.

```bash
bash <<'SETUP'
set -e
umask 077
keys="$HOME/.trading-lab-signing"
mkdir -p "$keys"
if [ ! -f "$keys/private.pem" ]; then
  openssl genrsa -out "$keys/private.pem" 3072
fi
openssl rsa -in "$keys/private.pem" -pubout -out "$keys/public.pem"
pbcopy < "$keys/private.pem"
open 'https://github.com/declanpeabody/trading-lab-releases/settings/secrets/actions/new'
SETUP
```

In GitHub's form:

- **Name:** `RELEASE_SIGNING_KEY`
- **Secret:** paste from the clipboard, then click **Add secret**.

Do not paste this private key in chat, an issue, a repository file or a release.
Keep `~/.trading-lab-signing/private.pem` securely backed up. Retain the same key
for future releases. Once saved, clear the clipboard by copying ordinary text.

## Automatic publishing

Push or merge an updated version to main: GitHub runs the tests, then signs and
publishes automatically. Pull requests only test. Existing complete releases are
skipped; an older version cannot replace the latest release. The manual workflow
button remains available for recovery. Signing failures prevent publication.

On your Mac: **Software Updates → Check for updates → Install reviewed update**.
Install version 2026.10.12.2 once, then click **Enable automatic updates** in the
Software Updates panel. This setting is off until enabled and persists across
restarts and updates. You can disable it or install manually at any time before
an installation starts. The bot does not create new strategies automatically
or provide public access to the Mac account.

## Connect your Mac dashboard

Install the updater bootstrap once from the setup ZIP using option 8. In
**Dashboard → Updates**, enter `declanpeabody/trading-lab-releases`.
Copy your PUBLIC key with:

```bash
pbcopy < "$HOME/.trading-lab-signing/public.pem"
```

Paste it in the public-key field, save the channel and check for updates. The
channel is pinned. Equal versions report no newer update. Checks run daily while
the service runs, with a check shortly after startup. Automatic mode installs
signed releases at weekends or 17:00–08:00 America/New_York on weekdays, with
backup, restart, version/health verification and attempted rollback on failure.
The Mac must be awake and online. An attempted release will not automatically
retry after failure or restart; inspect the status and retry manually if needed.
Automatic mode authorises every future signed release, including strategy changes;
signatures and passing tests do not establish separate human review. Power loss,
forced termination or disk failure may still require manual recovery.

## Future upgrades

Change source in `bot/`, increase `bot/version.py`, update `release-notes.md`,
then push or merge to main; tests and signed publication run automatically. Never include local
config.json, API keys, paper databases, news history, logs or private keys in
commits. The workflow uploads only explicit public asset filenames.

The updater requires the exact application file allowlist; new runtime files or
dependencies require a new bootstrap. Journal and research use existing modules.
Tests verify software behaviour, not returns. Mac launchd, power-loss recovery,
visual layout and live feeds have not been validated in the development Linux
environment. See bot/START_HERE_MAC.md and bot/VALIDATION.md for limitations.

## Dashboard market overview

Version 2026.10.12.3 adds eight navigation tabs and an external TradingView market
panel. This uses no Twelve Data credits and never changes trading decisions.
Index quotes can be restricted or delayed. The optional ETF view shows fund share
prices rather than index levels; CNBC links are supplied for the exact indices.
See TradingView's widget data policy:
https://www.tradingview.com/widget-docs/faq/data/
