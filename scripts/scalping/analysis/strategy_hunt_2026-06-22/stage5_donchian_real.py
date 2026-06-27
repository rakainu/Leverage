"""Stage 5 — the REAL @millerrh Donchian, honest engine, default-ish params.

Tests the actual strategy logic (buy-stop breakout + Donchian channel trail, NO
take-profit) — the thing my Stage 1-4 'donchian_breakout' approximation got wrong
by capping winners with an ATR TP. Default channels (20/10, tight stop 8). Across
the basket on 5m/15m/1h, both stop modes, Lighter + BloFin. Where does it live?
"""
from __future__ import annotations
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import Costs, RiskCfg                          # noqa: E402
from metrics import extended_metrics                       # noqa: E402

from stage2b_basket import load_tf, basket_metrics, COINS, RISK  # noqa: E402
from donchian_millerrh import simulate_donchian            # noqa: E402

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
BLOFIN = Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05, funding_pct_per_8h=0.01)
TFS = ["5m", "15m", "1h"]
TF_MIN = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}
fin = lambda x: 999.0 if (x == float("inf") or (isinstance(x, float) and x != x)) else x


def run_basket(dfs, costs, tfm, **kw):
    tbc, liq = {}, 0
    for c, df in dfs.items():
        tr = simulate_donchian(df, costs, RISK, tfm, **kw)
        tbc[c] = tr
        liq += extended_metrics(tr, RISK.starting_equity, compounding=False)["liq_hits"]
    return tbc, liq


def main():
    print("REAL @millerrh Donchian Breakout — honest engine, default channels (20/10, tight 8)")
    print("Long-only, buy-stop breakout, Donchian channel trailing exit, NO take-profit.\n")
    for tf in TFS:
        dfs = {c: load_tf(c, tf) for c in COINS}
        tfm = TF_MIN[tf]
        for tight in (False, True):
            tbc, liq = run_basket(dfs, LIGHTER, tfm, dc_high=20, dc_low=10, dc_stop=8, use_tight_stop=tight)
            m = basket_metrics(tbc, RISK.starting_equity)
            bf_tbc, _ = run_basket(dfs, BLOFIN, tfm, dc_high=20, dc_low=10, dc_stop=8, use_tight_stop=tight)
            bm = basket_metrics(bf_tbc, RISK.starting_equity)
            coins_prof = sum(1 for c in COINS
                             if extended_metrics(tbc[c], RISK.starting_equity, compounding=False)["profit_factor"] > 1.0)
            tag = "tight8" if tight else "loose10"
            if m is None:
                print(f"  {tf:<4} {tag:<8} no trades"); continue
            print(f"  {tf:<4} {tag:<8} n={m['n']:>4} PF={fin(m['pf']):.2f} WR={m['wr']:>3.0f}% "
                  f"payoff={fin(m['payoff']):.2f} net={m['net_pct']:>+6.0f}% DD={m['maxdd']:>3.0f}% "
                  f"liq={liq} coins+={coins_prof}/{len(COINS)} | BloFin PF={fin(bm['pf']):.2f} net={bm['net_pct']:>+6.0f}%")
    print("\n(default params only — no optimization yet. Next: optimize channels + walk-forward where it looks alive.)")


if __name__ == "__main__":
    main()
