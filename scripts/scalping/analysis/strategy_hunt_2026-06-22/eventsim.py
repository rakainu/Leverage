"""Generic honest event-driven simulator: entry-signal + exit-signal + ATR stop.

Unlocks faithful porting of the many real strategies whose exit is an INDICATOR
event (opposite crossover, trend flip) rather than a fixed TP — which the Signal
engine can't express. Same honesty discipline as engine.py:

  - NO LOOKAHEAD: entry/exit booleans are decided on bar i's CLOSE; the order
    fills at i+1's OPEN (+ adverse slippage, taker). ATR stop uses ATR at the
    decision bar.
  - HONEST FILLS: signal exit = market at next open + slippage. Protective stop =
    intrabar at the stop price + slippage (taker). Stop checked every bar.
  - One position at a time. Fixed-fractional sizing to the ATR stop, leverage cap
    + liquidation model reused from the engine. Produces engine.Trade objects so
    the same metrics/guardrails/validation apply.
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import Costs, RiskCfg, Trade  # noqa: E402


def simulate_rules(df, *, entries_long=None, exits_long=None, entries_short=None,
                   exits_short=None, atr_series, atr_mult, costs: Costs, risk: RiskCfg,
                   tf_minutes: int, max_bars: int = 0) -> list[Trade]:
    """Replay boolean entry/exit signals with an ATR protective stop, honestly."""
    O = df["Open"].values; H = df["High"].values; L = df["Low"].values; C = df["Close"].values
    idx = df.index
    n = len(df)
    z = np.zeros(n, dtype=bool)
    eL = entries_long.values if entries_long is not None else z
    xL = exits_long.values if exits_long is not None else z
    eS = entries_short.values if entries_short is not None else z
    xS = exits_short.values if exits_short is not None else z
    av = atr_series.values

    slip = costs.slippage_pct / 100.0
    taker = costs.taker_pct / 100.0
    trades: list[Trade] = []
    equity = risk.starting_equity
    i = 0
    while i < n - 1:
        long_sig = eL[i]; short_sig = eS[i]
        if not (long_sig or short_sig) or not np.isfinite(av[i]) or av[i] <= 0:
            i += 1; continue
        sidev = 1 if long_sig else -1            # prefer long if both (rare)
        f = i + 1                                 # fill next bar open
        entry_price = O[f] * (1 + slip) if sidev > 0 else O[f] * (1 - slip)
        stop_dist = atr_mult * av[i]
        init_stop = entry_price - stop_dist if sidev > 0 else entry_price + stop_dist
        sdf = stop_dist / entry_price
        if sdf <= 0:
            i += 1; continue

        eq = equity if risk.compounding else risk.starting_equity
        risk_usd = eq * risk.risk_frac
        notional = risk_usd / sdf
        if notional > eq * risk.max_leverage:
            notional = eq * risk.max_leverage; risk_usd = notional * sdf
        qty = notional / entry_price
        safe_lev = 1.0 / (sdf * risk.liq_buffer)
        eff_lev = min(risk.max_leverage, max(1.0, safe_lev))
        liq = entry_price * (1 - (1.0 / eff_lev) * (1 - risk.maint_margin_rate)) if sidev > 0 \
            else entry_price * (1 + (1.0 / eff_lev) * (1 - risk.maint_margin_rate))

        stop = init_stop; pending = False; mae = 0.0
        exit_i = exit_price = reason = None
        cap = (f + max_bars) if max_bars > 0 else (n - 1)
        j = f
        while j < n:
            mae = max(mae, (entry_price - L[j]) / entry_price if sidev > 0 else (H[j] - entry_price) / entry_price)
            if pending:                                   # signal exit -> fill this open
                exit_price = O[j] * (1 - slip) if sidev > 0 else O[j] * (1 + slip)
                reason = "signal"; exit_i = j; break
            if (sidev > 0 and L[j] <= stop) or (sidev < 0 and H[j] >= stop):
                exit_price = stop * (1 - slip) if sidev > 0 else stop * (1 + slip)
                reason = "stop"; exit_i = j; break
            if j >= cap:
                exit_price = C[j] * (1 - slip) if sidev > 0 else C[j] * (1 + slip)
                reason = "time"; exit_i = j; break
            if (sidev > 0 and xL[j]) or (sidev < 0 and xS[j]):
                pending = True                            # exit at next open
            j += 1
        if exit_i is None:
            exit_i = n - 1
            exit_price = C[exit_i] * (1 - slip) if sidev > 0 else C[exit_i] * (1 + slip)
            reason = "eod"

        bars = exit_i - f
        hours = bars * tf_minutes / 60.0
        fees = notional * taker + (qty * exit_price) * taker
        funding = notional * (costs.funding_pct_per_8h / 100.0) * (hours / 8.0)
        pnl = (exit_price - entry_price) * qty * sidev - fees - funding
        equity += pnl
        trades.append(Trade(
            side=sidev, entry_i=f, entry_time=idx[f], entry_price=entry_price,
            exit_i=exit_i, exit_time=idx[exit_i], exit_price=exit_price, exit_reason=reason,
            notional=notional, qty=qty, risk_usd=risk_usd, fees_usd=fees, funding_usd=funding,
            pnl_usd=pnl, r_multiple=pnl / risk_usd if risk_usd > 0 else 0.0, equity_after=equity,
            bars_held=bars, liq_price=liq, eff_leverage=eff_lev, mae_frac=mae))
        i = exit_i + 1
    return trades
