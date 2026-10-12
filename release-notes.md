# 2026.10.12.3 — Dashboard tabs and US market display

- Eight top navigation links: Overview, Positions, Scanner, Markets,
  Research & Journal, News, Rules & Limits, and Software Updates.
- Dedicated account panels, persistent bot status, and clearer empty-position text.
- TradingView market display with five index selections: DJIA, S&P 500,
  Nasdaq Composite, Nasdaq 100, and Russell 2000. Quotes may be delayed or
  unavailable under the widget provider's exchange permissions.
- Explicit ETF tracker alternative (DIA, SPY, ONEQ, QQQ, IWM): fund share prices,
  not index levels. CNBC index links remain available in Markets.
- Market display uses no Twelve Data credits and never supplies trading signals.
  External content runs in a separate cross-origin frame; account scripts remain
  protected by the existing nonce and same-origin connection policy.
- Hide/reload controls and horizontal scrolling for narrow screens.

The external feed needs internet access in your browser. No real-time entitlement
is promised. Automated navigation/control checks passed; browser visual rendering
and live quote availability still need checking on the Mac.
