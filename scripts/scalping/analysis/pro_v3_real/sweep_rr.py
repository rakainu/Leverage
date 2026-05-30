"""Single-TP + single-SL reward:risk sweep over the REAL Pro V3 SOL signals.

Rich's ask (2026-05-30): drop the scale-out ladder. ONE take-profit + ONE stop line.
Test reward:risk ratios 1:1, 1.5:1, 2:1, 2.5:1, 3:1 — does a wider TP beat the dead 1:1?

Config: all-out-at-TP1 (r1=1, r2=r3=0), so BE-after-TP1 is irrelevant (single exit).
  SL  = sl_atr * ATR(14) at entry
  TP  = rr * sl_atr * ATR(14)  (TP distance = rr x SL distance)
EMA9-retest entry (matches the live bridge). Lighter zero-fee + BloFin reference.
Chronological 70/30 walk-forward for OOS.

Usage: python sweep_rr.py
"""
from __future__ import annotations

import itertools
from dataclasses import replace

import pandas as pd

from replay import ExitParams, load_prices, run_replay, SIGNALS_CSV
from strategy import kpis  # via replay's sys.path insert

RR     = [1.0, 1.5, 2.0, 2.5, 3.0]
SL_ATR = [2.5, 3.0, 3.5, 4.0]
FEES   = [("lighter", 0.0), ("blofin", 0.0006)]
TIMINGS = ["retest", "signal"]


def split_oos(tdf: pd.DataFrame, frac: float = 0.30):
    if tdf.empty:
        return tdf, tdf
    cut = int(len(tdf) * (1 - frac))
    return tdf.iloc[:cut], tdf.iloc[cut:]


def run(sym: str, signals: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for timing, sl, rr in itertools.product(TIMINGS, SL_ATR, RR):
        base = ExitParams(sl_atr=sl, tp1_atr=rr * sl, tp2_atr=rr * sl, tp3_atr=rr * sl,
                          r1=1.0, r2=0.0, r3=0.0, be_after_tp1=False)
        row = {"symbol": sym, "timing": timing, "sl_atr": sl, "rr": rr}
        for fee_name, comm in FEES:
            p = replace(base, commission_pct=comm)
            tdf = run_replay(signals, prices, p, entry_timing=timing)
            k = kpis(tdf) if not tdf.empty else {"n": 0, "net_pnl": 0, "profit_factor": 0,
                                                 "max_dd": 0, "win_rate": 0}
            is_p, oos_p = split_oos(tdf)
            oos = kpis(oos_p) if not oos_p.empty else {"net_pnl": 0, "profit_factor": 0}
            pre = fee_name + "_"
            row[pre + "n"] = k["n"]
            row[pre + "net"] = round(k["net_pnl"], 0)
            row[pre + "pf"] = round(k["profit_factor"], 2)
            row[pre + "wr"] = round(k["win_rate"], 3)
            row[pre + "dd"] = round(k["max_dd"], 0)
            row[pre + "avg"] = round(k["net_pnl"] / k["n"], 2) if k["n"] else 0
            row[pre + "oos_net"] = round(oos["net_pnl"], 0)
            row[pre + "oos_pf"] = round(oos["profit_factor"], 2)
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    allsig = pd.read_csv(SIGNALS_CSV)
    sym = "SOL-USDT"
    px = load_prices(sym)
    s = allsig[allsig["symbol"] == sym]
    df = run(sym, s, px)
    df.to_csv("runs/SOL_rr_sweep.csv", index=False)

    for timing in TIMINGS:
        sub = df[df.timing == timing].copy()
        print(f"\n{'='*92}\nSOL — {timing} entry — single TP + single SL, all-out (Lighter zero-fee)\n{'='*92}")
        cols = ["sl_atr", "rr", "lighter_n", "lighter_wr", "lighter_net",
                "lighter_avg", "lighter_pf", "lighter_dd", "lighter_oos_net", "lighter_oos_pf"]
        print(sub[cols].to_string(index=False))
    print("\nWrote runs/SOL_rr_sweep.csv (BloFin columns included in CSV)")


if __name__ == "__main__":
    main()
