2026.10.20.3 — Daily learning and controlled strategy candidates

- Adds Daily Learning: one local review per weekday after 18:00 New York while the app is running. Reviews persist across restarts; missed review days are not reconstructed.
- Reviews matured 120-hour signal outcomes, detailed closed trades, modelled costs and hypothetical missed BUY observations. Pending and missing outcomes remain distinct from losses.
- Adds descriptive source/model scorecards. News remains context, not a directional prediction; scores do not establish predictive skill.
- Compares the existing 20/50 and 10/30 trend models. Selection freezes a candidate and requires later, previously unseen observations before proposing review. Insufficient evidence explicitly produces no change.
- Writes versioned candidate Python source from two validated templates. No unrestricted code rewriting, dynamic execution of generated code or automatic deployment. Active account, risk controls and request budgets remain unchanged.
- No new market-data calls or paid model subscriptions. Source journal remains capped at 10,000 records; daily reviews retain 366 dates.
- Install through Software Updates. Daily Learning starts automatically; no comparison reset is needed. Keep the app running for scheduled reviews.

Validation: full Python regression, authenticated read-only endpoint, forward-data separation, candidate integrity and failure isolation; JavaScript and desktop/mobile browser checks.
