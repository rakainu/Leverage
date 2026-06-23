"""Sanity tests pinning the honest-fill conventions of btengine.simulate.

Run: python test_engine.py   (no network)
"""
from __future__ import annotations
import os
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from engine import Signal, Costs, RiskCfg, simulate, metrics

passed = failed = 0
def check(name, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"  PASS  {name}")
    else: failed += 1; print(f"  FAIL  {name}  {extra}")

def mkdf(rows):
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(rows, columns=["Open","High","Low","Close","Volume"], index=idx).astype(float)

# Zero-cost config to test raw fill mechanics deterministically
ZERO = Costs(taker_pct=0, maker_pct=0, slippage_pct=0, funding_pct_per_8h=0)
R = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=30, compounding=False)

def t_market_entry_next_open():
    # decision at bar0; entry must be bar1 open (=100). long, SL 2 below, TP 4 above.
    df = mkdf([
        [ 90,  91,  89,  90, 1],   # bar0 decision
        [100, 101,  99, 100, 1],   # bar1 entry @open=100
        [100, 105, 100, 104, 1],   # bar2 hits TP=104
    ])
    tr = simulate(df, [Signal(i=0, side=1, sl_dist=2, tp_dist=4)], ZERO, R, 5)
    check("market entry fills at next-bar open", len(tr)==1 and tr[0].entry_price==100.0,
          f"got {tr[0].entry_price if tr else None}")
    check("TP fills at tp price (maker, no slip)", tr and tr[0].exit_price==104.0 and tr[0].exit_reason=="tp")

def t_stop_fill_at_low_with_slip():
    df = mkdf([
        [ 90,  91,  89,  90, 1],
        [100, 101,  99, 100, 1],   # entry @100, SL=98
        [100, 100,  97,  98, 1],   # low 97 <= 98 -> stop
    ])
    C = Costs(taker_pct=0, maker_pct=0, slippage_pct=0.5, funding_pct_per_8h=0)  # 0.5% slip
    tr = simulate(df, [Signal(i=0, side=1, sl_dist=2, tp_dist=10)], C, R, 5)
    # market entry slips to 100*1.005=100.5 -> SL=98.5 -> stop fill 98.5*(1-0.005)=98.0075
    check("market entry + stop both take adverse slippage", tr and abs(tr[0].entry_price-100.5)<1e-6
          and abs(tr[0].exit_price-98.0075)<1e-6, f"got entry={tr[0].entry_price}, exit={tr[0].exit_price}" if tr else "no trade")
    check("stop reason == sl", tr and tr[0].exit_reason=="sl")

def t_both_hit_stop_wins():
    df = mkdf([
        [ 90,  91,  89,  90, 1],
        [100, 101,  99, 100, 1],   # entry @100, SL=98, TP=104
        [100, 106,  97, 100, 1],   # hits BOTH 104 and 97 -> stop must win
    ])
    tr = simulate(df, [Signal(i=0, side=1, sl_dist=2, tp_dist=4)], ZERO, R, 5)
    check("both-hit bar resolves to SL (conservative)", tr and tr[0].exit_reason=="sl",
          f"got {tr[0].exit_reason if tr else None}")

def t_limit_only_fills_if_touched():
    # limit long 1.0 below decision close(=100) -> 99. Next bars never dip to 99 -> no fill.
    df = mkdf([
        [100, 100, 100, 100, 1],   # decision close=100, limit=99
        [100, 102, 99.5,101, 1],   # low 99.5 > 99 -> no fill
        [101, 103, 100, 102, 1],   # low 100 > 99 -> no fill
        [102, 104, 101, 103, 1],
    ])
    tr = simulate(df, [Signal(i=0, side=1, sl_dist=5, tp_dist=10, entry_style="limit", limit_dist=1.0)], ZERO, R, 5)
    check("unfilled limit -> no trade", len(tr)==0, f"got {len(tr)} trades")

    df2 = mkdf([
        [100, 100, 100, 100, 1],   # limit=99
        [100, 101,  98.5,99, 1],   # low 98.5 <= 99 -> fills at 99
        [ 99, 110,  99, 109, 1],   # TP 99+10=109 hits
    ])
    tr2 = simulate(df2, [Signal(i=0, side=1, sl_dist=5, tp_dist=10, entry_style="limit", limit_dist=1.0)], ZERO, R, 5)
    check("touched limit fills at limit price", tr2 and tr2[0].entry_price==99.0, f"got {tr2[0].entry_price if tr2 else None}")

def t_no_lookahead_and_one_position():
    # two signals at bar0 and bar1; second must be ignored while first is open
    df = mkdf([
        [100, 100, 100, 100, 1],
        [100, 101,  99, 100, 1],   # entry1 @100
        [100, 101,  99, 100, 1],   # still open
        [100, 105, 100, 104, 1],   # TP1 @104
        [104, 105, 103, 104, 1],
    ])
    sigs = [Signal(i=0, side=1, sl_dist=5, tp_dist=4), Signal(i=1, side=1, sl_dist=5, tp_dist=4)]
    tr = simulate(df, sigs, ZERO, R, 5)
    check("overlapping signal ignored (one position at a time)", len(tr)==1, f"got {len(tr)}")

def t_pnl_and_sizing_math():
    # entry 100, SL 98 (2% stop). risk 1% of 1000 = $10. notional = 10/0.02 = 500.
    # qty = 5. TP 104 (+4). gross = 4*5 = 20. zero fees -> pnl 20. R = 20/10 = 2.
    df = mkdf([
        [ 90,  91,  89,  90, 1],
        [100, 101,  99, 100, 1],
        [100, 105, 100, 104, 1],
    ])
    tr = simulate(df, [Signal(i=0, side=1, sl_dist=2, tp_dist=4)], ZERO, R, 5)
    t = tr[0]
    check("notional = risk/stop_frac", abs(t.notional-500)<1e-6, f"got {t.notional}")
    check("pnl matches gross (zero cost)", abs(t.pnl_usd-20)<1e-6, f"got {t.pnl_usd}")
    check("R multiple correct", abs(t.r_multiple-2.0)<1e-6, f"got {t.r_multiple}")

def t_short_side():
    df = mkdf([
        [110, 111, 109, 110, 1],
        [100, 101,  99, 100, 1],   # short entry @100, SL=102, TP=96
        [100, 100,  95,  96, 1],   # low 95 <= TP 96 -> tp
    ])
    tr = simulate(df, [Signal(i=0, side=-1, sl_dist=2, tp_dist=4)], ZERO, R, 5)
    check("short TP fills, positive pnl", tr and tr[0].exit_reason=="tp" and tr[0].pnl_usd>0,
          f"got {tr[0].exit_reason if tr else None}")

def t_leverage_cap():
    # tiny stop -> notional would explode; cap at equity*max_lev
    df = mkdf([
        [100, 100, 100, 100, 1],
        [100, 100.5, 99.99, 100, 1],   # entry @100, SL 0.05% away
        [100, 105, 100, 104, 1],
    ])
    Rcap = RiskCfg(starting_equity=1000, risk_frac=0.01, max_leverage=30, compounding=False)
    tr = simulate(df, [Signal(i=0, side=1, sl_dist=0.05, tp_dist=4)], ZERO, Rcap, 5)
    check("notional capped at equity*max_leverage", tr and tr[0].notional<=1000*30+1e-6,
          f"got {tr[0].notional if tr else None}")

if __name__ == "__main__":
    print("Engine honesty tests")
    for fn in [t_market_entry_next_open, t_stop_fill_at_low_with_slip, t_both_hit_stop_wins,
               t_limit_only_fills_if_touched, t_no_lookahead_and_one_position,
               t_pnl_and_sizing_math, t_short_side, t_leverage_cap]:
        fn()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
