# 2026.10.13.4 — Controlled forward strategy experiments

Adds Strategy Experiments as a top navigation link and research panel. Following
approval, separate paper experiment accounts and automatic promotion are enabled
on first use. Each can be disabled in the panel; the active setting starts at £200.

The first bounded rule changes compare £100, £150 and £200 purchases. Ranking,
signals, costs, £1,000 initial experimental capital, five positions, £200 safety
cap, 5% observed-price exit and 10%/3% buying blocks stay fixed. No arbitrary code
is generated. Real-money execution remains unavailable. Existing active funding,
holdings, account history, user controls and risk flags are never reset.

- Three separate accounts receive the same fresh validated quote opportunities.
  All experiment books commit together or all remain unchanged. Restart recovery
  preserves books and evaluation state; repeated snapshots cannot add evidence.
- Each phase needs 30 completed qualifying observed sessions. A qualifying day
  has at least three complete observations spanning one hour. Development picks
  one eligible challenger, then separate reset accounts evaluate it on later data.
  Forward-test results cannot pick a different challenger retrospectively.
- Both phases need 95% complete observed checks, ten closed trades per compared
  model, at least one percentage point higher after-cost return than incumbent
  and fixed £200 reference, positive return and no greater observed drawdown.
  A £200 challenger compares against the incumbent rather than itself. Portfolio
  marks include modelled liquidation costs; intraday observed peaks are retained.
- Passing evidence is rechecked before promotion. Automatic promotion waits for
  permission, an unpaused and flat active paper account, no buying/risk blocks and
  no account error. It changes only the purchase setting for later entries.
  Pending evidence expires after seven calendar days. Previous setting and a
  bounded promotion log are retained; Restore previous setting disables automatic
  promotion and preserves existing holdings and risk flags.
- Completed rounds restart on a later observed day. Corporate-action uncertainty,
  excessive observer holdings or insufficient coverage over 120 observed sessions
  requires review and manual evaluation restart. No resulting incomplete round
  can promote a strategy.
- Experiment holdings share the six normal candidate quote slots. They use no
  reserved held-position allowance and never increase the 500/700 limits. Entry
  coverage may be reduced. More than six additional observer holdings stops the
  evaluation and returns those slots to normal screening. Existing active held
  positions keep priority. Missing experiment data holds all comparison books.
- Authenticated, CSRF-protected controls pause/resume experiments, toggle automatic
  promotion, restore the previous setting and restart an evaluation. JSON export
  includes accounts, metrics, up to six phase reports and 100 promotion/control
  events. Each model retains its latest 200 account events per phase.

These are predeclared research screens, not proof of profitability or machine
learning training. Observed quotes miss unobserved intraday lows; downtime and
partial coverage limit the evidence. Daily synthetic backtests, news and other
bots' trades cannot qualify promotions. Data connectivity must work first. A
successful candidate needs at least 60 qualifying sessions, enough closed trades
and a flat unblocked account; no promotion is guaranteed.
