# Trading Lab 2026.10.11.2

Paper-only Mac bot with £1,000 contributed capital and £200 maximum purchases.

- Decision journal records signals, holdings, skipped candidates and reasons.
- Forward observations track later price moves without changing trades.
- Walk-forward comparison evaluates £100, £150 and £200 purchase limits using
  past-only selection and a separate final 63-session test.
- Signed dashboard updates require user installation approval and include
  backups and attempted rollback on caught startup failure.

108 local synthetic automated tests passed; the workflow reruns tests before
publishing. No live market feed or Mac launchd update has been verified.
These tests do not establish profitability. News remains observation-only.

Download Trading_Bot_Mac_Setup.zip, extract it and use Mac_Setup.command option 8
for an existing installation. Read START_HERE_MAC.md. Preserve your local config
and data; never upload them to this repository.

In Dashboard → Updates, configure `declanpeabody/trading-lab-releases` using the
public.pem belonging to your locally generated signing key. The release includes
public.pem for comparison; use your original local copy as the trust source.
An installed version equal to this release correctly reports no newer update.
Future releases require a higher version and the same signing key.
