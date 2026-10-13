# 2026.10.13.1 — Market-data diagnostics and outage protection

- Replaces the generic connection error with safe diagnostics for TLS certificates,
  TLS failures, DNS, timeouts, network failures, HTTP/API authentication (401),
  access/entitlement (403), quota/rate limits (429), server errors and invalid JSON.
- Stops the scanner on the first shared data failure. A shared 15-minute request
  cooldown also protects against repeated requests from research in the same process.
  Cooldown skips consume no local API allowance. There is no immediate retry loop.
- Failed shared-connection histories stay pending for a later scan, not marked
  unusable all day. Old generic failed-history markers are released during startup,
  preserving valid history, paper account data and the existing request counters.
- The real reason reaches dashboard status, scanner notes and the decision journal.
  Decisions completed before a later history-download failure remain recorded.
- Individual stock/request errors still skip that stock; credentials, exception
  details, URLs and raw server response bodies are never displayed in diagnostics.

TLS verification remains enabled. This release diagnoses the underlying Mac/data
connection problem; it does not claim to repair an unknown provider or certificate
issue. Install, allow the next regular-hours scan, then review its exact message.
Paper rules, funding, position limits and API allowance are unchanged.
