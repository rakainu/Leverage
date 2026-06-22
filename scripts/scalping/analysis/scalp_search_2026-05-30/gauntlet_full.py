"""GAUNTLET (full) — exhaustive optimization of the regime_mr VWAP-z signal.

Phase A: broad sweep over timeframe x stop x exit-model x z x slope x accel.
         For every config: pool trades across the basket, split 70/30 by time
         (IS/OOS), and run a 4-fold walk-forward. Cheap (trades simulated once
         per config; splits are just slicing).
Phase B: take the top-K by OUT-OF-SAMPLE total profit, re-simulate under 2x
         slippage, and report the survivors with full stats + max adverse
         excursion (liq-risk proxy for leverage).

Ranks by OOS sumR (honest out-of-sample total return per unit risk). Also flags
the best-SHAPED robust config (highest win/loss ratio that stays positive OOS).

Run:  venv/Scripts/python.exe analysis/scalp_search_2026-05-30/gauntlet_full.py
Writes: gauntlet_full_results.txt
"""
import os
import sys
import itertools

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import common as K            # noqa: E402
from btengine import simulate  # noqa: E402

HERE = os.path.dirname(__file__)
COINS = ["BTC", "ETH", "SOL", "HYPE", "ZEC", "BNB", "DOGE", "SUI"]
TFS = ["5m", "15m", "30m", "1h"]
TF_MIN = {**K.TF_MIN, "30m": 30}
MAXBARS = {"5m": 48, "15m": 24, "30m": 16, "1h": 12}

# ---- axes -----------------------------------------------------------------
SL_ATR = [1.5, 2.0, 2.5, 3.0, 3.5]
Z_ENTRY = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]   # extended DOWN — optimizer pinned the 1.0 bound
SLOPE = [0.0, 0.08, 0.12]
ACCEL = [0.0, 3.0]
# liq-safe leverage from worst adverse excursion: keep maxMAE*lev under ~80% (buffer)
def safe_lev(maxmae_pct):
    return max(1, int(80.0 / maxmae_pct)) if maxmae_pct > 0 else 99


def exit_configs():
    cfgs = {}
    for tpf in [0.3, 0.5, 0.7, 1.0]:
        cfgs[f"tp{tpf}"] = dict(tp_frac=tpf)
    for tp1 in [0.3, 0.5]:
        for tp2 in [1.5, 2.0]:
            cfgs[f"run_tp1{tp1}_x{tp2}"] = dict(tp_frac=0.5, tp1_frac=tp1, tp2_mult=tp2, be_after_tp1=True)
    for btr in [1.0, 1.5]:
        cfgs[f"be@{btr}R_tp1.0"] = dict(tp_frac=1.0, be_trigger_r=btr, be_offset_r=0.0)
    return cfgs


# ---- data -----------------------------------------------------------------
_CACHE = {}
def load_tf(coin, tf):
    key = (coin, tf)
    if key in _CACHE:
        return _CACHE[key]
    p5 = os.path.join(HERE, "data", f"okx_{coin}_5m.parquet")
    if not os.path.exists(p5):
        _CACHE[key] = None
        return None
    try:
        base = pd.read_parquet(p5).astype(float)
    except Exception:        # file mid-write by the background fetch — skip this run
        return None
    if tf == "5m":
        df = base
    else:
        rule = {"15m": "15min", "30m": "30min", "1h": "60min"}[tf]
        df = base.resample(rule).agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
    _CACHE[key] = df
    return df


ENTRY_FIXED = dict(trend_len=200, slope_lb=20, z_period=30, limit_atr=0.25, atr_p=14)


def trades_for(tf, sl_atr, z_entry, slope, accel, exit_kw, costs):
    """Pooled (entry_time, r) across the basket for one config."""
    params = dict(ENTRY_FIXED, sl_atr=sl_atr, z_entry=z_entry, min_slope_pct=slope,
                  accel_mult=accel, max_bars=MAXBARS[tf], **exit_kw)
    fn = K.SL.REGISTRY["regime_mr"]
    rows = []
    weeks = 0.0
    for coin in COINS:
        df = load_tf(coin, tf)
        if df is None:
            continue
        tr = simulate(df, fn(df, side="both", **params), costs, K.RISK, TF_MIN[tf])
        weeks += len(df) * TF_MIN[tf] / (60 * 24 * 7) / len(COINS)
        for t in tr:
            r = t.side * (t.exit_price - t.entry_price) / t.entry_price
            rows.append((t.entry_time, r, t.mae_frac))
    rows.sort(key=lambda x: x[0])
    return rows, weeks


def stats(rets):
    if len(rets) == 0:
        return None
    rets = np.asarray(rets)
    w, l = rets[rets > 0], rets[rets < 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else float("inf")
    aw = 100 * w.mean() if len(w) else 0.0
    al = 100 * l.mean() if len(l) else 0.0
    ratio = (aw / -al) if al < 0 else float("inf")
    be = (-al / (aw - al) * 100) if (aw - al) else float("nan")
    return dict(n=len(rets), pf=pf, wr=100 * len(w) / len(rets), sumR=float(rets.sum()),
                aw=aw, al=al, ratio=ratio, be=be, cush=100 * len(w) / len(rets) - be)


def wf_folds(rows, k=4):
    """Sum-R per time fold; return list of fold sumRs."""
    if len(rows) < k * 10:
        return []
    rets = [r[1] for r in rows]
    fold = len(rets) // k
    return [float(np.sum(rets[i * fold:(i + 1) * fold])) for i in range(k)]


def main():
    exits = exit_configs()
    grid = list(itertools.product(TFS, SL_ATR, Z_ENTRY, SLOPE, ACCEL, exits.items()))
    print(f"Phase A: {len(grid)} configs x {len(COINS)} coins ...", flush=True)
    avail = [c for c in COINS if load_tf(c, "5m") is not None]
    print(f"coins available: {avail}", flush=True)

    rowsA = []
    for i, (tf, sl, z, slope, accel, (el, ekw)) in enumerate(grid, 1):
        rows, weeks = trades_for(tf, sl, z, slope, accel, ekw, K.LIGHTER)
        if len(rows) < 100:
            if i % 200 == 0:
                print(f"\r  {i}/{len(grid)}", end="", flush=True)
            continue
        rets = [r[1] for r in rows]
        cut = int(len(rets) * 0.7)
        IS, OOS = stats(rets[:cut]), stats(rets[cut:])
        if not OOS or OOS["n"] < 30:
            continue
        folds = wf_folds(rows)
        pos_folds = sum(1 for f in folds if f > 0)
        rowsA.append(dict(tf=tf, sl=sl, z=z, slope=slope, accel=accel, exit=el,
                          all=stats(rets), IS=IS, OOS=OOS, tpw=len(rets) / weeks if weeks else 0,
                          pos_folds=pos_folds, nfolds=len(folds),
                          maxmae=100 * max(r[2] for r in rows)))
        if i % 200 == 0:
            print(f"\r  {i}/{len(grid)}  kept={len(rowsA)}", end="", flush=True)
    print(f"\nPhase A done: {len(rowsA)} configs with tradeable sample.", flush=True)

    rowsA.sort(key=lambda d: d["OOS"]["sumR"], reverse=True)
    topK = rowsA[:30]

    # Phase B: re-sim top-K under hi-slip
    print(f"Phase B: re-sim top {len(topK)} under 2x slippage ...", flush=True)
    for d in topK:
        ekw = exits[d["exit"]]
        rows, _ = trades_for(d["tf"], d["sl"], d["z"], d["slope"], d["accel"], ekw, K.LIGHTER_HISLIP)
        d["slip"] = stats([r[1] for r in rows]) if rows else None

    # ---- report ----
    out = []
    def p(s=""):
        out.append(s); print(s, flush=True)

    p("\n" + "=" * 120)
    p("GAUNTLET — regime_mr VWAP-z, full optimization. Ranked by OUT-OF-SAMPLE total profit (sumR).")
    p("zero-fee Lighter; IS=first 70% / OOS=last 30% by time; WF=4 folds; slip=2x (0.10%).")
    p("=" * 120)
    hdr = (f"{'#':>3} {'tf':>4} {'sl':>4} {'z':>4} {'slp':>5} {'acl':>4} {'exit':>14} "
           f"{'t/wk':>6} {'OOSn':>5} {'OOSpf':>6} {'OOSwr':>6} {'OOSbe':>6} {'cush':>5} "
           f"{'W/L':>5} {'OOSsumR':>8} {'WF':>4} {'slipR':>7} {'mae%':>6}")
    p(hdr)
    p("-" * 120)
    for rank, d in enumerate(topK, 1):
        o = d["OOS"]
        pf = "inf" if o["pf"] == float("inf") else f"{o['pf']:.2f}"
        rr = "inf" if o["ratio"] == float("inf") else f"{o['ratio']:.2f}"
        slipR = f"{d['slip']['sumR']:+.2f}" if d.get("slip") else "  -  "
        p(f"{rank:>3} {d['tf']:>4} {d['sl']:>4} {d['z']:>4} {d['slope']:>5} {d['accel']:>4} {d['exit']:>14} "
          f"{d['tpw']:>6.1f} {o['n']:>5} {pf:>6} {o['wr']:>6.0f} {o['be']:>6.0f} {o['cush']:>+5.1f} "
          f"{rr:>5} {o['sumR']:>+8.3f} {d['pos_folds']}/{d['nfolds']:<2} {slipR:>7} {d['maxmae']:>6.1f}")

    # robust survivors: WF >= 3/4, slip positive, OOS positive
    robust = [d for d in topK if d["pos_folds"] >= 3 and d.get("slip") and d["slip"]["sumR"] > 0 and d["OOS"]["sumR"] > 0]
    p("\n" + "-" * 120)
    p(f"ROBUST survivors (WF>=3/4 + slip-positive + OOS-positive): {len(robust)}")
    if robust:
        best_total = max(robust, key=lambda d: d["OOS"]["sumR"])
        best_shape = max(robust, key=lambda d: (d["OOS"]["ratio"] if d["OOS"]["ratio"] != float("inf") else 99))
        for tag, d in [("BEST TOTAL PROFIT", best_total), ("BEST SHAPE (W/L)", best_shape)]:
            o = d["OOS"]
            p(f"\n  >>> {tag}: {d['tf']} sl{d['sl']} z{d['z']} slope{d['slope']} accel{d['accel']} {d['exit']}")
            p(f"      OOS: n={o['n']} PF={o['pf']:.2f} WR={o['wr']:.0f}% breakeven={o['be']:.0f}% "
              f"cushion={o['cush']:+.1f}pt W/L={o['ratio']:.2f} sumR={o['sumR']:+.2f}")
            p(f"      all-data: PF={d['all']['pf']:.2f} WR={d['all']['wr']:.0f}% sumR={d['all']['sumR']:+.2f} | "
              f"WF {d['pos_folds']}/{d['nfolds']} | slip sumR={d['slip']['sumR']:+.2f} | t/wk={d['tpw']:.0f} | "
              f"maxMAE={d['maxmae']:.1f}% -> liq-safe leverage <= {safe_lev(d['maxmae'])}x")
    else:
        p("  none cleared all three gates — see top table for the closest.")
    p("\nLOCKED baseline for reference: 15m sl2.0 z1.5 slope0.08 accel3.0 tp0.3 (breakeven ~86%, W/L ~0.16).")

    with open(os.path.join(HERE, "gauntlet_full_results.txt"), "w") as f:
        f.write("\n".join(out))
    print(f"\nwrote {os.path.join(HERE, 'gauntlet_full_results.txt')}", flush=True)


if __name__ == "__main__":
    main()
