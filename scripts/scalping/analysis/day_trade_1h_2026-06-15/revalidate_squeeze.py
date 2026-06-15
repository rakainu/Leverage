"""Re-validate the shelved squeeze candidate on FRESH OKX data (Dec->Jun 15) and
test a few simple, principled improvements — each a ONE-KNOB change from the locked
baseline so the effect is attributable.

Honest engine (btengine). Portfolio = merged equity across SOL/ETH/ZEC/HYPE, $1000,
1% risk/trade, compounding, trades ordered by exit time (matches finalize_squeeze).

Key new test: the JUNE-FORWARD slice (data that did NOT exist when the strategy was
built on 2026-05-30) = a true out-of-sample forward test.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
ENGINE = os.path.abspath(os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
LIB = os.path.abspath(os.path.join(HERE, "..", "lighter_strat_2026-05-30"))
sys.path.insert(0, ENGINE); sys.path.insert(0, LIB)
import btengine as bt  # noqa: E402
from strat_lib import squeeze_expansion as SQ  # noqa: E402

DATA = os.path.join(HERE, "data_june")
COINS = ["SOL", "ETH", "ZEC", "HYPE"]
TF_MIN = 60
LIGHTER = bt.Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
BLOFIN = bt.Costs()
RISK = bt.RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20, liq_buffer=2.5, compounding=True)

# locked candidate (finalize_squeeze.py)
BASE = dict(side="both", bb_len=20, kc_mult=1.5, min_squeeze=10, sl_atr=1.5, tp_atr=3.0,
            trail=True, entry="market")


def load_1h(coin: str) -> pd.DataFrame:
    df5 = pd.read_parquet(os.path.join(DATA, f"okx_{coin}_5m.parquet")).astype(float)
    return df5.resample("1h").agg({"Open": "first", "High": "max", "Low": "min",
                                   "Close": "last", "Volume": "sum"}).dropna()


# ---- improvement variants: each filters the BASE signals with one extra rule ----
def make_signals(df, variant):
    p = {k: v for k, v in BASE.items() if k != "side"}
    if variant == "base":
        return SQ(df, side="both", **p)
    if variant == "sq12":     # tighter compression (>=12 bars) -> cleaner release
        p2 = dict(p); p2["min_squeeze"] = 12
        return SQ(df, side="both", **p2)
    if variant == "sq15":
        p2 = dict(p); p2["min_squeeze"] = 15
        return SQ(df, side="both", **p2)

    sigs = SQ(df, side="both", **p)
    C = df["Close"].values
    atr = bt.atr(df, 14).values
    ema50 = bt.ema(df["Close"], 50).values
    ema200 = bt.ema(df["Close"], 200).values
    basis = bt.sma(df["Close"], 20).values
    tr = pd.concat([(df["High"] - df["Low"]),
                    (df["High"] - df["Close"].shift()).abs(),
                    (df["Low"] - df["Close"].shift()).abs()], axis=1).max(axis=1).values
    out = []
    for s in sigs:
        i = s.i
        if variant == "confirm":   # release bar must show REAL expansion: range > 1*ATR
            if tr[i] > atr[i]:
                out.append(s)
        elif variant == "trend":   # only trade releases aligned with EMA50 vs EMA200
            up = ema50[i] > ema200[i]
            if (s.side > 0 and up) or (s.side < 0 and not up):
                out.append(s)
        elif variant == "trail25":
            s.trail_atr = 2.5 * atr[i]; out.append(s)
        elif variant == "trail35":
            s.trail_atr = 3.5 * atr[i]; out.append(s)
        else:
            out.append(s)
    return out


def portfolio(dfs: dict, costs, variant, start=1000.0, rf=0.01):
    recs = []
    for c, df in dfs.items():
        sigs = make_signals(df, variant)
        for t in bt.simulate(df, sigs, costs, RISK, TF_MIN):
            recs.append((t.exit_time, t.r_multiple))
    recs.sort(key=lambda x: x[0])
    if not recs:
        return None
    eq = start; curve = [start]; pnls = []
    for _, r in recs:
        pnl = r * rf * eq; eq += pnl; pnls.append(pnl); curve.append(eq)
    pnls = np.array(pnls); curve = np.array(curve)
    wins = pnls[pnls > 0]; losses = pnls[pnls < 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    peak = np.maximum.accumulate(curve); dd = ((peak - curve) / peak).max() * 100
    rs = np.array([r for _, r in recs])
    t_stat = rs.mean() / (rs.std(ddof=1) / np.sqrt(len(rs))) if len(rs) > 1 else 0.0
    return dict(n=len(pnls), pf=pf, wr=(pnls > 0).mean() * 100, avg_r=rs.mean(),
                net_pct=pnls.sum() / start * 100, max_dd=dd, t=t_stat, final=eq)


def show(tag, m):
    if m is None:
        print(f"  {tag:20} (no trades)"); return
    pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    print(f"  {tag:20} n={m['n']:>4} PF={pf:>5} WR={m['wr']:3.0f}% avgR={m['avg_r']:+.3f} "
          f"net={m['net_pct']:+6.1f}% maxDD={m['max_dd']:4.1f}% t={m['t']:+.2f}")


def per_coin(dfs, costs, variant):
    for c in COINS:
        show(c, portfolio({c: dfs[c]}, costs, variant))


def main():
    full = {c: load_1h(c) for c in COINS}
    span = (list(full.values())[0].index.min(), max(d.index.max() for d in full.values()))
    print(f"# Fresh OKX 1h | {COINS} | {span[0].date()} -> {span[1].date()}\n")

    # June-forward slice (built-on-May-30 strategy never saw this)
    JUN = pd.Timestamp("2026-06-01", tz="UTC")
    jun = {c: full[c][full[c].index >= JUN] for c in COINS}

    variants = ["base", "confirm", "trend", "sq12", "sq15", "trail25", "trail35"]

    print("=== FULL WINDOW — Lighter zero-fee (variant comparison) ===")
    for v in variants:
        show(v, portfolio(full, LIGHTER, v))

    print("\n=== BASE candidate — robustness ===")
    show("Lighter .05", portfolio(full, LIGHTER, "base"))
    show("BloFin fees", portfolio(full, BLOFIN, "base"))
    isd = {c: bt.split_is_oos(full[c], 0.70)[0] for c in COINS}
    oosd = {c: bt.split_is_oos(full[c], 0.70)[1] for c in COINS}
    show("IS 70%", portfolio(isd, LIGHTER, "base"))
    show("OOS 30%", portfolio(oosd, LIGHTER, "base"))

    print("\n=== JUNE-FORWARD slice (true forward OOS, Lighter) ===")
    show("base", portfolio(jun, LIGHTER, "base"))
    show("confirm", portfolio(jun, LIGHTER, "confirm"))
    show("trend", portfolio(jun, LIGHTER, "trend"))

    print("\n=== BASE per-coin (full window, Lighter) ===")
    per_coin(full, LIGHTER, "base")
    print("\n=== BASE per-coin (June-forward, Lighter) ===")
    per_coin(jun, LIGHTER, "base")


if __name__ == "__main__":
    main()
