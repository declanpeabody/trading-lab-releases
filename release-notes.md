# 2026.10.13.3 — Correct request-error diagnostics

- Fixes a misleading error: Python/urllib can reject an invalid request header
  before receiving any HTTP response. Such errors are no longer reported as a
  provider response containing invalid JSON.
- Checks the saved API key for internal spaces, line breaks, control characters
  and non-ASCII characters before spending API allowance or sending a request.
  Surrounding clipboard whitespace is trimmed. The key is never displayed.
- The dashboard performs the same local key validation on refresh, including
  outside market hours, without making data requests. An invalid key now gives
  an explicit instruction to copy only the key and replace it using Mac setup.
- Updated Mac setup validates newly entered keys before saving. JSON parsing
  errors now explicitly say that an HTTP response body was actually received.
- Keeps existing authentication, TLS, quota and connection diagnostics and the
  shared cooldown. Raw exception messages and provider bodies remain private.

This corrects diagnosis; it does not establish that a key is authorised or that
an underlying network/provider problem has been repaired. Paper account, limits,
free plan, update settings and retained history are unchanged.
