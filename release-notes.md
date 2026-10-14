# 2026.10.14.4 — Optional private read-only monitoring

Adds three read-only MCP tools for saved account status, recent decisions and
recorded BUY/SELL decisions. Overview now includes a Private monitoring switch;
sharing is disabled by default and can be revoked locally at any time.

The connector reads existing databases without changing the paper account or
requesting market data. It excludes configuration and redacts known current
secrets. Reports show the last successful check; prices may be stale and saved
state does not prove that the trading worker is running.

A separately configured private tunnel client on the iMac is required. Installing
this release does not establish a connection or enable automatic reporting.
End-to-end ChatGPT tunnel verification remains pending local setup.
