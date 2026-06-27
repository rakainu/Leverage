"""Faithful honest port of the @millerrh 'Donchian Breakout' Pine strategy.

Pine logic (v5, long-only):
  - Entry: buy-stop at the dcPeriodHigh-bar high (new N-bar high breakout).
  - Exit:  TRAIL the dcPeriodLow-bar low (Donchian channel trailing stop). NO take
    profit — winners run until price breaks the lower channel. (This is the Turtle
    'let winners run' mechanism my earlier ATR-TP reimplementation destroyed.)
  - Optional tighter initial stop: trail the dcPeriod2Low-bar low until in profit,
    then loosen to the dcPeriodLow trail.
  - Optional filters (all OFF by default): current-TF MA, HTF MA, MA-slope, ADR.

Honesty layer (same discipline as engine.py, intentionally stricter than Pine,
which uses current-bar intrabar channels):
  - NO LOOKAHEAD: entry/exit channel levels use only CONFIRMED prior bars (shift 1).
    Entry level at bar i = highest high of [i-N .. i-1]; fires if bar i's HIGH
    crosses it. Trail level likewise from prior bars.
  - HONEST FILLS: stop-entry fills at max(level, open)+slippage (taker) — a gap
    above the level fills worse, at the open. Trailing-stop exit fills at
    min(level, open)-slippage (taker). Both are taker (stop orders).
  - Monotonic trail: the stop only ratchets up, never down.
  - Fixed-fractional sizing to the INITIAL stop, leverage cap + liquidation model
    reused from the engine, so results feed the same metrics/guardrails.
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import Costs, RiskCfg, Trade, ema, sma  # noqa: E402


def _ma(series, length, kind):
    return ema(series, length) if kind == "EMA" else sma(series, length)


def simulate_donchian(df, costs: Costs, risk: RiskCfg, tf_minutes: int, *,
                      dc_high=20, dc_low=10, dc_stop=8, use_tight_stop=False,
                      ma_filter=False, ma_len=50, ma_type="SMA",
                      slope_filter=False, slope_len=5, slope_type="SMA",
                      entry_gate=None) -> list[Trade]:
    """Replay the millerrh Donchian breakout honestly. Long-only, one position.

    entry_gate: optional bool array/Series aligned to df. When provided, a new
    position may only open on a bar where the gate is True (used for a broad-market
    regime filter — e.g. only go long while BTC is above its 200-EMA)."""
    if entry_gate is not None:
        entry_gate = np.asarray(entry_gate, dtype=bool)
    O = df["Open"].values; H = df["High"].values; L = df["Low"].values; C = df["Close"].values
    idx = df.index
    n = len(df)

    # channel levels from CONFIRMED prior bars (no lookahead)
    up_lvl = pd.Series(H).rolling(dc_high).max().shift(1).values
    lo_lvl = pd.Series(L).rolling(dc_low).min().shift(1).values
    tight_lvl = pd.Series(L).rolling(dc_stop).min().shift(1).values

    # optional filters (evaluated on confirmed close, shift 1)
    if ma_filter:
        maf = _ma(df["Close"], ma_len, ma_type).shift(1).values
    if slope_filter:
        ms = _ma(df["Close"], slope_len, slope_type)
        rising = (ms > ms.shift(1)).shift(1).values

    slip = costs.slippage_pct / 100.0
    taker = costs.taker_pct / 100.0

    trades: list[Trade] = []
    equity = risk.starting_equity
    i = 0
    while i < n:
        # ---- look for ENTRY ----
        if not np.isfinite(up_lvl[i]) or not np.isfinite(lo_lvl[i]):
            i += 1; continue
        if entry_gate is not None and not entry_gate[i]:
            i += 1; continue                 # broad-market regime gate: off -> no new longs
        if ma_filter and (not np.isfinite(maf[i]) or up_lvl[i] <= maf[i]):
            i += 1; continue
        if slope_filter and not (rising[i] == True):  # noqa: E712
            i += 1; continue
        if H[i] < up_lvl[i]:        # no breakout this bar
            i += 1; continue

        # breakout: stop-buy fills at the level, or worse at the open if it gapped
        raw_entry = max(up_lvl[i], O[i])
        entry_price = raw_entry * (1 + slip)
        init_stop = tight_lvl[i] if use_tight_stop else lo_lvl[i]
        if not np.isfinite(init_stop) or init_stop >= entry_price:
            i += 1; continue
        stop_dist_frac = (entry_price - init_stop) / entry_price

        # sizing (fixed fractional risk to initial stop) + leverage cap + liq model
        eq = equity if risk.compounding else risk.starting_equity
        risk_usd = eq * risk.risk_frac
        notional = risk_usd / stop_dist_frac
        if notional > eq * risk.max_leverage:
            notional = eq * risk.max_leverage
            risk_usd = notional * stop_dist_frac
        qty = notional / entry_price
        safe_lev = 1.0 / (stop_dist_frac * risk.liq_buffer)
        eff_lev = min(risk.max_leverage, max(1.0, safe_lev))
        liq_price = entry_price * (1 - (1.0 / eff_lev) * (1 - risk.maint_margin_rate))

        entry_i = i
        trail = init_stop
        in_profit = False
        mae = 0.0
        exit_i = exit_price = exit_reason = None

        j = i + 1
        while j < n:
            mae = max(mae, (entry_price - L[j]) / entry_price)
            # ratchet the trail with confirmed prior-bar channel
            if use_tight_stop and not in_profit:
                # tighten on dc_stop until the tight stop rises above entry (=in profit)
                if np.isfinite(tight_lvl[j]):
                    trail = max(trail, tight_lvl[j])
                if trail >= entry_price:
                    in_profit = True
            else:
                if np.isfinite(lo_lvl[j]):
                    trail = max(trail, lo_lvl[j])
            # exit if this bar trades down to the trail
            if L[j] <= trail:
                raw_exit = min(trail, O[j])
                exit_price = raw_exit * (1 - slip)
                exit_reason = "trail" if trail > init_stop else "stop"
                exit_i = j
                break
            j += 1
        if exit_i is None:  # ran out of data -> close at last close
            exit_i = n - 1
            exit_price = C[exit_i] * (1 - slip)
            exit_reason = "eod"

        bars_held = exit_i - entry_i
        hours = bars_held * tf_minutes / 60.0
        fees = notional * taker + (qty * exit_price) * taker
        funding = notional * (costs.funding_pct_per_8h / 100.0) * (hours / 8.0)
        pnl = (exit_price - entry_price) * qty - fees - funding
        equity += pnl
        trades.append(Trade(
            side=1, entry_i=entry_i, entry_time=idx[entry_i], entry_price=entry_price,
            exit_i=exit_i, exit_time=idx[exit_i], exit_price=exit_price, exit_reason=exit_reason,
            notional=notional, qty=qty, risk_usd=risk_usd, fees_usd=fees, funding_usd=funding,
            pnl_usd=pnl, r_multiple=pnl / risk_usd if risk_usd > 0 else 0.0, equity_after=equity,
            bars_held=bars_held, liq_price=liq_price, eff_leverage=eff_lev, mae_frac=mae))
        i = exit_i + 1   # one position at a time; resume scanning after the exit
    return trades
