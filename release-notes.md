# 2026.10.14.6 — Three reliability fixes

Strategy experiment storage is now isolated from held-stock checks. A separate validated purchase setting survives experiment-storage failure. New entries wait while experiment settings are unavailable; fresh-price valuation and exits continue. Damaged experiment data is preserved rather than reset.

Unreadable news configuration or storage disables only the optional news component. The dashboard displays the problem and the paper trading service can still start. Existing news files are preserved for repair.

An update hold now shows explicitly in worker health and the dashboard. Process liveness is separate from trading readiness so installation can verify a deliberately held service. Future updates record durable phases and backup hashes. Mac setup option 11 validates a complete backup and account database before restoring an interrupted update; interrupted data is retained. It refuses incomplete or altered backups and cannot silently roll back an active account.

Recovery requires a verified transaction record from this updater. Older interrupted installations without that record need manual review; never delete the hold file to bypass recovery. Native macOS launchd behavior still requires local verification.

Paper trading only. Existing balances, positions, controls and history are preserved. This release addresses the three high-severity reliability findings; atomic execution journaling, redirect hardening and SEC document retries remain separate work.
