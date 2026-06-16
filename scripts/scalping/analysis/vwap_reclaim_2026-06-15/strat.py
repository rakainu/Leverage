"""VWAP Failed-Breakout Reclaim Scalper — 5m, honest backtest (2026-06-15).

Spec source: Rich's "New Test.docx". This is the mean-reversion-after-failed-
breakout strategy:

  LONG  when price stretches below the lower Bollinger band AND >= X% below the
        session VWAP AND RSI < Y, then a candle RECLAIMS back inside the band and
        closes above the prior high on above-average volume (the "failed
        breakdown"). Enter on a resting limit at the reclaim close or a 25%
        retrace into the reclaim body. Mirror for SHORT.

Honesty model (inherits btengine's rules):
  - Signals decided on the CLOSE of bar i; orders worked from bar i+1 onward.
  - Limit entries are resting maker orders: fill ONLY if a later bar trades
    THROUGH the limit price. Cancel if price runs > chase_pct past the reclaim
    close before filling (the doc's "do not chase" rule).
  - Stop = wider of (swing extreme +/- buffer) or (ATR multiple). SKIP the trade
    if that distance exceeds the hard-max stop (doc rule, not a cap).
  - TP1 (50%) = min(fixed % target, current session VWAP)  -> "VWAP or +X%,
    whichever comes first", evaluated dynamically each bar.
  - After TP1 the stop moves to breakeven; TP2 (runner) = min(fixed %, opposite
    band), also dynamic.
  - Emergency exit: after `emerg_bars` candles, if still on the wrong side of
    entry on a CLOSE, flatten at that close.
  - Both-hit bar: STOP wins (conservative).
  - Session rules: one position at a time, max N trades/day, 2-loss daily
    shutdown, cooldown after a loss, and "no new trade if the signal candle's
    range > vol_spike_atr * ATR".

Costs default to Lighter (zero fee) — the deployment venue. Slippage is the
variable being stress-tested. A BloFin-fee pass is available via Costs.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# reuse the audited engine pieces (indicators, costs, sizing knobs, metrics)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30"))
from btengine import (  # noqa: E402
    ema, sma, atr, rsi, Costs, RiskCfg, Trade, metrics, fmt, split_is_oos,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "sweeps", "2026-05-20", "data")


def load(symbol: str, tf: str = "5m") -> pd.DataFrame:
    df = pd.read_parquet(os.path.join(DATA_DIR, f"blofin_{symbol}_USDT_USDT_{tf}_180d.parquet"))
    return df.astype(float)


# ----------------------------------------------------------------------------
# Indicators
# ----------------------------------------------------------------------------

def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Daily-anchored VWAP (resets at UTC midnight). No lookahead: each bar's
    VWAP uses only that day's bars up to and including itself."""
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    pv = tp * df["Volume"]
    day = df.index.tz_convert("UTC").normalize()
    cum_pv = pv.groupby(day).cumsum()
    cum_v = df["Volume"].groupby(day).cumsum()
    return cum_pv / cum_v.replace(0.0, np.nan)


def prepare(df: pd.DataFrame, bb_len: int = 20, bb_std: float = 2.0,
            atr_len: int = 14, rsi_len: int = 14,
            ema_fast: int = 50, ema_slow: int = 200, vol_len: int = 20) -> pd.DataFrame:
    d = df.copy()
    d["vwap"] = session_vwap(d)
    mid = sma(d["Close"], bb_len)
    sd = d["Close"].rolling(bb_len, min_periods=bb_len).std(ddof=0)
    d["bb_mid"] = mid
    d["bb_up"] = mid + bb_std * sd
    d["bb_lo"] = mid - bb_std * sd
    d["atr"] = atr(d, atr_len)
    d["rsi"] = rsi(d["Close"], rsi_len)
    d["ema_f"] = ema(d["Close"], ema_fast)
    d["ema_s"] = ema(d["Close"], ema_slow)
    d["ema_f_slope"] = d["ema_f"].diff() / d["ema_f"]   # per-bar fractional slope
    d["vol_sma"] = sma(d["Volume"], vol_len)
    return d


# ----------------------------------------------------------------------------
# Parameters
# ----------------------------------------------------------------------------

@dataclass
class Params:
    # entry gates
    vwap_dist_pct: float = 0.45      # X% stretch from VWAP (the doc's SOL/ETH default)
    rsi_long: float = 35.0           # RSI must be below this to go long
    rsi_short: float = 65.0          # RSI must be above this to go short
    swing_lookback: int = 10         # bars for swing high/low
    swing_buffer_pct: float = 0.15   # buffer beyond swing extreme
    atr_mult: float = 1.2            # ATR stop multiple
    hard_max_stop_pct: float = 0.65  # skip trade if needed stop > this
    entry_mode: str = "close"        # "close" or "retrace25"
    chase_pct: float = 0.25          # cancel limit if price runs this far past reclaim close
    entry_valid_bars: int = 6        # bars a resting limit stays live
    # exits
    tp1_pct: float = 0.60            # TP1 fixed target (also bounded by VWAP)
    tp2_pct: float = 1.00            # TP2 fixed target (also bounded by opposite band)
    tp1_frac: float = 0.50           # fraction taken at TP1
    use_vwap_tp1: bool = True        # cap TP1 at VWAP touch
    use_band_tp2: bool = True        # cap TP2 at opposite band
    emerg_bars: int = 4              # emergency-exit lookback
    # trend filter (optional)
    use_trend_filter: bool = True
    trend_slope_min: float = 5e-5    # |ema_f slope| considered "strong"
    # session risk controls
    max_trades_day: int = 4
    max_losses_day: int = 2
    cooldown_bars: int = 6
    vol_spike_atr: float = 2.5       # block entry if signal candle range > this * ATR


@dataclass
class Sizing:
    mode: str = "fixed_notional"     # "fixed_notional" or "risk_frac"
    notional: float = 5000.0         # used in fixed_notional mode
    leverage: float = 10.0
    starting_equity: float = 1000.0  # only used for DD% denominator / risk_frac
    risk_frac: float = 0.01          # used in risk_frac mode


# ----------------------------------------------------------------------------
# Signal generation (vectorized candidate detection; honest gating in simulate)
# ----------------------------------------------------------------------------

@dataclass
class Cand:
    i: int
    side: int
    limit_price: float
    sl_dist: float
    range_atr: float          # signal candle range / ATR (for vol-spike gate)


def gen_candidates(d: pd.DataFrame, p: Params) -> list[Cand]:
    O = d["Open"].values; H = d["High"].values; L = d["Low"].values; C = d["Close"].values
    vwap = d["vwap"].values; bb_up = d["bb_up"].values; bb_lo = d["bb_lo"].values
    rsi_ = d["rsi"].values; emaf = d["ema_f"].values; emas = d["ema_s"].values
    slope = d["ema_f_slope"].values; vol = d["Volume"].values; vsma = d["vol_sma"].values
    atr_ = d["atr"].values
    n = len(d)
    swing = p.swing_lookback
    out: list[Cand] = []

    for i in range(max(swing, 1), n):
        if np.isnan(vwap[i]) or np.isnan(bb_lo[i]) or np.isnan(rsi_[i]) or \
           np.isnan(emas[i]) or np.isnan(vsma[i]) or np.isnan(atr_[i]) or atr_[i] <= 0:
            continue
        prev_hi, prev_lo = H[i - 1], L[i - 1]
        vol_ok = vol[i] > vsma[i]
        rng_atr = (H[i] - L[i]) / atr_[i]

        # ---- LONG: failed breakdown reclaim ----
        stretched_dn = (L[i] < bb_lo[i]) or (L[i - 1] < bb_lo[i - 1])
        below_vwap = (vwap[i] - C[i]) / C[i] * 100.0 >= p.vwap_dist_pct
        reclaim_up = (C[i] >= bb_lo[i]) and (C[i] > prev_hi) and (C[i] > O[i])
        rsi_lo = rsi_[i] < p.rsi_long
        trend_block_long = p.use_trend_filter and (C[i] < emas[i]) and (slope[i] < -p.trend_slope_min)
        if stretched_dn and below_vwap and reclaim_up and rsi_lo and vol_ok and not trend_block_long:
            if p.entry_mode == "close":
                lp = C[i]
            else:  # 25% retrace into the reclaim body
                lp = C[i] - 0.25 * (C[i] - O[i])
            swing_lo = L[i - swing:i + 1].min()
            sl_swing = lp - swing_lo * (1 - p.swing_buffer_pct / 100.0)  # buffer below swing low
            sl_atr = p.atr_mult * atr_[i]
            sl_dist = max(sl_swing, sl_atr)
            if sl_dist <= 0:
                continue
            if sl_dist / lp * 100.0 <= p.hard_max_stop_pct:   # else SKIP (doc rule)
                out.append(Cand(i, +1, lp, sl_dist, rng_atr))
            continue

        # ---- SHORT: failed breakout reclaim ----
        stretched_up = (H[i] > bb_up[i]) or (H[i - 1] > bb_up[i - 1])
        above_vwap = (C[i] - vwap[i]) / C[i] * 100.0 >= p.vwap_dist_pct
        reclaim_dn = (C[i] <= bb_up[i]) and (C[i] < prev_lo) and (C[i] < O[i])
        rsi_hi = rsi_[i] > p.rsi_short
        trend_block_short = p.use_trend_filter and (C[i] > emas[i]) and (slope[i] > p.trend_slope_min)
        if stretched_up and above_vwap and reclaim_dn and rsi_hi and vol_ok and not trend_block_short:
            if p.entry_mode == "close":
                lp = C[i]
            else:
                lp = C[i] + 0.25 * (O[i] - C[i])
            swing_hi = H[i - swing:i + 1].max()
            sl_swing = swing_hi * (1 + p.swing_buffer_pct / 100.0) - lp
            sl_atr = p.atr_mult * atr_[i]
            sl_dist = max(sl_swing, sl_atr)
            if sl_dist <= 0:
                continue
            if sl_dist / lp * 100.0 <= p.hard_max_stop_pct:
                out.append(Cand(i, -1, lp, sl_dist, rng_atr))
    return out


# ----------------------------------------------------------------------------
# Session-aware simulator (honest fills + the doc's daily risk rules)
# ----------------------------------------------------------------------------

def simulate(d: pd.DataFrame, cands: list[Cand], p: Params, costs: Costs,
             sizing: Sizing, tf_minutes: int = 5) -> list[Trade]:
    O = d["Open"].values; H = d["High"].values; L = d["Low"].values; C = d["Close"].values
    vwap = d["vwap"].values; bb_up = d["bb_up"].values; bb_lo = d["bb_lo"].values
    idx = d.index
    day = idx.tz_convert("UTC").normalize()
    n = len(d)

    slip = costs.slippage_pct / 100.0
    taker = costs.taker_pct / 100.0
    maker = costs.maker_pct / 100.0

    trades: list[Trade] = []
    equity = sizing.starting_equity
    busy_until = -1
    cooldown_until = -1
    cur_day = None
    trades_today = 0
    losses_today = 0

    for cd in sorted(cands, key=lambda c: c.i):
        di = cd.i
        if di + 1 >= n:
            continue
        # daily counter reset
        if day[di] != cur_day:
            cur_day = day[di]
            trades_today = 0
            losses_today = 0
        if di <= busy_until:
            continue                      # one position at a time
        if di < cooldown_until:
            continue                      # cooldown after a loss
        if trades_today >= p.max_trades_day:
            continue
        if losses_today >= p.max_losses_day:
            continue                      # 2-loss daily shutdown
        if cd.range_atr > p.vol_spike_atr:
            continue                      # no new trade after a vertical candle

        side = cd.side
        limit_price = cd.limit_price

        # ---- ENTRY: resting maker limit, cancel on runaway (no-chase rule) ----
        entry_i = -1
        for j in range(di + 1, min(di + 1 + p.entry_valid_bars, n)):
            # cancel if price ran chase_pct favorably past the reclaim close before filling
            run = (C[di] - L[j]) / C[di] if side > 0 else (H[j] - C[di]) / C[di]
            filled = (L[j] <= limit_price) if side > 0 else (H[j] >= limit_price)
            if filled:
                entry_i = j
                break
            if run * 100.0 > p.chase_pct:
                break                      # cancelled, never filled
        if entry_i < 0:
            continue

        entry_price = limit_price          # maker fill at the resting price
        sl_dist = cd.sl_dist
        sl_price = entry_price - sl_dist if side > 0 else entry_price + sl_dist
        tp1_fixed = entry_price * (1 + p.tp1_pct / 100.0) if side > 0 else entry_price * (1 - p.tp1_pct / 100.0)
        tp2_fixed = entry_price * (1 + p.tp2_pct / 100.0) if side > 0 else entry_price * (1 - p.tp2_pct / 100.0)

        # ---- SIZING ----
        if sizing.mode == "fixed_notional":
            notional = sizing.notional
        else:
            eq = equity
            risk_usd = eq * sizing.risk_frac
            notional = min(risk_usd / (sl_dist / entry_price), eq * sizing.leverage)
        qty = notional / entry_price
        entry_fee = notional * maker

        # ---- WALK FORWARD ----
        trail_sl = sl_price
        tp1_done = False
        rem = 1.0
        legs = []                          # (frac, price, fee_rate, reason)
        exit_i = None
        mae_frac = 0.0

        for j in range(entry_i, n):
            hi, lo, cl = H[j], L[j], C[j]
            mae_frac = max(mae_frac, (entry_price - lo) / entry_price if side > 0
                           else (hi - entry_price) / entry_price)

            # hard / breakeven stop (stop wins on both-hit)
            hit_sl = (lo <= trail_sl) if side > 0 else (hi >= trail_sl)
            if hit_sl:
                px = trail_sl * (1 - slip) if side > 0 else trail_sl * (1 + slip)
                reason = "be" if tp1_done else "sl"
                legs.append((rem, px, taker, reason)); rem = 0.0; exit_i = j; break

            # TP1 = min(fixed, VWAP)  -> whichever level price reaches first
            if not tp1_done:
                t1 = tp1_fixed
                if p.use_vwap_tp1 and not np.isnan(vwap[j]):
                    t1 = min(t1, vwap[j]) if side > 0 else max(t1, vwap[j])
                hit_t1 = (hi >= t1) if side > 0 else (lo <= t1)
                if hit_t1:
                    legs.append((p.tp1_frac, t1, maker, "tp1"))
                    rem -= p.tp1_frac
                    tp1_done = True
                    trail_sl = entry_price          # stop -> breakeven on the runner
                    if rem <= 1e-9:
                        exit_i = j; break
                    # fall through; TP2 handled next bars

            # TP2 = min(fixed, opposite band)
            if tp1_done and rem > 1e-9:
                t2 = tp2_fixed
                if p.use_band_tp2:
                    band = bb_up[j] if side > 0 else bb_lo[j]
                    if not np.isnan(band):
                        t2 = min(t2, band) if side > 0 else max(t2, band)
                hit_t2 = (hi >= t2) if side > 0 else (lo <= t2)
                if hit_t2:
                    legs.append((rem, t2, maker, "tp2")); rem = 0.0; exit_i = j; break

            # emergency exit: still wrong side of entry after emerg_bars closes
            if (j - entry_i) >= p.emerg_bars:
                wrong = (cl < entry_price) if side > 0 else (cl > entry_price)
                if wrong:
                    px = cl * (1 - slip) if side > 0 else cl * (1 + slip)
                    legs.append((rem, px, taker, "emerg")); rem = 0.0; exit_i = j; break

        if rem > 1e-9:                      # ran off the end of data
            j = n - 1
            px = C[j] * (1 - slip) if side > 0 else C[j] * (1 + slip)
            legs.append((rem, px, taker, "eod")); exit_i = j; rem = 0.0

        # ---- PnL ----
        gross = 0.0; exit_fees = 0.0
        for frac, px, fee_rate, _ in legs:
            lq = qty * frac
            gross += (px - entry_price) * lq if side > 0 else (entry_price - px) * lq
            exit_fees += (lq * px) * fee_rate
        fees = entry_fee + exit_fees
        bars_held = exit_i - entry_i
        hours = bars_held * tf_minutes / 60.0
        funding = notional * (costs.funding_pct_per_8h / 100.0) * (hours / 8.0)
        pnl = gross - fees - funding
        equity += pnl
        risk_usd = notional * (sl_dist / entry_price)
        r_mult = pnl / risk_usd if risk_usd > 0 else 0.0

        trades.append(Trade(
            side=side, entry_i=entry_i, entry_time=idx[entry_i], entry_price=entry_price,
            exit_i=exit_i, exit_time=idx[exit_i], exit_price=legs[-1][1], exit_reason=legs[-1][3],
            notional=notional, qty=qty, risk_usd=risk_usd, fees_usd=fees, funding_usd=funding,
            pnl_usd=pnl, r_multiple=r_mult, equity_after=equity, bars_held=bars_held,
            liq_price=0.0, eff_leverage=sizing.leverage, mae_frac=mae_frac))

        busy_until = exit_i
        trades_today += 1
        if pnl < 0:
            losses_today += 1
            cooldown_until = exit_i + p.cooldown_bars

    return trades


def trades_per_day(trades: list[Trade], df: pd.DataFrame) -> float:
    if not trades:
        return 0.0
    days = max(1.0, (df.index.max() - df.index.min()).total_seconds() / 86400.0)
    return len(trades) / days
