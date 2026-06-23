"""Reusable single-strategy, multi-coin PORTFOLIO backtest.

The hunt scripts report a per-coin, fixed-risk, POOLED net% (each coin sized off
the same $1k, summed). That is not a real account. This harness runs ONE strategy
across a basket, then merges every coin's trades into a single shared-capital,
COMPOUNDING account via portfolio_sim — yielding the true equity curve (return,
maxDD, monthly distribution) you actually trade. That headline number is the #1
open caveat before any strategy goes live.

Decoupled by injection:
  - strategy_fn : fn(df, costs, risk, tf_minutes, *, entry_gate=None, **params)
                  -> list[Trade]   (the Trade-returning specialist contract;
                  wrap Signal strategies with engine.simulate before passing in)
  - load(coin)  -> OHLCV DataFrame  (caller binds the timeframe)
  - gate_fn(coin, df) -> bool array aligned to df, or None  (optional regime gate)

Per-coin intents are generated with gen_risk (use compounding=False for clean,
comparable per-trade R); the account-level compounding is owned by portfolio_sim
via acct_risk, so growth is never counted twice.
"""
from __future__ import annotations
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from metrics import extended_metrics
import portfolio_sim


def run(strategy_fn, coins, load, tf_minutes, *, costs, gen_risk, acct_risk,
        params=None, gate_fn=None, max_positions=5, max_total_notional=None):
    params = params or {}
    intents, liq = {}, 0
    for c in coins:
        df = load(c)
        gate = gate_fn(c, df) if gate_fn is not None else None
        tr = strategy_fn(df, costs, gen_risk, tf_minutes, entry_gate=gate, **params)
        intents[c] = tr
        liq += extended_metrics(tr, gen_risk.starting_equity, compounding=False)["liq_hits"]
    if max_total_notional is None:
        max_total_notional = acct_risk.starting_equity * acct_risk.max_leverage
    out = portfolio_sim.simulate(intents, acct_risk, max_positions=max_positions,
                                 max_total_notional=max_total_notional)
    out["intents"] = intents
    out["liq"] = liq
    return out


def summarize(out, starting_equity) -> dict:
    eq = out["equity_curve"]
    if eq is None or len(eq) < 2:
        return dict(total_return_pct=0.0, cagr_pct=0.0, max_dd_pct=0.0, sharpe=0.0,
                    n_trades=len(out.get("trades", [])), liq=out.get("liq", 0),
                    months_green_pct=0.0, worst_month_pct=0.0, best_month_pct=0.0, n_months=0)
    peak = eq.cummax()
    dd = float(((peak - eq) / peak).max() * 100)
    monthly = eq.resample("ME").last().dropna().pct_change().dropna() * 100
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.0, 1e-9)
    total = float(eq.iloc[-1] / eq.iloc[0] - 1) * 100
    cagr = float(((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100)
    sharpe = float(monthly.mean() / monthly.std() * np.sqrt(12)) if len(monthly) > 1 and monthly.std() > 0 else 0.0
    return dict(total_return_pct=total, cagr_pct=cagr, max_dd_pct=dd, sharpe=sharpe,
                n_trades=len(out["trades"]), liq=out["liq"], n_months=int(len(monthly)),
                months_green_pct=float((monthly > 0).mean() * 100) if len(monthly) else 0.0,
                worst_month_pct=float(monthly.min()) if len(monthly) else 0.0,
                best_month_pct=float(monthly.max()) if len(monthly) else 0.0)
