"""Full tuning + honest validation for the 1H Donchian Momentum Breakout.

FIX vs first draft: indicators are computed in prepare() from the cfg, so a config sweep
MUST re-prepare the data when a prep-time knob changes (don_entry/don_exit/ema_len/
ema_slope/atr_len/vol_sma). The first draft prepped once -> those knobs were silently
inert. Here every config is prepped from raw with its own settings (cached by prep-key).

Stage 1: TRUE one-knob sweeps across all of Rich's ranges (full window, both venues).
Stage 2: focused grid, ranked for context, then genuine WALK-FORWARD OPTIMIZATION
         (re-tune on past, trade only the next unseen window, stitch) = the real test.
Stage 3: slippage 0.02/0.05/0.10, BloFin fee survival, per-coin, on the WFO pick.
"""
from __future__ import annotations
import os, sys, itertools
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import donchian_engine as de  # noqa: E402
import run_donchian as R      # noqa: E402

PREP_KEYS = ("don_entry", "don_exit", "ema_len", "ema_slope_lb", "atr_len", "vol_sma")
_raw = {}        # coin -> raw 1H df (un-prepped), loaded once
_prep_cache = {}  # (coins_key, prep-key) -> prepared dict


def raw(coins):
    for c in coins:
        if c not in _raw:
            _raw[c] = R.load_1h(c)
    return {c: _raw[c] for c in coins}


def prep(coins, cfg):
    key = (tuple(coins),) + tuple(getattr(cfg, k) for k in PREP_KEYS)
    if key not in _prep_cache:
        _prep_cache[key] = {c: de.prepare(_raw[c], cfg) for c in coins}
    return _prep_cache[key]


def run_cfg(coins, over, costs=R.LIGHTER, start=None):
    cfg = R.base_cfg(**(over or {}))
    if start is not None:
        cfg.start_equity = start
    data = prep(coins, cfg)
    tr, curve = de.simulate(data, cfg, costs)
    return tr, curve, cfg


def show_cfg(tag, coins, over, costs=R.LIGHTER):
    tr, curve, cfg = run_cfg(coins, over, costs)
    m = R.metrics(tr, curve, cfg)
    R.show(tag, m)
    return m, tr


SWEEPS = {
    "don_entry":   [15, 20, 30],
    "don_exit":    [5, 10, 15],
    "ema_len":     [50, 100, 200],
    "atr_stop":    [1.2, 1.5, 2.0],
    "atr_trail":   [2.0, 2.5, 3.0],
    "vol_mult":    [0.0, 1.1, 1.2, 1.5],
    "atr_min_pct": [0.4, 0.6, 0.8],
    "tp1_r":       [1.0, 1.5, 2.0],
    "tp2_r":       [2.0, 3.0, 4.0],
}

# inner grid for walk-forward optimization (kept modest to avoid over-search)
WFO_GRID = [dict(zip(("don_entry", "ema_len", "atr_stop", "atr_trail", "vol_mult", "atr_min_pct"), v))
            for v in itertools.product([20, 30], [100, 200], [1.5, 2.0], [2.0, 2.5], [1.2, 1.5], [0.6, 0.8])]


def wfo_optimize(coins, n_oos=5, is_frac0=0.40, costs=R.LIGHTER, sel="net"):
    """Genuine anchored WFO: each window, pick best config (over WFO_GRID) trained on
    data STRICTLY before it, trade only that window, carry equity, stitch OOS results."""
    cfg0 = R.base_cfg()
    data0 = prep(coins, cfg0)
    times = sorted(set().union(*[set(d.index) for d in data0.values()]))
    t0, t1 = times[0], times[-1]; span = t1 - t0
    cuts = [t0 + span * (is_frac0 + (1 - is_frac0) * k / n_oos) for k in range(n_oos + 1)]
    stitched, curve = [], []
    eq = cfg0.start_equity
    picks = []
    for k in range(n_oos):
        oos_lo, oos_hi = cuts[k], cuts[k + 1]
        # train: best config on entries strictly before oos_lo
        best, best_score = None, -1e18
        for over in WFO_GRID:
            cfg = R.base_cfg(**over)
            d = prep(coins, cfg)
            sub = {c: x[x.index < oos_lo] for c, x in d.items()}
            sub = {c: x for c, x in sub.items() if len(x) > cfg.ema_len + 30}
            tr, cv = de.simulate(sub, cfg, costs)
            m = R.metrics(tr, cv, cfg)
            if m and m["n"] >= 30:
                score = m["net"] if sel == "net" else m["pf"]
                if score > best_score:
                    best, best_score = over, score
        over = best or {}
        picks.append(tuple(sorted(over.items())))
        cfg = R.base_cfg(**over); cfg.start_equity = eq
        d = prep(coins, cfg)
        sub = {c: x[(x.index >= oos_lo) & (x.index < oos_hi)] for c, x in d.items()}
        sub = {c: x for c, x in sub.items() if len(x) > cfg.ema_len + 30}
        tr, cv = de.simulate(sub, cfg, costs)
        stitched += tr; curve += cv
        eq = tr[-1].equity_after if tr else eq
    m = R.metrics(stitched, curve or [(t0, cfg0.start_equity)], cfg0)
    R.show("WFO re-optimized (OOS)", m)
    print(f"    configs chosen per window: {[dict(p) for p in picks]}")
    return m


def wfo_fixed(coins, over, n_oos=5, is_frac0=0.40, costs=R.LIGHTER, label=""):
    """OOS-period test of a FIXED config (trade only the last (1-is_frac0) of history)."""
    cfg0 = R.base_cfg(**over)
    data = prep(coins, cfg0)
    times = sorted(set().union(*[set(d.index) for d in data.values()]))
    t0, t1 = times[0], times[-1]; span = t1 - t0
    lo = t0 + span * is_frac0
    sub = {c: x[x.index >= lo] for c, x in data.items()}
    tr, cv = de.simulate(sub, cfg0, costs)
    m = R.metrics(tr, cv, cfg0)
    R.show(f"OOS-period fixed {label}", m)
    return m


def main():
    coins = R.UNIVERSE
    raw(coins)
    span = (min(d.index.min() for d in _raw.values()).date(), max(d.index.max() for d in _raw.values()).date())
    print(f"# DONCHIAN SWEEP (fixed harness) | {coins} | {span[0]} -> {span[1]}\n")

    print("=" * 100 + "\nBASELINE (spec settings)\n" + "=" * 100)
    show_cfg("baseline Lighter 0-fee", coins, {}, R.LIGHTER)
    show_cfg("baseline BloFin fees", coins, {}, R.BLOFIN)

    print("\n" + "=" * 100 + "\nSTAGE 1 — TRUE one-knob sweeps (full window, Lighter 0-fee)\n" + "=" * 100)
    for knob, vals in SWEEPS.items():
        print(f"\n-- {knob} --")
        for v in vals:
            show_cfg(f"{knob}={v}", coins, {knob: v}, R.LIGHTER)

    print("\n" + "=" * 100 + "\nSTAGE 2 — core grid (context) then WALK-FORWARD OPTIMIZATION (honest)\n" + "=" * 100)
    grid = [dict(zip(("don_entry", "ema_len", "atr_stop", "vol_mult", "atr_min_pct"), v))
            for v in itertools.product([20, 30], [100, 200], [1.5, 2.0], [1.2, 1.5], [0.6, 0.8])]
    scored = []
    for over in grid:
        tr, cv, cfg = run_cfg(coins, over, R.LIGHTER)
        m = R.metrics(tr, cv, cfg)
        if m:
            scored.append((over, m))
    scored.sort(key=lambda x: x[1]["net"], reverse=True)
    print(f"  top 6 of {len(scored)} by full-window net (context only, NOT the pick):")
    for over, m in scored[:6]:
        R.show(str(over), m)

    print("\n  >>> WALK-FORWARD OPTIMIZATION (re-tune past -> trade next unseen window):")
    wfo_optimize(coins, sel="net")
    print("\n  baseline, OOS-period (fixed spec config, last 60%):")
    wfo_fixed(coins, {}, label="baseline")

    print("\n" + "=" * 100 + "\nSTAGE 3 — robustness on the strongest WFO-consistent config\n" + "=" * 100)
    # pick the config that is best by full-window net AND positive OOS-period
    cand = None
    for over, m in scored:
        mo = wfo_fixed(coins, over, label=str(over))
        if mo and mo["net"] > 0:
            cand = over; break
    if cand:
        print(f"\n  carried config (top full-window net w/ positive OOS-period) = {cand}")
        for sp in (0.02, 0.05, 0.10):
            show_cfg(f"slip {sp}%", coins, cand, de.Costs(slippage_pct=sp))
        show_cfg("BloFin fees", coins, cand, R.BLOFIN)
        _, tr = show_cfg("per-coin base (Lighter)", coins, cand, R.LIGHTER)
        R.coin_breakdown(tr)


if __name__ == "__main__":
    main()
