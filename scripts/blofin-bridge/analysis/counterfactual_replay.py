"""Counterfactual replay for the proposed min_5m_slope_pct = 0.03 fill gate.

Reads /tmp/trade_context.csv (produced by trade_context_audit.py) and lists,
for each historical trade, whether the proposed gate would have blocked the
fill. Confirms the headline numbers from the threshold-sensitivity table.
"""
from __future__ import annotations

import csv

THRESHOLD = 0.03  # %

with open("/tmp/trade_context.csv") as fh:
    rows = list(csv.DictReader(fh))


def signed_slope(r: dict) -> float:
    s = float(r["slope5m_pct_3bar"])
    return s if r["side"] == "long" else -s


blocked = []
kept = []
for r in rows:
    s = signed_slope(r)
    if abs(s) < THRESHOLD:
        blocked.append(r)
    else:
        kept.append(r)

print(f"Threshold: |signed 5m EMA(9) slope-over-3-bars| < {THRESHOLD}%\n")

print("=== BLOCKED TRADES (would NOT fill under proposed gate) ===")
print(f"{'id':>4} {'sym':8s} {'side':5s} {'slope%':>9s} "
      f"{'pnl':>7s} {'win':>4s} {'exit':<10s} {'dur_min':>8s}")
for r in sorted(blocked, key=lambda x: int(x["id"])):
    pnl = float(r["pnl"])
    print(f"{r['id']:>4} {r['symbol']:8s} {r['side']:5s} "
          f"{float(r['slope5m_pct_3bar']):+9.4f} "
          f"{pnl:+7.2f} "
          f"{'Y' if pnl > 0 else 'N':>4s} "
          f"{r['exit_reason']:<10s} "
          f"{float(r['duration_min']):>8.1f}")

n_blocked = len(blocked)
wins_blocked = sum(1 for r in blocked if float(r["pnl"]) > 0)
losses_blocked = sum(1 for r in blocked if float(r["pnl"]) < 0)
pnl_blocked = sum(float(r["pnl"]) for r in blocked)

n_kept = len(kept)
wins_kept = sum(1 for r in kept if float(r["pnl"]) > 0)
pnl_kept = sum(float(r["pnl"]) for r in kept)
wr_kept = (wins_kept / n_kept * 100) if n_kept else 0

orig_n = len(rows)
orig_wins = sum(1 for r in rows if float(r["pnl"]) > 0)
orig_pnl = sum(float(r["pnl"]) for r in rows)
orig_wr = orig_wins / orig_n * 100

print()
print("=== HEADLINE CONFIRMATION ===")
print(f"  trades blocked:       {n_blocked}        (target: 14)")
print(f"  winning trades blocked: {wins_blocked}        (target: 0)")
print(f"  losing trades blocked:  {losses_blocked}")
print(f"  blocked P&L:          ${pnl_blocked:+.2f}  (target: ~-$197)")
print()
print(f"  baseline:  {orig_n} trades, {orig_wins} wins ({orig_wr:.1f}% WR), ${orig_pnl:+.2f}")
print(f"  with gate: {n_kept} trades, {wins_kept} wins ({wr_kept:.1f}% WR), ${pnl_kept:+.2f}")
print(f"  delta:     P&L improvement = ${pnl_kept - orig_pnl:+.2f}, "
      f"WR delta = {wr_kept - orig_wr:+.1f}pp")
