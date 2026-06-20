"""Time-of-day seasonality. Crypto may have UTC hours that reliably lean one
way. Honest method: DISCOVER which hours+direction make money on in-sample data
(pooled across the basket), lock that hour->side map, then test the SAME map
out-of-sample. If the map made money IS by luck it falls apart OOS.

1h bars (each bar = one hour). Enter at the hour's bar in the mapped direction,
ATR stop, hold `hold` bars (intraday), no fixed TP. Pooled across 10 coins.

Run: ../../venv/Scripts/python.exe tod.py [hold_bars]
"""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from btengine import Signal, simulate, Costs, RiskCfg, atr  # noqa: E402

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20,
               liq_buffer=2.5, compounding=True)
HOLD = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2


def load_1h(coin):
    df = pd.read_parquet(os.path.join(HERE, "data", f"{coin}_5m.parquet")).astype(float)
    return df.resample("1h").agg({"Open": "first", "High": "max", "Low": "min",
                                  "Close": "last", "Volume": "sum"}).dropna()


def tod_signals(df, hour_sides, sl_atr=1.5, hold=2):
    a = atr(df, 14).values
    hours = df.index.hour
    sigs = []
    for i in range(len(df)):
        h = int(hours[i])
        if h in hour_sides and not np.isnan(a[i]) and a[i] > 0:
            sigs.append(Signal(i=i, side=hour_sides[h], sl_dist=sl_atr * a[i],
                               tp_dist=0.0, entry_style="market", max_bars=hold))
    return sigs


def net_for(coins, dfs_slice, hour_sides):
    tot = 0.0; n = 0
    for c in coins:
        tr = simulate(dfs_slice[c], tod_signals(dfs_slice[c], hour_sides, hold=HOLD), LIGHTER, RISK, 60)
        tot += sum(t.pnl_usd for t in tr); n += len(tr)
    return tot, n


def main():
    coins = sorted(os.path.basename(p).split("_")[0]
                   for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet")))
    dfs = {c: load_1h(c) for c in coins}
    IS = {c: dfs[c].iloc[:int(len(dfs[c]) * 0.7)] for c in coins}
    OOS = {c: dfs[c].iloc[int(len(dfs[c]) * 0.7):] for c in coins}
    print(f"time-of-day, 1h, hold={HOLD}h, basket={coins}, zero-fee\n")

    # --- discover per-hour edge on IS (both directions), build the map ---
    print(f"{'hourUTC':>7} {'IS long$':>9} {'IS short$':>10} {'pick':>5} {'OOS$':>8}")
    hour_sides = {}
    for h in range(24):
        lnet, _ = net_for(coins, IS, {h: 1})
        snet, _ = net_for(coins, IS, {h: -1})
        best = 1 if lnet >= snet else -1
        bestnet = max(lnet, snet)
        pick = ""
        oos = ""
        if bestnet > 0:                      # this hour leaned profitably IS
            hour_sides[h] = best
            pick = "LONG" if best == 1 else "SHORT"
            onet, _ = net_for(coins, OOS, {h: best})
            oos = f"{onet:+.0f}"
        print(f"{h:>7} {lnet:>+9.0f} {snet:>+10.0f} {pick:>5} {oos:>8}")

    # --- the locked map, IS vs OOS ---
    is_net, is_n = net_for(coins, IS, hour_sides)
    oos_net, oos_n = net_for(coins, OOS, hour_sides)
    print(f"\nLocked map ({len(hour_sides)} hours chosen on IS): "
          f"{ {h: ('L' if s>0 else 'S') for h,s in sorted(hour_sides.items())} }")
    print(f"  IS  net {is_net:+.0f} over {is_n} trades")
    print(f"  OOS net {oos_net:+.0f} over {oos_n} trades")
    print("\nread: OOS net POSITIVE = the time-of-day edge held on unseen data. "
          "OOS negative while IS positive = the hours were noise / regime, no edge.")


if __name__ == "__main__":
    main()
