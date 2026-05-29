"""Grid sweep of ATR scale-out exits over the REAL Pro V3 signals (ZEC + SOL).

Dumps the raw results table (no filtering / no nudging — per the sweep protocol).
Dual fee profiles (BloFin 0.06%/side, Lighter 0%). Chronological 70/30 walk-forward.

Usage:
  python sweep_real.py                 # both symbols, full grid
  python sweep_real.py --symbol ZEC
"""
from __future__ import annotations

import argparse
import itertools
from dataclasses import replace
from pathlib import Path

import pandas as pd

from replay import ExitParams, load_prices, run_replay, SIGNALS_CSV
from strategy import kpis  # via replay's sys.path insert

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
RUNS.mkdir(exist_ok=True)

# ---------- grid axes ----------
SL_ATR      = [1.0, 1.5, 2.0, 2.5]
TP_LADDERS  = [(0.75, 1.5, 3.0), (1.0, 2.0, 3.0), (1.5, 3.0, 5.0), (0.5, 1.0, 2.0)]
RATIOS      = [(0.5, 0.25, 0.25), (0.34, 0.33, 0.33), (0.6, 0.2, 0.2),
               (1.0, 0.0, 0.0), (0.5, 0.5, 0.0)]
BE_AFTER    = [True, False]
TIMINGS     = ["signal", "retest"]
FEES        = [("blofin", 0.0006), ("lighter", 0.0)]


def split_oos(tdf: pd.DataFrame, frac: float = 0.30):
    if tdf.empty:
        return tdf, tdf
    cut = int(len(tdf) * (1 - frac))
    return tdf.iloc[:cut], tdf.iloc[cut:]


def run_symbol(sym: str, signals: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    rows = []
    grid = list(itertools.product(SL_ATR, TP_LADDERS, RATIOS, BE_AFTER, TIMINGS))
    for sl, ladder, ratios, be, timing in grid:
        base = ExitParams(sl_atr=sl, tp1_atr=ladder[0], tp2_atr=ladder[1], tp3_atr=ladder[2],
                          r1=ratios[0], r2=ratios[1], r3=ratios[2], be_after_tp1=be)
        row = {
            "symbol": sym, "entry_timing": timing, "sl_atr": sl,
            "tp_ladder": str(ladder), "ratios": str(ratios), "be_after_tp1": be,
        }
        for fee_name, comm in FEES:
            p = replace(base, commission_pct=comm)
            tdf = run_replay(signals, prices, p, entry_timing=timing)
            full = kpis(tdf) if not tdf.empty else {"n": 0, "net_pnl": 0, "profit_factor": 0,
                                                    "max_dd": 0, "win_rate": 0}
            is_p, oos_p = split_oos(tdf)
            oos = kpis(oos_p) if not oos_p.empty else {"net_pnl": 0, "profit_factor": 0, "n": 0}
            pre = fee_name + "_"
            row[pre + "n"] = full["n"]
            row[pre + "net"] = full["net_pnl"]
            row[pre + "pf"] = full["profit_factor"]
            row[pre + "dd"] = full["max_dd"]
            row[pre + "wr"] = full["win_rate"]
            row[pre + "oos_net"] = oos["net_pnl"]
            row[pre + "oos_pf"] = oos["profit_factor"]
            if fee_name == "lighter" and not tdf.empty:
                row["tp1_rate"] = round(tdf["reached_tp1"].mean(), 3)
                row["tp3_rate"] = round(tdf["reached_tp3"].mean(), 3)
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", choices=["ZEC", "SOL", "both"], default="both")
    args = ap.parse_args()
    syms = {"ZEC": ["ZEC-USDT"], "SOL": ["SOL-USDT"], "both": ["ZEC-USDT", "SOL-USDT"]}[args.symbol]

    allsig = pd.read_csv(SIGNALS_CSV)
    out_all = []
    for sym in syms:
        print(f"\n=== {sym}: loading prices ...")
        px = load_prices(sym)
        s = allsig[allsig["symbol"] == sym]
        print(f"    {len(s)} signals; grid = {len(SL_ATR)*len(TP_LADDERS)*len(RATIOS)*len(BE_AFTER)*len(TIMINGS)} configs")
        df = run_symbol(sym, s, px)
        df.to_csv(RUNS / f"{sym.replace('-USDT','')}_real_full.csv", index=False)
        out_all.append(df)

    full = pd.concat(out_all, ignore_index=True)
    full.to_csv(RUNS / "ALL_real_full.csv", index=False)

    cols = ["symbol", "entry_timing", "sl_atr", "tp_ladder", "ratios", "be_after_tp1",
            "lighter_n", "lighter_net", "lighter_pf", "lighter_dd", "lighter_wr",
            "lighter_oos_net", "blofin_net", "blofin_pf", "tp1_rate"]
    cols = [c for c in cols if c in full.columns]
    for sym in syms:
        sub = full[full["symbol"] == sym].sort_values("lighter_net", ascending=False)
        print(f"\n{'='*100}\nTOP 15 by Lighter net — {sym}\n{'='*100}")
        print(sub[cols].head(15).to_string(index=False))
        print(f"\nBaseline (sl1.5, tp(1,2,3), 50/25/25, BE, signal) — {sym}:")
        bl = full[(full.symbol == sym) & (full.sl_atr == 1.5) & (full.tp_ladder == "(1.0, 2.0, 3.0)")
                  & (full.ratios == "(0.5, 0.25, 0.25)") & (full.be_after_tp1 == True)
                  & (full.entry_timing == "signal")]
        if not bl.empty:
            print(bl[cols].to_string(index=False))
    print(f"\nWrote {RUNS / 'ALL_real_full.csv'}")


if __name__ == "__main__":
    main()
