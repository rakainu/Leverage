"""Drill into the flat-slope bucket: exit reasons and durations."""
import csv
from collections import Counter, defaultdict

with open("/tmp/trade_context.csv") as fh:
    rows = list(csv.DictReader(fh))

def signed_slope(r):
    s = float(r["slope5m_pct_3bar"])
    return s if r["side"] == "long" else -s

flat = [r for r in rows if -0.03 <= signed_slope(r) < 0.03]
print(f"Flat-slope bucket: {len(flat)} trades")

print("\nExit reasons:")
exits = Counter(r["exit_reason"] for r in flat)
for k, v in exits.most_common():
    pnl = sum(float(r["pnl"]) for r in flat if r["exit_reason"] == k)
    durs = [float(r["duration_min"]) for r in flat if r["exit_reason"] == k]
    avg_dur = sum(durs)/len(durs) if durs else 0
    print(f"  {k:10s} n={v:2d} pnl={pnl:8.2f} avg_dur_min={avg_dur:6.1f}")

print("\nFlat trades, sorted by P&L:")
print(f"{'id':>4} {'sym':8s} {'side':5s} {'slope%':>8s} {'pnl':>7s} {'dur_min':>8s} {'exit':<10s} {'atr%':>6s} {'chop':>5s}")
for r in sorted(flat, key=lambda x: float(x["pnl"])):
    print(f"{r['id']:>4} {r['symbol']:8s} {r['side']:5s} "
          f"{float(r['slope5m_pct_3bar']):+8.4f} "
          f"{float(r['pnl']):+7.2f} "
          f"{float(r['duration_min']):>8.1f} "
          f"{r['exit_reason']:<10s} "
          f"{float(r['atr_pct']):>6.3f} "
          f"{float(r['chop_ratio_10v50']):>5.2f}")

print("\n=== Threshold sensitivity: how many trades blocked vs P&L impact? ===")
print(f"{'threshold':>10s} {'blocked':>8s} {'blocked_wins':>12s} {'blocked_pnl':>12s} "
      f"{'kept':>5s} {'kept_wins':>10s} {'kept_pnl':>10s} {'kept_wr%':>9s}")
for thr in [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10]:
    blocked = [r for r in rows if abs(signed_slope(r)) < thr]
    kept = [r for r in rows if abs(signed_slope(r)) >= thr]
    bw = sum(1 for r in blocked if float(r["pnl"]) > 0)
    bp = sum(float(r["pnl"]) for r in blocked)
    kw = sum(1 for r in kept if float(r["pnl"]) > 0)
    kp = sum(float(r["pnl"]) for r in kept)
    wr = (kw / len(kept) * 100) if kept else 0
    print(f"{thr:>10.3f} {len(blocked):>8d} {bw:>12d} {bp:>12.2f} "
          f"{len(kept):>5d} {kw:>10d} {kp:>10.2f} {wr:>9.1f}")
