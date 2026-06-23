"""Validate the Stage-10 winner as a TRUE single-account portfolio.

Stage 10 reported the regime-gated 4h Donchian as net +142% / maxDD 8%, but that
was fixed-risk, per-coin POOLED (each coin sized off its own $1k, summed) — NOT a
real account. This runs the SAME frozen config + the SAME BTC-daily-EMA100 regime
gate through portfolio_backtest, merging all 10 coins into one shared-capital,
compounding $3k account with concurrent-position + notional caps. That is the
headline equity curve you actually trade.

Also doubles as the first real exercise of portfolio_sim / portfolio_backtest on a
KNOWN-GOOD strategy (vs the failed hybrid), so the simulator itself is validated.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
sys.path.insert(0, HERE)
from engine import Costs, RiskCfg, ema
import portfolio_backtest as pb
from donchian_millerrh import simulate_donchian
from stage8_fair_retest import load_hist, COINS

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
BLOFIN = Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05, funding_pct_per_8h=0.01)
TF, TFM, START = "4h", 240, 3000.0
REGIME_EMA_DAYS = 100
# Stage-10 frozen config (SCORECARD)
CONFIG = dict(dc_high=49, dc_low=29, dc_stop=14, use_tight_stop=True,
              ma_filter=True, ma_len=200, ma_type="EMA")


def btc_daily_gate_fn():
    btc = load_hist("BTC", "1h")
    dclose = btc["Close"].resample("1D").last().dropna()
    reg = dclose > ema(dclose, REGIME_EMA_DAYS)
    ridx = dclose.index

    def gate_fn(coin, df):
        s = pd.Series(reg.values, index=ridx).reindex(df.index, method="ffill").fillna(False)
        return s.values.astype(bool)
    return gate_fn


def per_year(eq):
    out, prev = [], START
    for ts, v in eq.resample("YE").last().dropna().items():
        out.append((ts.year, (v / prev - 1) * 100)); prev = v
    return out


def main():
    gate_fn = btc_daily_gate_fn()
    load = lambda c: load_hist(c, TF)
    print(f"Stage-10 winner as a TRUE single account | {TF} | start ${START:.0f} | {len(COINS)} coins, 3y")
    print(f"config={CONFIG}  gate=BTC daily>EMA{REGIME_EMA_DAYS}d  caps: max_positions=5\n")
    for rf in (0.01, 0.02):
        gen = RiskCfg(starting_equity=START, risk_frac=rf, max_leverage=20, liq_buffer=2.5, compounding=False)
        acct = RiskCfg(starting_equity=START, risk_frac=rf, max_leverage=20, liq_buffer=2.5, compounding=True)
        out = pb.run(simulate_donchian, COINS, load, TFM, costs=LIGHTER, gen_risk=gen,
                     acct_risk=acct, params=CONFIG, gate_fn=gate_fn, max_positions=5)
        s = pb.summarize(out, START)
        pooled = sum(t.pnl_usd for tr in out["intents"].values() for t in tr) / START * 100
        print(f"--- risk {rf:.0%}/trade ---")
        print(f"  ACCOUNT (single, compounding): total {s['total_return_pct']:+.0f}%  CAGR {s['cagr_pct']:+.0f}%  "
              f"maxDD {s['max_dd_pct']:.0f}%  Sharpe {s['sharpe']:.2f}  liq {s['liq']}")
        print(f"    {s['n_trades']} trades over {s['n_months']} months | months green {s['months_green_pct']:.0f}%  "
              f"worst {s['worst_month_pct']:+.0f}%/mo  best {s['best_month_pct']:+.0f}%/mo")
        print(f"    per-year: " + "  ".join(f"{y} {r:+.0f}%" for y, r in per_year(out['equity_curve'])))
        print(f"    (vs per-coin POOLED fixed-risk net: {pooled:+.0f}% - the old reporting basis)")
        bout = pb.run(simulate_donchian, COINS, load, TFM, costs=BLOFIN, gen_risk=gen,
                      acct_risk=acct, params=CONFIG, gate_fn=gate_fn, max_positions=5)
        bs = pb.summarize(bout, START)
        print(f"  BloFin (informational): total {bs['total_return_pct']:+.0f}%  maxDD {bs['max_dd_pct']:.0f}%  liq {bs['liq']}\n")


if __name__ == "__main__":
    main()
