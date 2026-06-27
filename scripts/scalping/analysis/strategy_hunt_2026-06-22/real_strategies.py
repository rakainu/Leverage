"""Faithful ports of REAL strategies (from Rich's TradingView library) as Signal
families for the honest engine. Each is a documented, vetted strategy — NOT an
invented family. Logic extracted from the actual Pine source.

Port log:
  flawless_victory_v1 — Flawless Victory Strategy v11.3, mode v1 (clean BB+RSI).
    Source: USER;eb03dc2a... Pine v5. Long+short (default 'Long Only').
    Buy_1  = close < BB_lower(12,1.99) and RSI(9) > 42
    Sell_1 = close > BB_upper(12,1.99) and RSI(9) > 70   (long exit / short entry)
    Exit: fixed stop 0.5%, fixed TP 1.57% (strategy.exit bracket).
    Fidelity notes: models the fixed SL/TP bracket (the dominant exit at 0.5%/1.57%);
    the secondary Sell_1 indicator-close is omitted (rarely fires before the tight
    bracket). BB stdev = population (ddof=0) to match Pine ta.stdev. RSI = Wilder
    (engine.rsi) to match Pine ta.rsi.
"""
from __future__ import annotations
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import Signal, sma, rsi as rsi_ind  # noqa: E402


def _allow(side_val, side):
    if side == "both":
        return True
    return side_val > 0 if side == "long" else side_val < 0


def flawless_victory_v1(df, side="long", bb_len=12, bb_mult=1.99, rsi_len=9,
                        rsi_buy=42, rsi_sell=70, sl_pct=0.5, tp_pct=1.57, max_bars=0):
    """Flawless Victory v1 (faithful). Default side='long' (strategy default 'Long Only')."""
    C = df["Close"]
    basis = sma(C, bb_len)
    dev = bb_mult * C.rolling(bb_len, min_periods=bb_len).std(ddof=0)  # Pine ta.stdev = population
    upper = (basis + dev).values
    lower = (basis - dev).values
    r = rsi_ind(C, rsi_len).values
    cv = C.values
    sigs = []
    for i in range(len(df)):
        if not np.isfinite(lower[i]) or not np.isfinite(r[i]):
            continue
        side_val = 0
        if cv[i] < lower[i] and r[i] > rsi_buy:
            side_val = 1
        elif cv[i] > upper[i] and r[i] > rsi_sell:
            side_val = -1
        if side_val == 0 or not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val,
                           sl_dist=cv[i] * sl_pct / 100.0,
                           tp_dist=cv[i] * tp_pct / 100.0,
                           entry_style="market", max_bars=max_bars))
    return sigs


if __name__ == "__main__":
    # quick honest default-param test across the basket + timeframes
    from engine import Costs, RiskCfg, simulate
    from metrics import extended_metrics
    sys.path.insert(0, os.path.join(HERE, "..", "scalp_search_2026-05-30"))
    sys.path.insert(0, HERE)
    from stage2b_basket import load_tf, basket_metrics, COINS, RISK  # noqa: E402

    LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
    BLOFIN = Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05, funding_pct_per_8h=0.01)
    TF_MIN = {"5m": 5, "15m": 15, "1h": 60}
    fin = lambda x: 999.0 if (x == float("inf") or (isinstance(x, float) and x != x)) else x

    def run_basket(dfs, costs, tfm, sd):
        tbc, liq = {}, 0
        for c, d in dfs.items():
            tr = simulate(d, flawless_victory_v1(d, side=sd), costs, RISK, tfm)
            tbc[c] = tr
            liq += extended_metrics(tr, RISK.starting_equity, compounding=False)["liq_hits"]
        return tbc, liq

    print("Flawless Victory v1 (faithful) — honest engine, default params (BB12/1.99, RSI9, SL0.5%/TP1.57%)")
    for tf in ["5m", "15m", "1h"]:
        dfs = {c: load_tf(c, tf) for c in COINS}
        for sd in ["long", "both"]:
            tbc, liq = run_basket(dfs, LIGHTER, TF_MIN[tf], sd)
            m = basket_metrics(tbc, RISK.starting_equity)
            bf, _ = run_basket(dfs, BLOFIN, TF_MIN[tf], sd)
            bm = basket_metrics(bf, RISK.starting_equity)
            cp = sum(1 for c in COINS if extended_metrics(tbc[c], RISK.starting_equity, compounding=False)["profit_factor"] > 1.0)
            if m is None:
                print(f"  {tf:<4} {sd:<5} no trades"); continue
            print(f"  {tf:<4} {sd:<5} n={m['n']:>4} PF={fin(m['pf']):.2f} WR={m['wr']:>3.0f}% "
                  f"payoff={fin(m['payoff']):.2f} net={m['net_pct']:>+6.0f}% DD={m['maxdd']:>3.0f}% "
                  f"liq={liq} coins+={cp}/8 | BloFin PF={fin(bm['pf']):.2f} net={bm['net_pct']:>+6.0f}%")
