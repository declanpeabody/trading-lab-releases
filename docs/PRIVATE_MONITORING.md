# Private paper-bot monitoring — 2026.10.14.4

This batch adds three read-only MCP tools: `get_bot_status`,
`get_recent_decisions`, and `get_recent_trades`. The latter two accept an optional
New York trading date and limit (1–100). No tool can trade, change settings,
request new quotes, select files, execute commands, or connect to arbitrary URLs.

The interface reads the existing SQLite databases using `mode=ro` and
`query_only`. It does not instantiate the trading ledger, run migrations or start
the trading worker. Configuration is consulted only to redact known current
secrets from legacy error strings; configuration, credentials, reconciliation
notes and arbitrary stored fields are not exported. Decision history is bounded
by the existing 90-day/250,000-observation retention.

## Controls and limitations

Sharing defaults to disabled. After this release is installed, enable
**Overview → Private monitoring → Enable read-only sharing** locally. Disabling
it rejects new tool data reads, including from an already running connector.
It cannot recall information already delivered to ChatGPT. The switch does not
create a connection or schedule reports. No live account data is stored in this
GitHub repository.

Every status result includes a generated timestamp and the last successful
account check. Generating a report does not refresh its prices. The interface
cannot prove that the trading worker is currently alive; it labels worker health
unknown. A check older than 20 minutes is marked as old, not automatically a
failure (closed markets and pausing can explain it). Trade tools report journal
BUY/SELL decisions, not a complete broker execution statement. GBP position
returns include recorded purchase costs and FX changes, excluding future exits.

## Connection prerequisites to verify before activation

OpenAI documents a private Secure MCP Tunnel route. It requires a tunnel,
appropriate organization/workspace permissions, a runtime credential and a
`tunnel-client` process on the Mac. It can forward to a local stdio command,
so this adapter does not open a port or expose the dashboard publicly.

In ChatGPT Plugins, check **+ → Add custom MCP server → Connection → Tunnel**.
Account/workspace availability and any costs remain unverified. Do not purchase
a plan or create credentials until access is confirmed. Configure tunnel access
only for the intended user/workspace; permitted tunnel callers can read anything
the three tools expose while sharing is enabled.

After release, the local command is the bot's installed Python executable,
followed by the installed `app.py` path and `--monitor-stdio`. Both executable and
script paths must be quoted when they contain spaces. Use the existing bot
installation, not an empty checkout. The default data path is next to `app.py`;
`DATA_DIR` is a local operator override, never a tool argument.

Follow current tunnel-client setup and `doctor` instructions from the official
guide; keep credentials in a secure local environment reference, never chat,
GitHub, screenshots or command arguments. Keep the Mac and tunnel client awake
for queries. Verify tool discovery, a real status read and disabling sharing
before calling the connection operational. Automatic alerts are a separate task.

Official setup references (checked 14 October 2026):
- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://developers.openai.com/api/docs/guides/custom-mcp-server

## Verification performed

Local tests cover disabled access and revocation, read-only SQL, unchanged
account state, no market calls, redaction, date/limit validation, missing storage,
protocol discovery/initialization, bounded request framing, and launching stdio
without starting the dashboard. HTTP tests cover the local CSRF-protected toggle.
No iMac or end-to-end ChatGPT tunnel test has occurred yet.

## Optional login startup (next draft update)

After installing this batch, open Mac setup and choose **9**. Stop any manually running tunnel first with Control-C. Provide the path to your official OpenAI tunnel-client, your existing tunnel ID, and the runtime key at the hidden prompt. The setup copies the client into the installed application directory and creates a separate per-user launch agent. It uses a separate `trading-bot-login` profile; the earlier manual profile is retained.

The key is stored at `private-monitor/runtime-key` under the installed application directory with owner-only file permissions and a private parent directory. This is **not encrypted Keychain storage**. The profile contains only a file reference; neither launch arguments nor the launch-agent plist contain the key. Keep backups private and revoke a compromised key with OpenAI.

Check `http://127.0.0.1:8080/ui` for Ready status, enable local read-only sharing, and verify a real status read. Registration alone does not verify network authentication. Keep the iMac awake; this is login startup, not operation during sleep. **Option 10** stops this launch agent, removes its plist and saved runtime key, and leaves the trading service unchanged. Disable sharing in the dashboard to revoke monitor reads immediately, including any separately running manual tunnel. After a bot update, the stdio process may need a tunnel restart to load the updated monitor code.

Automated tests check configuration and secret handling. Actual macOS launch-agent startup, reboot, sleep/wake and revocation still require verification on the iMac.
