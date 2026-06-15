"""Append the sizing (compound 3x) + withdrawal (weekly skim) block to the live
bridge config, idempotently. Validates by loading the result."""
import sys

P = "/docker/scalper-paper/config.scalper.yaml"
s = open(P, encoding="utf-8").read()

BLOCK = """
# Position sizing (2026-06-14). compound = margin scales with live account equity
# (base_equity -> base margin per coin), capped at base x cap_mult and floored at 0
# on drawdown. See src/lighter_bridge/sizing.py (unit-tested, paper == live).
sizing:
  mode: compound
  base_equity: 3600      # equity at which a coin trades its base $500 margin
  cap_mult: 3.0          # margin/notional never exceeds 3x base ($1,500 margin / $15k notl)

# Weekly profit withdrawal. Skims REALIZED equity above base_equity x target_mult
# ($10,800), once per ISO week, to the withdrawals ledger + Telegram + dashboard.
# Never touches unrealized P&L. REVERT = set enabled:false.
withdrawal:
  enabled: true
  cadence: weekly
  target_mult: 3.0
"""

if "sizing:" in s or "withdrawal:" in s:
    print("sizing/withdrawal block already present; skipping")
else:
    s = s.rstrip() + "\n" + BLOCK
    open(P, "w", encoding="utf-8").write(s)

# validate via the bridge's own loader
sys.path.insert(0, "/docker/scalper-paper/src")
from lighter_bridge.config import load_config  # noqa: E402
c = load_config(P)
print("sizing:", c.sizing.mode, c.sizing.base_equity, c.sizing.cap_mult)
print("withdrawal:", c.withdrawal.enabled, c.withdrawal.cadence, c.withdrawal.target_mult)
print("symbols:", list(c.symbols.keys()), "collateral", c.initial_collateral_usdc)
print("cooldown:", c.cooldown.enabled, c.cooldown.consec_losses, c.cooldown.minutes)
