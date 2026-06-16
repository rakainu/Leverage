"""Rigorous build + validation + full report for the 1H Donchian family (v2 engine).

Pipeline:
  Stage 1  staged one-knob search (entry mode, exit model, gates, stop/trail, sizing)
  Stage 2  assemble a champion from the staged winners
  Stage 3  full report on the champion (all of Rich's 23 metrics)
  Stage 4  validation: 70/30 OOS, walk-forward 90d/30d roll, slippage, rejection checks
  Verdict  pass / paper / reject against the stated criteria
"""
from __future__ import annotations
import os, sys, itertools
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine_v2 as E      # noqa: E402
import run_donchian as R   # noqa: E402

COINS = R.UNIVERSE
STARTER = R.STARTER
_raw = {}
_cache = {}
PREP = ("don_entry", "don_exit", "ema_len", "ema_slope_lb", "atr_len", "vol_sma", "adx_len", "rs_lb")
LIGHT = E.Costs(slippage_pct=0.05)
BLOFIN = E.Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05)


def load(coins):
    for c in coins:
        if c not in _raw:
            _raw[c] = R.load_1h(c)


def cfg_of(over):
    return E.Cfg(stop_cap_pct=R.STOP_CAPS, **(over or {}))


def prep(coins, cfg):
    key = (tuple(coins),) + tuple(getattr(cfg, k) for k in PREP)
    if key not in _cache:
        _cache[key] = {c: E.prepare(_raw[c], cfg) for c in coins}
    return _cache[key]


def sim(coins, over, costs=LIGHT, start=None, lo=None, hi=None):
    cfg = cfg_of(over)
    if start is not None:
        cfg.start_equity = start
    data = prep(coins, cfg)
    if lo is not None or hi is not None:
        data = {c: d[((d.index >= lo) if lo is not None else True) &
                     ((d.index < hi) if hi is not None else True)] for c, d in data.items()}
        data = {c: d for c, d in data.items() if len(d) > cfg.ema_len + 40}
    return E.simulate(data, cfg, costs) + (cfg,)


def metric(trades, curve, cfg, days_override=None):
    if not trades:
        return None
    pnl = np.array([t.pnl_usd for t in trades])
    rs = np.array([t.r_multiple for t in trades])
    holds = np.array([t.bars_held for t in trades])
    wins = pnl[pnl > 0]; losses = pnl[pnl < 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    eq = np.array([e for _, e in curve])
    peak = np.maximum.accumulate(eq); dd_usd = (peak - eq).max(); dd_pct = ((peak - eq) / peak).max() * 100
    # max consecutive losses
    mcl = cur = 0
    for p in pnl:
        cur = cur + 1 if p < 0 else 0
        mcl = max(mcl, cur)
    days = days_override or max(1, (curve[-1][0] - curve[0][0]).days)
    net = eq[-1] - cfg.start_equity
    return dict(n=len(trades), net=net, net_pct=net / cfg.start_equity * 100, avg_daily=net / days, days=days,
                wr=(pnl > 0).mean() * 100, pf=pf, avg_win=wins.mean() if len(wins) else 0,
                avg_loss=losses.mean() if len(losses) else 0, avg_r=rs.mean(), maxdd_usd=dd_usd, maxdd_pct=dd_pct,
                mcl=mcl, avg_hold=holds.mean(), final=eq[-1],
                t=rs.mean() / (rs.std(ddof=1) / np.sqrt(len(rs))) if len(rs) > 1 else 0.0,
                tpw=len(trades) / (days / 7.0))


def line(tag, m):
    if m is None:
        print(f"  {tag:<30} (no trades)"); return
    pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    print(f"  {tag:<30} n={m['n']:>4} PF={pf:>5} WR={m['wr']:3.0f}% net=${m['net']:>+7.0f}({m['net_pct']:>+4.0f}%) "
          f"$/d={m['avg_daily']:>+5.1f} DD={m['maxdd_pct']:4.1f}% mcl={m['mcl']:>2} t={m['t']:+.2f}")


def m_of(coins, over, costs=LIGHT):
    tr, cv, cfg = sim(coins, over, costs)
    return metric(tr, cv, cfg), tr


# ----- validation -----
def is_oos(coins, over, frac=0.70):
    cfg = cfg_of(over); data = prep(coins, cfg)
    times = sorted(set().union(*[set(d.index) for d in data.values()]))
    cut = times[int(len(times) * frac)]
    tri, cvi, _ = sim(coins, over, lo=times[0], hi=cut)
    tro, cvo, _ = sim(coins, over, lo=cut, hi=None)
    return metric(tri, cvi, cfg), metric(tro, cvo, cfg)


def walk_forward(coins, grid, train_days=90, test_days=30, costs=LIGHT):
    cfg0 = cfg_of({}); data0 = prep(coins, cfg0)
    times = sorted(set().union(*[set(d.index) for d in data0.values()]))
    t0, t1 = times[0], times[-1]
    stitched, curve = [], []; eq = cfg0.start_equity; picks = []
    train_lo = t0
    while True:
        train_hi = train_lo + pd.Timedelta(days=train_days)
        test_hi = train_hi + pd.Timedelta(days=test_days)
        if train_hi >= t1:
            break
        # optimize on [train_lo, train_hi)
        best, best_net = None, -1e18
        for over in grid:
            tr, cv, cfg = sim(coins, over, costs, lo=train_lo, hi=train_hi)
            m = metric(tr, cv, cfg)
            if m and m["n"] >= 20 and m["net"] > best_net:
                best, best_net = over, m["net"]
        over = best or {}
        picks.append(over)
        tr, cv, cfg = sim(coins, over, costs, start=eq, lo=train_hi, hi=test_hi)
        stitched += tr; curve += cv
        if tr:
            eq = tr[-1].equity_after
        train_lo = train_lo + pd.Timedelta(days=test_days)
    m = metric(stitched, curve or [(t0, cfg0.start_equity)], cfg0)
    return m, picks


def rejection_checks(coins, over, oos_m, full_tr):
    checks = {}
    checks["OOS PF>=1.25"] = (oos_m and oos_m["pf"] >= 1.25, f"OOS PF={oos_m['pf']:.2f}" if oos_m else "no OOS")
    full_m = metric(full_tr, [(full_tr[0].entry_time, cfg_of(over).start_equity)] +
                    [(t.exit_time, t.equity_after) for t in full_tr], cfg_of(over)) if full_tr else None
    checks["maxDD<=30%"] = (full_m and full_m["maxdd_pct"] <= 30, f"DD={full_m['maxdd_pct']:.0f}%" if full_m else "-")
    by = {}
    for t in full_tr:
        by[t.coin] = by.get(t.coin, 0) + t.pnl_usd
    net = sum(by.values())
    topshare = max(by.values()) / net * 100 if net > 0 else 999
    checks["not one-coin (<60%)"] = (0 < topshare < 60, f"top coin={topshare:.0f}% of net")
    pnls = sorted([t.pnl_usd for t in full_tr], reverse=True)
    net_minus3 = net - sum(pnls[:3])
    checks["survive remove best 3"] = (net_minus3 > 0, f"net-best3=${net_minus3:+.0f}")
    return checks


def report(coins, over, label):
    print("\n" + "#" * 100)
    print(f"# FULL REPORT — {label}\n# config: {over}")
    print("#" * 100)
    tr, cv, cfg = sim(coins, over, LIGHT)
    m = metric(tr, cv, cfg)
    line("PORTFOLIO (Lighter 0-fee)", m)
    if not tr:
        return
    print(f"    avg winner=${m['avg_win']:+.0f}  avg loser=${m['avg_loss']:+.0f}  avg hold={m['avg_hold']:.0f}h  "
          f"trades/wk={m['tpw']:.1f}  max consec losses={m['mcl']}")
    line("  long only", metric([t for t in tr if t.side > 0], cv, cfg))
    line("  short only", metric([t for t in tr if t.side < 0], cv, cfg))
    # per coin
    print("  by coin:")
    by = {}
    for t in tr:
        by.setdefault(t.coin, []).append(t)
    for c, ts in sorted(by.items(), key=lambda kv: -sum(t.pnl_usd for t in kv[1])):
        p = np.array([t.pnl_usd for t in ts])
        print(f"      {c:<5} n={len(p):>3} net=${p.sum():>+7.0f} WR={(p>0).mean()*100:3.0f}% avg=${p.mean():>+6.1f}")
    # monthly pnl
    print("  monthly PnL:")
    mp = {}
    for t in tr:
        k = t.exit_time.strftime("%Y-%m")
        mp[k] = mp.get(k, 0) + t.pnl_usd
    for k in sorted(mp):
        print(f"      {k}  ${mp[k]:>+7.0f}")
    # best/worst
    srt = sorted(tr, key=lambda t: t.pnl_usd)
    print("  worst 5:", [f"{t.coin} ${t.pnl_usd:+.0f}" for t in srt[:5]])
    print("  best 5: ", [f"{t.coin} ${t.pnl_usd:+.0f}" for t in srt[-5:][::-1]])
    # equity curve sparkline
    eq = [e for _, e in cv]
    lo, hi = min(eq), max(eq)
    ramp = " .:-=+*#"
    spark = "".join(ramp[min(7, int((e - lo) / (hi - lo + 1e-9) * 7))] for e in eq[::max(1, len(eq) // 60)])
    print(f"  equity {cfg.start_equity:.0f}->{eq[-1]:.0f} (min {lo:.0f}): [{spark}]")
    return m, tr


if __name__ == "__main__":
    main = None
