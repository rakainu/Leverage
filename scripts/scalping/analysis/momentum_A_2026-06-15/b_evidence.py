"""B evidence package — show the real numbers, not a hand-wave.

1. Edge metrics (PF/WR/avgR/freq) full window + IS/OOS + June-forward + 2x-slip
2. Per-coin breakdown (which coins carry it)
3. REALISTIC $3k @2% curve with a notional cap reflecting Lighter depth — the honest
   projection (the first double is cap-independent and real; only far-out compounding
   is fantasy)
4. A sample of actual trades (real prices / pnl) to eyeball
"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
from common import available_coins, load, portfolio, weeks_span, LIGHTER, LIGHTER_2X, bt, TF_MIN
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scalp_search_2026-05-30")))
from strat_lib import regime_mr  # noqa: E402

CFG = dict(side="both", trend_len=200, slope_lb=20, z_period=30, z_entry=1.5,
           sl_atr=2.0, tp_frac=0.3, max_bars=12, limit_atr=0.25)
BASKET = ["SOL", "ETH", "ZEC", "HYPE", "BTC"]   # validated 5
START = 3000.0


def trades_for(dfs, costs):
    return {c: bt.simulate(df, regime_mr(df, **CFG), costs, risk=bt.RiskCfg(
        starting_equity=1000.0, risk_frac=0.01, max_leverage=10, liq_buffer=2.5,
        compounding=True), tf_minutes=TF_MIN) for c, df in dfs.items()}


def realistic_curve(per, start, risk_frac, notional_cap):
    """Single shared account, compounding, sizing each trade by risk%*equity but
    capping NOTIONAL at notional_cap (Lighter depth). stop_frac recovered from each
    trade (risk_usd/notional). Returns (curve df, final, maxDD%, weeks_to_2x)."""
    rows = []
    for c, ts in per.items():
        for t in ts:
            stop_frac = t.risk_usd / t.notional if t.notional > 0 else 0.0
            rows.append((t.exit_time, t.r_multiple, stop_frac))
    rows.sort(key=lambda x: x[0])
    eq = start; times = []; eqs = []; first2x = None
    for tm, r, sf in rows:
        desired_risk = risk_frac * eq
        notional = desired_risk / sf if sf > 0 else 0.0
        if notional > notional_cap:
            actual_risk = notional_cap * sf
        else:
            actual_risk = desired_risk
        eq += r * actual_risk
        times.append(tm); eqs.append(eq)
        if first2x is None and eq >= 2 * start:
            first2x = tm
    eqs = np.array(eqs)
    peak = np.maximum.accumulate(np.concatenate([[start], eqs]))
    dd = ((peak - np.concatenate([[start], eqs])) / peak).max() * 100
    if first2x is not None:
        wk2x = (first2x - times[0]).days / 7.0
    else:
        wk2x = None
    return pd.Series(eqs, index=pd.to_datetime(times)), eq, dd, wk2x


def edge_line(tag, m):
    if m is None:
        print(f"  {tag:18} (none)"); return
    pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    print(f"  {tag:18} n={m['n']:>5} PF={pf:>5} WR={m['wr']:3.0f}% avgR={m['avg_r']:+.4f} t={m['t']:+.1f}")


def main():
    full = {c: load(c) for c in BASKET}
    wk = weeks_span(full)
    print(f"# APPROACH B EVIDENCE | regime_mr | {BASKET} | 15m Lighter 0-fee | ~{wk:.0f}wk\n")
    print(f"# config: {CFG}\n")

    per = trades_for(full, LIGHTER)
    print("=== 1. EDGE METRICS (size-independent — these are the real edge) ===")
    edge_line("full window", portfolio(per))
    # IS/OOS
    isd = {c: bt.split_is_oos(full[c], 0.70)[0] for c in BASKET}
    oosd = {c: bt.split_is_oos(full[c], 0.70)[1] for c in BASKET}
    edge_line("IS 70%", portfolio(trades_for(isd, LIGHTER)))
    edge_line("OOS 30%", portfolio(trades_for(oosd, LIGHTER)))
    # June-forward
    JUN = pd.Timestamp("2026-06-01", tz="UTC")
    jun = {c: full[c][full[c].index >= JUN] for c in BASKET}
    edge_line("June-forward", portfolio(trades_for(jun, LIGHTER)))
    edge_line("2x slippage", portfolio(trades_for(full, LIGHTER_2X)))
    m = portfolio(per)
    print(f"  -> ~{m['n']/wk:.0f} trades/week across the basket\n")

    print("=== 2. PER-COIN (which coins carry it) ===")
    for c in BASKET:
        ts = per[c]; n = len(ts)
        netR = sum(t.r_multiple for t in ts)
        wr = sum(1 for t in ts if t.pnl_usd > 0) / n * 100 if n else 0
        print(f"  {c:5} n={n:>5} netR={netR:+7.1f} WR={wr:3.0f}%")

    print("\n=== 3. REALISTIC $3k @ 2% risk — notional capped to Lighter depth ===")
    print("  (first double is cap-independent & real; caps only bound far-out compounding)")
    for cap in (20000, 50000, 100000, 1e12):
        curve, final, dd, wk2x = realistic_curve(per, START, 0.02, cap)
        caps = "uncapped" if cap > 1e11 else f"${cap/1000:.0f}k"
        w2 = f"{wk2x:.1f}wk" if wk2x else "—"
        print(f"  notional cap {caps:>9}: first 2x in {w2:>7} | final ${final:>14,.0f} | maxDD {dd:.0f}%")

    print("\n=== 4. SAMPLE REAL TRADES (last 12, ZEC+SOL) ===")
    print(f"  {'coin':4} {'side':5} {'entry_time':16} {'entry':>9} {'exit':>9} {'reason':8} {'R':>6}")
    sample = []
    for c in ("ZEC", "SOL"):
        for t in per[c][-6:]:
            sample.append((c, t))
    sample.sort(key=lambda x: x[1].entry_time)
    for c, t in sample:
        sd = "long" if t.side > 0 else "short"
        print(f"  {c:4} {sd:5} {str(t.entry_time)[:16]:16} {t.entry_price:9.3f} "
              f"{t.exit_price:9.3f} {t.exit_reason:8} {t.r_multiple:+6.2f}")


if __name__ == "__main__":
    main()
