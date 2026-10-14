# 2026.10.14.1 — Held-quote timing and validation diagnostics

Fetch the existing candidate quote batch before active holdings, with USD/GBP
last. This reduces the wait between receiving held-stock quotes and validating
all prices at the decision timestamp. The same request caps, priority credits,
120-second maximum age and 5-second future tolerance remain enforced. No extra
requests, cached-price substitutions, or immediate retries are introduced.

Held-quote errors now state the validation reason: stale/future timestamp,
missing/invalid timestamp or price, currency/symbol/exchange mismatch, closed
market or extended hours. Age failures show derived seconds; failed validations
also report seconds since receipt. Quote failures appear in scanner notes, and
FX failures identify USD/GBP. Provider bodies and unvalidated field values are
not included. Invalid holdings or FX still block the complete account cycle.

This addresses a demonstrated collection-order risk, not a confirmed diagnosis
of the user's MSFT response. The next check will identify any remaining provider
or timing issue. The update preserves holdings, paper funds and experiment rules.
