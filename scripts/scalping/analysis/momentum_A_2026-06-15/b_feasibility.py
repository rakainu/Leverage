"""APPROACH B feasibility: the PROVEN edge (regime_mr — regime-gated VWAP mean-
reversion, the scalper's family) sized AGGRESSIVELY toward Rich's target: ~2x in
1-2 months within ~40% drawdown on $3,000.

Runs on the fresh 15m basket, Lighter zero-fee, maker entry. Sweeps risk/trade and
reports realized return, drawdown, and the implied WEEKS-TO-DOUBLE."""
from __future__ import annotations
import os, sys, math
import numpy as np
from common import available_coins, load, portfolio, weeks_span, LIGHTER, LIGHTER_2X, bt, TF_MIN  # local first
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scalp_search_2026-05-30")))
from strat_lib import regime_mr  # noqa: E402

# deployed winning config (STRATEGY.md): sl 2.0ATR, tp 0.3*dist-to-vwap, 12-bar stop
CFG = dict(side="both", trend_len=200, slope_lb=20, z_period=30, z_entry=1.5,
           sl_atr=2.0, tp_frac=0.3, max_bars=12, limit_atr=0.25)
VALIDATED = ["SOL", "ETH", "ZEC", "HYPE", "BTC"]
START = 3000.0


def per_coin_trades(dfs, costs, risk):
    return {c: bt.simulate(df, regime_mr(df, **CFG), costs, risk, TF_MIN) for c, df in dfs.items()}


def weeks_to_2x(final, start, weeks):
    if final <= start:
        return float("inf")
    g = (final / start) ** (1.0 / weeks) - 1.0          # realized weekly growth
    return math.log(2) / math.log(1 + g)


def show(tag, m, weeks):
    if m is None:
        print(f"  {tag:24} (no trades)"); return
    w2x = weeks_to_2x(m["final"], START, weeks)
    w2xs = "never" if w2x == float("inf") else f"{w2x:.1f}wk"
    print(f"  {tag:24} ${m['final']:>8,.0f} net {m['net_pct']:+7.0f}% maxDD {m['max_dd']:4.0f}% "
          f"PF {m['pf']:.2f} WR {m['wr']:.0f}% n={m['n']} ->2x {w2xs}")


def main():
    coins = available_coins()
    full = {c: load(c) for c in coins}
    val = {c: full[c] for c in VALIDATED if c in full}
    wk = weeks_span(full)
    print(f"# Approach B (regime_mr, PROVEN edge) | 15m Lighter 0-fee | ~{wk:.0f}wk | $3k start")
    print(f"# config: {CFG}\n")

    for label, dfs in (("VALIDATED 5-coin", val), ("FULL 8-coin", full)):
        w = weeks_span(dfs)
        print(f"=== {label} ({list(dfs)}) ===")
        for rf in (0.01, 0.02, 0.03, 0.04):
            risk = bt.RiskCfg(starting_equity=START, risk_frac=rf, max_leverage=10,
                              liq_buffer=2.5, compounding=True)
            per = per_coin_trades(dfs, LIGHTER, risk)
            m = portfolio(per, start=START, rf=rf, compounding=True)
            show(f"risk {rf*100:.0f}% compound", m, w)
        # 2x-slip stress at a mid risk
        risk = bt.RiskCfg(starting_equity=START, risk_frac=0.02, max_leverage=10,
                          liq_buffer=2.5, compounding=True)
        per = per_coin_trades(dfs, LIGHTER_2X, risk)
        show("risk 2% @2x slippage", portfolio(per, start=START, rf=0.02, compounding=True), w)
        print()


if __name__ == "__main__":
    main()
