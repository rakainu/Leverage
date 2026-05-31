"""STAGE 1 — broad triage. Every family x coin x timeframe, side='both', one
sensible default param set, Lighter zero-fee costs. Goal: find which combos have
BOTH frequency (trades/wk) AND a positive raw edge (avgR>0 / PF>1) worth tuning.

Writes runs/stage1.json and prints a ranked table. Fast: ~1 backtest per combo.
"""
from __future__ import annotations
import os, sys, json, time, traceback
import common as K

OUT = os.path.join(os.path.dirname(__file__), "runs")
os.makedirs(OUT, exist_ok=True)

FAMILIES = list(K.SL.REGISTRY.keys())


def main():
    rows = []
    t0 = time.time()
    print(K.HDR, flush=True)
    for tf in K.TFS:
        for coin in K.COINS:
            try:
                df = K.load(coin, tf)
            except Exception as e:
                print(f"  [skip load {coin} {tf}] {e}", flush=True)
                continue
            for fam in FAMILIES:
                params = K.DEFAULTS[fam]
                try:
                    m, _ = K.run(fam, df, tf, "both", params)
                except Exception:
                    print(f"  [err {fam} {coin} {tf}]", flush=True)
                    traceback.print_exc()
                    continue
                rec = dict(family=fam, coin=coin, tf=tf, side="both",
                           n=m["n"], trades_per_wk=m["trades_per_wk"],
                           pf=(None if m["profit_factor"] == float("inf") else m["profit_factor"]),
                           wr=m["win_rate"], avg_r=m["avg_r"], net_pct=m["net_pct"],
                           max_dd=m["max_dd_pct"], hold_min=m["avg_hold_min"],
                           weeks=m["weeks"])
                rows.append(rec)
                if m["n"] >= 1:
                    print(K.row(fam, coin, tf, "both", m), flush=True)

    with open(os.path.join(OUT, "stage1.json"), "w") as f:
        json.dump(rows, f, indent=2)

    # ---- ranked shortlist: frequency AND edge ----
    print("\n\n===== SHORTLIST (trades/wk>=5 AND avg_r>0) sorted by avg_r =====", flush=True)
    print(K.HDR, flush=True)
    short = [r for r in rows if r["trades_per_wk"] >= 5 and r["avg_r"] > 0]
    short.sort(key=lambda r: r["avg_r"], reverse=True)
    for r in short[:60]:
        pf = r["pf"]; pfs = "inf" if pf is None else f"{pf:.2f}"
        print(f"{r['family']:17}{r['coin']:5}{r['tf']:4}{'both':5}{r['n']:>6}"
              f"{r['trades_per_wk']:>7.1f}{pfs:>7}{r['wr']:>6.0f}{r['avg_r']:>+8.3f}"
              f"{r['net_pct']:>+8.1f}{r['max_dd']:>7.1f}{r['hold_min']:>8.0f}", flush=True)
    print(f"\n{len(rows)} combos tested, {len(short)} in shortlist, {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
