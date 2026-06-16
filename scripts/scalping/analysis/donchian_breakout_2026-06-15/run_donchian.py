"""Load data, run the 1H Donchian Momentum Breakout, report. Sanity + baseline.

Data sources (OKX 1H, resampled from 5m, ~200d incl. June-forward holdout):
  data_june/        -> SOL ETH HYPE (and ZEC, unused here)
  ../day_trade_1h_2026-06-15/data_oos_coins/ -> BTC BNB XRP DOGE AVAX LINK
  data/             -> SUI
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import donchian_engine as de  # noqa: E402

DJUNE = os.path.join(HERE, "..", "day_trade_1h_2026-06-15", "data_june")
DOOS = os.path.join(HERE, "..", "day_trade_1h_2026-06-15", "data_oos_coins")
DSUI = os.path.join(HERE, "data")

PATHS = {
    "SOL": DJUNE, "ETH": DJUNE, "HYPE": DJUNE,
    "BTC": DOOS, "BNB": DOOS, "XRP": DOOS, "DOGE": DOOS, "AVAX": DOOS, "LINK": DOOS,
    "SUI": DSUI,
}
UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "DOGE", "SUI", "LINK", "AVAX", "BNB", "HYPE"]
STARTER = ["SOL", "ETH", "BTC", "DOGE", "SUI"]
STOP_CAPS = {"BTC": 1.2, "ETH": 1.5, "SOL": 1.8, "DOGE": 2.2, "SUI": 2.2, "HYPE": 2.2}

LIGHTER = de.Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
BLOFIN = de.Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05, funding_pct_per_8h=0.01)


def load_1h(coin):
    df5 = pd.read_parquet(os.path.join(PATHS[coin], f"okx_{coin}_5m.parquet")).astype(float)
    return df5.resample("1h").agg({"Open": "first", "High": "max", "Low": "min",
                                   "Close": "last", "Volume": "sum"}).dropna()


def base_cfg(**over):
    c = de.Cfg(stop_cap_pct=STOP_CAPS)
    for k, v in over.items():
        setattr(c, k, v)
    return c


def prep_universe(coins, cfg):
    return {c: de.prepare(load_1h(c), cfg) for c in coins}


def metrics(trades, curve, cfg):
    if not trades:
        return None
    pnls = np.array([t.pnl_usd for t in trades])
    rs = np.array([t.r_multiple for t in trades])
    wins = pnls[pnls > 0]; losses = pnls[pnls < 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    eq = np.array([e for _, e in curve])
    peak = np.maximum.accumulate(eq); dd_usd = (peak - eq).max()
    dd_pct = ((peak - eq) / peak).max() * 100
    days = max(1, (curve[-1][0] - curve[0][0]).days)
    net = eq[-1] - cfg.start_equity
    return dict(
        n=len(trades), net=net, net_pct=net / cfg.start_equity * 100,
        avg_daily=net / days, days=days,
        wr=(pnls > 0).mean() * 100, pf=pf,
        avg_win=wins.mean() if len(wins) else 0.0,
        avg_loss=losses.mean() if len(losses) else 0.0,
        avg_r=rs.mean(), maxdd_usd=dd_usd, maxdd_pct=dd_pct,
        final=eq[-1], t=rs.mean() / (rs.std(ddof=1) / np.sqrt(len(rs))) if len(rs) > 1 else 0.0,
    )


def show(tag, m):
    if m is None:
        print(f"  {tag:<26} (no trades)"); return
    pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    print(f"  {tag:<26} n={m['n']:>4} PF={pf:>5} WR={m['wr']:3.0f}% net=${m['net']:>+7.0f} "
          f"({m['net_pct']:>+5.0f}%) $/day={m['avg_daily']:>+5.1f} DD=${m['maxdd_usd']:>5.0f}/{m['maxdd_pct']:4.1f}% "
          f"avgW=${m['avg_win']:>+5.0f} avgL=${m['avg_loss']:>+5.0f} t={m['t']:+.2f}")


def coin_breakdown(trades):
    by = {}
    for t in trades:
        by.setdefault(t.coin, []).append(t.pnl_usd)
    print("  per-coin:")
    for c, p in sorted(by.items(), key=lambda kv: -sum(kv[1])):
        p = np.array(p)
        w = (p > 0).mean() * 100
        print(f"      {c:<5} n={len(p):>3} net=${p.sum():>+7.0f} WR={w:3.0f}% avg=${p.mean():>+6.1f}")


def reason_mix(trades):
    from collections import Counter
    cnt = Counter()
    for t in trades:
        for r in t.reasons.split("+"):
            cnt[r] += 1
    return dict(cnt)


def main():
    cfg = base_cfg()
    print("# 1H DONCHIAN MOMENTUM BREAKOUT — baseline sanity\n")
    for name, coins in (("STARTER 5", STARTER), ("FULL 10", UNIVERSE)):
        data = prep_universe(coins, cfg)
        span = (min(d.index.min() for d in data.values()).date(),
                max(d.index.max() for d in data.values()).date())
        print(f"== {name} {coins} | {span[0]} -> {span[1]} ==")
        for vlabel, costs in (("Lighter 0-fee", LIGHTER), ("BloFin fees", BLOFIN)):
            tr, curve = de.simulate(data, cfg, costs)
            m = metrics(tr, curve, cfg)
            show(f"{vlabel}", m)
            if costs is LIGHTER and tr:
                print("    exit reasons:", reason_mix(tr))
                coin_breakdown(tr)
        print()


if __name__ == "__main__":
    main()
