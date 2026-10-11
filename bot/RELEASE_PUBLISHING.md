# Signed releases through GitHub Actions

Repository: https://github.com/declanpeabody/trading-lab-releases

Follow the repository README for one-time key generation on your Mac and the
RELEASE_SIGNING_KEY GitHub Actions secret. The private key must never be put in
source code, release assets or chat. Keep a secure local backup.

Actions → Test and publish signed update → Run workflow on main runs tests,
builds signed assets and publishes the version in bot/version.py. Publishing
requires that secret. Ordinary source pushes only run tests. For future updates,
bump the version and update release-notes.md before manually publishing.

The release channel in the Mac dashboard is declanpeabody/trading-lab-releases.
Use the PUBLIC key from ~/.trading-lab-signing/public.pem as its trusted key.
The offline release_tools.py utility remains available for manual builds.

Mac installation/restart and live downloads still require end-to-end verification.
