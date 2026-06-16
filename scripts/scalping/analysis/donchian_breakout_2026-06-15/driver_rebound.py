"""Rebound (1H VWAP-anchored MR fade) — full validation gauntlet + report.

Carries the in-sample-promising candidate through the same honest tests that killed
the trend strategies: 70/30 OOS, walk-forward (90d train / 30d test, rolled), slippage
0.02/0.05/0.10, BloFin fees, and every rejection check. No claim survives unless the
walk-forward does.
"""
from __future__ import annotations
import os, sys, itertools
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mr_engine as M
import run_donchian as R
from search_v2 import metric, line   # generic metrics/printer

TF = 60
COINS = R.UNIVERSE
LIGHT = M.Costs(slippage_pct=0.05)
BLOFIN = M.Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05)
_raw = {}
_cache = {}
PREP = ("mean_anchor", "vwap_len", "bb_len", "bb_mult", "z_len", "adx_len", "atr_len", "vol_sma")

CHAMP = dict(mean_anchor="vwap", vwap_len=48, bb_mult=2.5, adx_max=20, trigger="reclaim",
             tp_mode="mean", tp1_frac=0.7, atr_trail=2.0, atr_stop=2.5, max_bars=24,
             risk_mode="risk", risk_usd=75)


def load_tf(c):
    if c not in _raw:
        df5 = pd.read_parquet(os.path.join(R.PATHS[c], f"okx_{c}_5m.parquet")).astype(float)
        _raw[c] = df5.resample(f"{TF}min").agg({"Open": "first", "High": "max", "Low": "min",
                                                "Close": "last", "Volume": "sum"}).dropna()
    return _raw[c]


def cfg_of(over):
    return M.Cfg(stop_cap_pct=R.STOP_CAPS, tf_minutes=TF, **(over or {}))


def prep(coins, cfg):
    key = (tuple(coins),) + tuple(getattr(cfg, k) for k in PREP)
    if key not in _cache:
        _cache[key] = {c: M.prepare(load_tf(c), cfg) for c in coins}
    return _cache[key]


def sim(coins, over, costs=LIGHT, start=None, lo=None, hi=None):
    cfg = cfg_of(over)
    if start is not None:
        cfg.start_equity = start
    data = prep(coins, cfg)
    if lo is not None or hi is not None:
        data = {c: d[((d.index >= lo) if lo is not None else True) &
                     ((d.index < hi) if hi is not None else True)] for c, d in data.items()}
        data = {c: d for c, d in data.items() if len(d) > 60}
    return E_sim(coins, data, cfg, costs)


def E_sim(coins, data, cfg, costs):
    tr, cv = M.simulate(data, cfg, costs)
    return tr, cv, cfg


def m_of(coins, over, costs=LIGHT):
    tr, cv, cfg = sim(coins, over, costs)
    return metric(tr, cv, cfg), tr, cv


def is_oos(coins, over, frac=0.70):
    cfg = cfg_of(over); data = prep(coins, cfg)
    times = sorted(set().union(*[set(d.index) for d in data.values()]))
    cut = times[int(len(times) * frac)]
    tri, cvi, _ = sim(coins, over, lo=times[0], hi=cut)
    tro, cvo, _ = sim(coins, over, lo=cut, hi=None)
    return metric(tri, cvi, cfg), metric(tro, cvo, cfg)


def walk_forward(coins, grid, train_days=90, test_days=30):
    cfg0 = cfg_of({}); data0 = prep(coins, cfg0)
    times = sorted(set().union(*[set(d.index) for d in data0.values()]))
    t0, t1 = times[0], times[-1]
    stitched, curve = [], []; eq = cfg0.start_equity; picks = []
    lo = t0
    while lo + pd.Timedelta(days=train_days) < t1:
        thi = lo + pd.Timedelta(days=train_days)
        testhi = thi + pd.Timedelta(days=test_days)
        best, bn = None, -1e18
        for over in grid:
            tr, cv, cfg = sim(coins, over, lo=lo, hi=thi)
            m = metric(tr, cv, cfg)
            if m and m["n"] >= 12 and m["net"] > bn:
                best, bn = over, m["net"]
        over = best or CHAMP; picks.append(over)
        tr, cv, cfg = sim(coins, over, start=eq, lo=thi, hi=testhi)
        stitched += tr; curve += cv
        if tr:
            eq = tr[-1].equity_after
        lo = lo + pd.Timedelta(days=test_days)
    return metric(stitched, curve or [(t0, cfg0.start_equity)], cfg0), picks


def report(coins, over, label):
    print("\n" + "#" * 96 + f"\n# {label}\n# {over}\n" + "#" * 96)
    tr, cv, cfg = sim(coins, over, LIGHT)
    m = metric(tr, cv, cfg)
    line("PORTFOLIO (Lighter 0-fee)", m)
    if not tr:
        return None
    print(f"    avgW=${m['avg_win']:+.0f} avgL=${m['avg_loss']:+.0f} hold={m['avg_hold']:.0f}h "
          f"t/wk={m['tpw']:.1f} maxConsecLoss={m['mcl']} t-stat={m['t']:+.2f}")
    line("  long only", metric([t for t in tr if t.side > 0], cv, cfg))
    line("  short only", metric([t for t in tr if t.side < 0], cv, cfg))
    by = {}
    for t in tr:
        by.setdefault(t.coin, []).append(t.pnl_usd)
    print("  by coin:")
    for c, p in sorted(by.items(), key=lambda kv: -sum(kv[1])):
        p = np.array(p); print(f"      {c:<5} n={len(p):>3} net=${p.sum():>+6.0f} WR={(p>0).mean()*100:3.0f}%")
    mp = {}
    for t in tr:
        k = t.exit_time.strftime("%Y-%m"); mp[k] = mp.get(k, 0) + t.pnl_usd
    print("  monthly:", " ".join(f"{k}:{v:+.0f}" for k, v in sorted(mp.items())))
    s = sorted(tr, key=lambda t: t.pnl_usd)
    print("  worst5:", [f"{t.coin}{t.pnl_usd:+.0f}" for t in s[:5]], " best5:", [f"{t.coin}{t.pnl_usd:+.0f}" for t in s[-5:][::-1]])
    return m, tr


def main():
    print("REBOUND — 1H VWAP-anchored mean-reversion fade | full validation\n")
    report(COINS, CHAMP, "CHAMPION (full window)")

    print("\n" + "=" * 96 + "\nVALIDATION\n" + "=" * 96)
    mi, mo = is_oos(COINS, CHAMP)
    print("  70/30 split:"); line("in-sample 70%", mi); line("out-sample 30%", mo)

    grid = [dict(CHAMP, **dict(zip(("bb_mult", "adx_max", "atr_stop", "tp1_frac"), v)))
            for v in itertools.product((2.5, 3.0), (15, 20), (2.0, 2.5), (0.7, 1.0))]
    mwf, picks = walk_forward(COINS, grid)
    print("\n  walk-forward (90/30 roll, re-optimized):"); line("WALK-FORWARD (stitched OOS)", mwf)

    print("\n  slippage / fees (full window):")
    for sp in (0.02, 0.05, 0.10):
        line(f"slip {sp}%", m_of(COINS, CHAMP, M.Costs(slippage_pct=sp))[0])
    line("BloFin fees", m_of(COINS, CHAMP, BLOFIN)[0])

    # rejection checks
    full_m, full_tr, _ = m_of(COINS, CHAMP)
    by = {}
    for t in full_tr:
        by[t.coin] = by.get(t.coin, 0) + t.pnl_usd
    net = sum(by.values()); topshare = max(by.values()) / net * 100 if net > 0 else 999
    pnls = sorted([t.pnl_usd for t in full_tr], reverse=True)
    print("\n  REJECTION CHECKS:")
    def chk(name, ok, detail):
        print(f"      [{'PASS' if ok else 'FAIL'}] {name:<26} {detail}")
    chk("OOS PF>=1.25", mo and mo["pf"] >= 1.25, f"OOS PF={mo['pf']:.2f}" if mo else "-")
    chk("walk-forward PF>=1.25", mwf and mwf["pf"] >= 1.25, f"WF PF={mwf['pf']:.2f}" if mwf else "-")
    chk("maxDD<=30%", full_m["maxdd_pct"] <= 30, f"DD={full_m['maxdd_pct']:.0f}%")
    chk("not one-coin (<60%)", 0 < topshare < 60, f"top coin={topshare:.0f}% of net")
    chk("survive remove best 3", (net - sum(pnls[:3])) > 0, f"net-best3=${net - sum(pnls[:3]):+.0f}")
    chk("survive 0.05% slip", m_of(COINS, CHAMP, M.Costs(slippage_pct=0.05))[0]["net"] > 0, "")
    if mwf:
        print(f"\n  $/day: full={full_m['avg_daily']:.1f}  OOS={mo['avg_daily']:.1f}  WF={mwf['avg_daily']:.1f} (target ~100)")


if __name__ == "__main__":
    main()
