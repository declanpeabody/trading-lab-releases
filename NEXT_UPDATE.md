# Release 2026.10.14.6

Declan authorized implementation and publication of the three high-severity reliability fixes on 14 October 2026.

Implemented: isolated strategy experiments with a separate validated active setting; optional news startup fallback; visible update holds, durable transaction phases and integrity-checked local recovery (Mac setup option 11).

Verification includes storage corruption, continued held-stock exits, full service startup with damaged optional stores, explicit held-worker status, altered/incomplete backup rejection, interrupted recovery retry and a subprocess killed during update replacement.

Not included: atomic execution journal, market-data redirect hardening, SEC document retries or strategy changes. No real-money execution.
