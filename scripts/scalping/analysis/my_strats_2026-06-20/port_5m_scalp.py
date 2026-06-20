"""Faithful port of Rich's "5m Crypto Scalping Strategy" (TV Pine) to our honest
engine. Rules (exact):
  - trend: EMA(200). long only if close>EMA, short only if close<EMA.
  - entry: RSI(14) crossover 40 (long, in uptrend) / crossunder 60 (short, downtrend).
  - volume filter: volume > SMA(volume,20).
  - exit: stop = 1.5*ATR(14); take-profit = 2.0 * risk (2R). market entry (next open).
Tested on a few coins, 5m + 15m, IS/OOS, zero-fee (our Lighter target) and also
with the 0.05% fee the TV script assumes, for comparison.

Run: ../../venv/Scripts/python.exe port_5m_scalp.py
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from btengine import Signal, simulate, Costs, RiskCfg, atr, ema, sma, rsi  # noqa: E402

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
FEE05 = Costs(taker_pct=0.05, maker_pct=0.05, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20,
               liq_buffer=2.5, compounding=True)
COINS = ["HYPE", "BTC", "SOL", "ETH", "DOGE"]
TF_MIN = {"5m": 5, "15m": 15}


def load(coin, tf):
    df = pd.read_parquet(os.path.join(HERE, "data", f"{coin}_5m.parquet")).astype(float)
    if tf == "5m":
        return df
    return df.resample("15min").agg({"Open": "first", "High": "max", "Low": "min",
                                     "Close": "last", "Volume": "sum"}).dropna()


def signals(df, ema_len=200, rsi_len=14, rsi_long=40, rsi_short=60,
            atr_len=14, atr_mult=1.5, rr=2.0, vol_len=20, use_vol=True,
            tp_abs_atr=0.0):
    C = df["Close"]
    e = ema(C, ema_len).values
    r = rsi(C, rsi_len).values
    a = atr(df, atr_len).values
    vma = sma(df["Volume"], vol_len).values
    cv, vv = C.values, df["Volume"].values
    sigs = []
    for i in range(1, len(df)):
        if np.isnan(e[i]) or np.isnan(r[i]) or np.isnan(r[i - 1]) or np.isnan(a[i]) or a[i] <= 0:
            continue
        vol_ok = (not use_vol) or (not np.isnan(vma[i]) and vv[i] > vma[i])
        if not vol_ok:
            continue
        up, dn = cv[i] > e[i], cv[i] < e[i]
        x_up = r[i] > rsi_long and r[i - 1] <= rsi_long      # crossover 40
        x_dn = r[i] < rsi_short and r[i - 1] >= rsi_short     # crossunder 60
        side = 1 if (up and x_up) else (-1 if (dn and x_dn) else 0)
        if side == 0:
            continue
        sl = atr_mult * a[i]
        tp = tp_abs_atr * a[i] if tp_abs_atr > 0 else rr * sl
        sigs.append(Signal(i=i, side=side, sl_dist=sl, tp_dist=tp, entry_style="market",
                           max_bars=24))
    return sigs


def stat(trades):
    if not trades:
        return dict(n=0, wr=0, net=0.0, pf=0.0)
    w = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    l = [t.pnl_usd for t in trades if t.pnl_usd <= 0]
    pf = sum(w) / -sum(l) if sum(l) < 0 else float("inf")
    return dict(n=len(trades), wr=len(w) / len(trades) * 100, net=sum(w) + sum(l), pf=pf)


def run(coin, tf, lo, hi, costs, **kw):
    df = load(coin, tf); a, b = int(len(df) * lo), int(len(df) * hi)
    sub = df.iloc[a:b]
    return stat(simulate(sub, signals(sub, **kw), costs, RISK, TF_MIN[tf]))


def pooled(tf, lo, hi, costs, **kw):
    tot = 0.0; n = wins = 0; wsum = lsum = 0.0
    for c in COINS:
        m = run(c, tf, lo, hi, costs, **kw)
        tot += m["net"]; n += m["n"]
        wins += round(m["wr"] / 100 * m["n"])
    return tot, n, (wins / n * 100 if n else 0)


def exit_sweep():
    print("\n=== SALVAGE TEST: your entry, different exits (pooled 5 coins, "
          "zero-fee, IS->OOS) ===")
    print(f"{'tf':4} {'exit':14} {'IS net':>8} {'OOS net':>8} {'OOS n':>6} {'OOS wr':>7}")
    variants = [
        ("2R (original)", dict(rr=2.0)),
        ("1R", dict(rr=1.0)),
        ("TP 0.75xATR", dict(tp_abs_atr=0.75)),
        ("TP 0.5xATR", dict(tp_abs_atr=0.5)),
        ("TP 0.3xATR", dict(tp_abs_atr=0.3)),
    ]
    for tf in ("5m", "15m"):
        for lbl, kw in variants:
            i_net, _, _ = pooled(tf, 0.0, 0.7, LIGHTER, **kw)
            o_net, o_n, o_wr = pooled(tf, 0.7, 1.0, LIGHTER, **kw)
            print(f"{tf:4} {lbl:14} {i_net:>+8.0f} {o_net:>+8.0f} {o_n:>6} {o_wr:>6.0f}%")
        print()


def main():
    for tf in ("5m", "15m"):
        print(f"\n=== {tf} (zero-fee Lighter) ===")
        print(f"{'coin':5} {'IS net':>8} {'IS PF':>6} {'IS n':>5} {'OOS net':>8} {'OOS PF':>7} {'OOS n':>5} {'OOS wr':>7}")
        tot_is = tot_oos = 0.0
        for c in COINS:
            i = run(c, tf, 0.0, 0.7, LIGHTER); o = run(c, tf, 0.7, 1.0, LIGHTER)
            tot_is += i["net"]; tot_oos += o["net"]
            ipf = "inf" if i["pf"] == float("inf") else f"{i['pf']:.2f}"
            opf = "inf" if o["pf"] == float("inf") else f"{o['pf']:.2f}"
            print(f"{c:5} {i['net']:>+8.0f} {ipf:>6} {i['n']:>5} {o['net']:>+8.0f} {opf:>7} {o['n']:>5} {o['wr']:>6.0f}%")
        print(f"{'TOT':5} {tot_is:>+8.0f} {'':>6} {'':>5} {tot_oos:>+8.0f}")
    # fee comparison on 5m
    print(f"\n=== 5m WITH 0.05% fee (what the TV script assumes) — OOS net ===")
    for c in COINS:
        o = run(c, "5m", 0.7, 1.0, FEE05)
        print(f"  {c:5} OOS net {o['net']:>+7.0f} PF {o['pf']:.2f} n {o['n']}")
    print("\nread: positive IS+OOS across coins = real. zero-fee is our venue; the "
          "fee row shows how much the 0.05% cost eats (the TV tester includes it).")
    exit_sweep()


if __name__ == "__main__":
    main()
