# Release batch 2026.10.14.5

Declan authorized publication on 14 October 2026. Installation is confirmed separately through the dashboard.

- Commit mandatory held-stock checks before optional candidate requests. Revalidate all quotes before a second transition; retain the 120-second ceiling.
- Skip candidate quotes when the account has no entry capacity and comparisons are disabled. Experiments still share at most six optional quote slots.
- Exclude recognisable warrants, units, rights and preferred listings, including incorrectly labelled catalogue rows. Refresh catalogues after held-account decisions.
- Record saved worker progress and explicit price/volume decision basis for read-only monitoring.
- Optional SEC document evidence: bounded excerpts, source hash and retrieval time. No sentiment inference or forum scraping.
- Opt-in, independent prospective paper books: £200 reference, smaller risk-based allocations with reserve/correlation checks, and SEC filing-risk entry deferral. Missing shared holdings data freezes comparisons. No automatic promotion from these books.
- Optional Mac setup options 9/10 for monitor login startup and removal. Runtime secret is stored in an owner-only local file, not encrypted Keychain storage; never in launch arguments. Actual launchd operation needs iMac verification.

The active stock selection and existing purchase-size experiment promotion rules are unchanged. These comparisons are not proof of improved returns. Allocation is per-cycle, not a daily entry cap. SEC coverage is not comprehensive news coverage. Sector caps, licensed forum/news ingestion, and an exact intraday scanner replay remain future work; the existing daily replay is approximate.

Release gates: full automated suite, browser script checks and signed asset verification. After installation, verify held checks, monitor login/revocation, and opt-in comparisons on the iMac. No paid service or new subscription is enabled.
