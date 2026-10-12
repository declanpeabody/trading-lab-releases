# Declan's Trading Lab releases

Public source and signed updates for a **paper-only** Mac trading research bot.
It cannot submit real-money broker orders. Source version: **2026.10.12.1**. Live screening targets 300 supported US stocks.

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
Installation always requires your approval. The bot does not create new strategies
automatically or provide public access to the Mac account.

## Connect your Mac dashboard

Install the updater bootstrap once from the setup ZIP using option 8. In
**Dashboard → Updates**, enter `declanpeabody/trading-lab-releases`.
Copy your PUBLIC key with:

```bash
pbcopy < "$HOME/.trading-lab-signing/public.pem"
```

Paste it in the public-key field, save the channel and check for updates. The
channel is pinned. Equal versions report no newer update. Checks run daily while
the service runs; installation requires your dashboard approval.

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
