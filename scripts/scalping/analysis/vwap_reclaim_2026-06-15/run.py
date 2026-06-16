"""Run the VWAP Failed-Breakout Reclaim Scalper backtest on SOL 5m.

Per Rich's doc: start with SOL 5m, $4-6k notional, 10x, 2-loss daily shutdown.
Venue = Lighter (zero fee); slippage stress-tested at 0.02/0.05/0.10%.
Optimize for PF > 1.35, stable trades, low DD — NOT max profit.
"""
from __future__ import annotations

import itertools
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30"))
from btengine import Costs, metrics
from strat import (Params, Sizing, prepare, gen_candidates, simulate,
                   trades_per_day, load, split_is_oos)


def lighter(slip=0.05):
    return Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=slip, funding_pct_per_8h=0.01)


def blofin():
    return Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05, funding_pct_per_8h=0.01)


SIZ = Sizing(mode="fixed_notional", notional=5000.0, leverage=10.0, starting_equity=2000.0)


def row(tag, trades, df):
    m = metrics(trades, SIZ.starting_equity)
    wins = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    losses = [t.pnl_usd for t in trades if t.pnl_usd < 0]
    pf = m["profit_factor"]
    pfs = "inf" if pf == float("inf") else f"{pf:.2f}"
    return dict(tag=tag, n=m["n"], pf=pf, pf_s=pfs, wr=m["win_rate"], net=m["net_pnl"],
                maxdd=m["max_dd_pct"], aw=np.mean(wins) if wins else 0.0,
                al=np.mean(losses) if losses else 0.0, tpd=trades_per_day(trades, df),
                bars=m["avg_bars"])


def pr(r):
    print(f"  {r['tag']:<30} n={r['n']:>4}  PF={r['pf_s']:>5}  WR={r['wr']:>4.0f}%  "
          f"net={r['net']:>+8.0f}  maxDD={r['maxdd']:>4.1f}%  avgW={r['aw']:>+6.1f}  "
          f"avgL={r['al']:>+6.1f}  t/day={r['tpd']:>4.2f}  bars~{r['bars']:.0f}")


def run_one(seg, p, costs, sizing=SIZ):
    return simulate(seg, gen_candidates(seg, p), p, costs, sizing)


def main():
    SYM = "SOL"
    raw = load(SYM, "5m")
    d = prepare(raw)
    days = (raw.index.max() - raw.index.min()).days
    print(f"\n=== {SYM} 5m | {raw.index.min().date()} -> {raw.index.max().date()} "
          f"({len(raw)} bars, {days}d) | venue=Lighter 0-fee unless noted ===")

    # -------- 0) SIGNAL CADENCE: does the spec even fire enough to scalp? --------
    print(f"\n[0] SIGNAL CADENCE — doc targets 2-4 trades/day (= {2*days}-{4*days} over window)")
    print(f"    {'config':<46} {'cands':>5} {'/day':>5}")
    for tag, kw in [
        ("doc 'tuned starting' (trend filter ON)", dict()),
        ("same, trend filter OFF", dict(use_trend_filter=False)),
        ("trendOFF + swing3 + hardmax0.80", dict(use_trend_filter=False, swing_lookback=3, hard_max_stop_pct=0.80)),
        ("trendOFF + vwap0.30/rsi40 + swing3", dict(use_trend_filter=False, vwap_dist_pct=0.30, rsi_long=40, rsi_short=60, swing_lookback=3)),
        ("LOOSEST tradeable (vwap0.30/rsi40/swing3/hmax0.80)", dict(use_trend_filter=False, vwap_dist_pct=0.30, rsi_long=40, rsi_short=60, swing_lookback=3, hard_max_stop_pct=0.80)),
    ]:
        c = gen_candidates(d, Params(**kw))
        print(f"    {tag:<46} {len(c):>5} {len(c)/days:>5.2f}")

    # -------- 1) BASELINE as literally specified --------
    print("\n[1] BASELINE — doc 'Tuned starting version' EXACTLY (trend filter ON), $5k/10x, slip 0.05%")
    pr(row("full-sample", run_one(d, Params(), lighter()), d))
    print("    -> the spec as written does not trade. Trend filter (rule #7) blocks ~all longs.")

    # -------- 2) REALISTIC BASELINE (trend filter off, the only version that trades) --------
    base = Params(use_trend_filter=False, swing_lookback=3, hard_max_stop_pct=0.80)
    print("\n[2] REALISTIC BASELINE — doc params but trend filter OFF + swing3 + hardmax0.80")
    pr(row("full-sample", run_one(d, base, lighter()), d))
    is_df, oos_df = split_is_oos(d, 0.70)
    pr(row("in-sample 70%", run_one(is_df, base, lighter()), is_df))
    pr(row("out-sample 30%", run_one(oos_df, base, lighter()), oos_df))

    # -------- 3) cost / slippage sensitivity --------
    print("\n[3] COST / SLIPPAGE SENSITIVITY (realistic baseline, full sample)")
    for slip in (0.02, 0.05, 0.10):
        pr(row(f"Lighter 0-fee slip {slip:.2f}%", run_one(d, base, lighter(slip)), d))
    pr(row("BloFin fees + slip 0.05%", run_one(d, base, blofin()), d))

    # -------- 4) entry style --------
    print("\n[4] ENTRY STYLE (realistic baseline, Lighter slip 0.05%)")
    for mode in ("close", "retrace25"):
        p = Params(use_trend_filter=False, swing_lookback=3, hard_max_stop_pct=0.80, entry_mode=mode)
        pr(row(f"entry={mode}", run_one(d, p, lighter()), d))

    # -------- 5) SWEEP ranked by OOS PF --------
    print("\n[5] PARAMETER SWEEP — ranked by OUT-OF-SAMPLE profit factor")
    grid = {
        "vwap_dist_pct": [0.30, 0.40, 0.45],
        "rsi_long": [35, 40, 45],
        "use_trend_filter": [False],          # ON = untradeable (section 0)
        "swing_lookback": [3, 5],
        "atr_mult": [1.0, 1.2],
        "hard_max_stop_pct": [0.65, 0.80],
        "tp1_pct": [0.50, 0.60],
        "tp2_pct": [1.00, 1.25],
        "cooldown_bars": [3, 6],
        "entry_mode": ["close", "retrace25"],
    }
    keys = list(grid)
    combos = list(itertools.product(*[grid[k] for k in keys]))
    print(f"    evaluating {len(combos)} configs (IS train / OOS score) ...")
    cost = lighter(0.05)
    cache: dict = {}

    def cands(seg_key, seg, kw):
        ck = (seg_key, kw["vwap_dist_pct"], kw["rsi_long"], kw["swing_lookback"],
              kw["atr_mult"], kw["hard_max_stop_pct"], kw["entry_mode"])
        if ck not in cache:
            cache[ck] = gen_candidates(seg, Params(
                vwap_dist_pct=kw["vwap_dist_pct"], rsi_long=kw["rsi_long"], rsi_short=100 - kw["rsi_long"],
                use_trend_filter=False, swing_lookback=kw["swing_lookback"], atr_mult=kw["atr_mult"],
                hard_max_stop_pct=kw["hard_max_stop_pct"], entry_mode=kw["entry_mode"]))
        return cache[ck]

    results = []
    for vals in combos:
        kw = dict(zip(keys, vals))
        kw["rsi_short"] = 100 - kw["rsi_long"]
        p = Params(**kw)
        ti = simulate(is_df, cands("is", is_df, kw), p, cost, SIZ)
        to = simulate(oos_df, cands("oos", oos_df, kw), p, cost, SIZ)
        mi = metrics(ti, SIZ.starting_equity); mo = metrics(to, SIZ.starting_equity)
        if mi["n"] < 15 or mo["n"] < 6:
            continue
        results.append(dict(kw=kw, is_pf=mi["profit_factor"], is_n=mi["n"],
                            oos_pf=mo["profit_factor"], oos_n=mo["n"], oos_wr=mo["win_rate"],
                            oos_dd=mo["max_dd_pct"], oos_net=mo["net_pnl"]))

    fin = lambda x: 99.0 if x == float("inf") else x
    results.sort(key=lambda r: fin(r["oos_pf"]), reverse=True)
    print(f"    {len(results)}/{len(combos)} configs cleared min-trade filter (IS>=15, OOS>=6). Top 15 by OOS PF:")
    print(f"    {'vwX':>4} {'rsi':>3} {'sw':>2} {'atr':>4} {'mxS':>4} {'tp1':>4} {'tp2':>4} "
          f"{'cd':>2} {'entry':>9} | {'IS_PF':>5} {'IS_n':>4} | {'OOS_PF':>6} {'OOS_n':>4} {'OOS_WR':>6} {'OOS_DD':>6} {'OOSnet':>7}")
    for r in results[:15]:
        k = r["kw"]
        ip = "inf" if r["is_pf"] == float("inf") else f"{r['is_pf']:.2f}"
        op = "inf" if r["oos_pf"] == float("inf") else f"{r['oos_pf']:.2f}"
        print(f"    {k['vwap_dist_pct']:>4.2f} {k['rsi_long']:>3} {k['swing_lookback']:>2} {k['atr_mult']:>4.1f} "
              f"{k['hard_max_stop_pct']:>4.2f} {k['tp1_pct']:>4.2f} {k['tp2_pct']:>4.2f} {k['cooldown_bars']:>2} "
              f"{k['entry_mode']:>9} | {ip:>5} {r['is_n']:>4} | {op:>6} {r['oos_n']:>4} {r['oos_wr']:>5.0f}% {r['oos_dd']:>5.1f}% {r['oos_net']:>+7.0f}")
    prof = [r for r in results if fin(r["oos_pf"]) >= 1.0]
    strong = [r for r in results if fin(r["oos_pf"]) >= 1.35]
    print(f"\n    robustness: {len(prof)}/{len(results)} OOS-profitable; {len(strong)}/{len(results)} clear OOS PF>=1.35")

    # -------- 6) $100/day feasibility --------
    print("\n[6] $100/DAY FEASIBILITY (realistic baseline, full sample, Lighter slip 0.05%)")
    print(f"    {'notional':>9} {'margin@10x':>10} {'net$':>8} {'$/day':>7} {'PF':>5} {'maxDD$':>7} {'DD%margin':>10}")
    for N in (4000, 6000, 8000, 10000):
        s = Sizing(mode="fixed_notional", notional=float(N), leverage=10.0, starting_equity=2000.0)
        t = simulate(d, gen_candidates(d, base), base, lighter(), s)
        m = metrics(t, s.starting_equity)
        pnls = np.array([x.pnl_usd for x in t]); eq = np.concatenate([[0.0], np.cumsum(pnls)])
        dd = (np.maximum.accumulate(eq) - eq).max(); margin = N / 10.0
        pfs = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        print(f"    {N:>9,} {margin:>10,.0f} {m['net_pnl']:>+8.0f} {m['net_pnl']/days:>+7.2f} "
              f"{pfs:>5} {dd:>7.0f} {dd/margin*100:>9.1f}%")
    print(f"\n    (window {days}d; $/day = net/{days}. To hit $100/day you need ~{100*days:.0f} net over the window.)")


if __name__ == "__main__":
    main()
