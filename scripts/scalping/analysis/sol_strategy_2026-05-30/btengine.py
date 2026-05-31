"""Honest, strategy-agnostic backtest engine for SOL leverage scalping (2026-05-30).

Design goals (lessons from project_v3_entry_fill_phantom):
  - NO LOOKAHEAD: a signal is decided on the CLOSE of bar i; the order is worked
    starting at bar i+1. Indicators only ever see data up to the decision bar.
  - HONEST FILLS:
      * market entry  -> filled at next-bar OPEN, plus adverse slippage.
      * limit  entry  -> a resting maker order; fills ONLY if a later bar's
        extreme actually trades through the limit price (long: low <= limit).
      * hard stop     -> filled at the adverse bar extreme (long: low) plus
        adverse slippage; treated as a taker fill.
      * take-profit   -> resting maker limit; fills at the TP price, no slippage.
      * time stop     -> taker market at the bar close + slippage.
  - BOTH-HIT BAR: if a single bar touches both SL and TP, the STOP wins
    (conservative; we cannot know intrabar order).
  - ONE POSITION AT A TIME: signals arriving while in a trade are ignored. No
    pyramiding, no averaging into losers.

A "strategy" is any callable that, given a prepared DataFrame, returns a list of
Signal objects (decision bar index, side, entry style, SL/TP distances). The
engine turns those into honestly-filled trades and an equity curve.

Fees/slippage default to BloFin perp (taker 0.06%, maker 0.02%, slippage 0.05%).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------------
# Indicators (Pine-matched where it matters; vectorized)
# ----------------------------------------------------------------------------

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing (Pine ta.rma), used by ATR/RSI."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return rma(tr, period)


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0.0)
    dn = (-delta).clip(lower=0.0)
    rs = rma(up, period) / rma(dn, period)
    return 100.0 - 100.0 / (1.0 + rs)


def rolling_zscore(series: pd.Series, period: int) -> pd.Series:
    m = series.rolling(period, min_periods=period).mean()
    s = series.rolling(period, min_periods=period).std(ddof=0)
    return (series - m) / s.replace(0.0, np.nan)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = ((up > dn) & (up > 0)) * up
    minus_dm = ((dn > up) & (dn > 0)) * dn
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = rma(tr, period)
    plus_di = 100 * rma(plus_dm, period) / atr_
    minus_di = 100 * rma(minus_dm, period) / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return rma(dx, period)


# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------

DATA_DIR = "../sweeps/2026-05-20/data"


def load_symbol_tf(symbol: str, timeframe: str) -> pd.DataFrame:
    """Load OHLCV for a symbol. 5m/15m read cached parquet; 1h resampled from 5m."""
    import os
    base = os.path.join(os.path.dirname(__file__), DATA_DIR)
    if timeframe in ("5m", "15m"):
        df = pd.read_parquet(os.path.join(base, f"blofin_{symbol}_USDT_USDT_{timeframe}_180d.parquet"))
    elif timeframe == "1h":
        df5 = pd.read_parquet(os.path.join(base, f"blofin_{symbol}_USDT_USDT_5m_180d.parquet"))
        df = df5.resample("1h").agg({"Open": "first", "High": "max", "Low": "min",
                                     "Close": "last", "Volume": "sum"}).dropna()
    else:
        raise ValueError(f"unsupported timeframe {timeframe}")
    return df.astype(float)


def load_sol(timeframe: str) -> pd.DataFrame:
    return load_symbol_tf("SOL", timeframe)


# ----------------------------------------------------------------------------
# Signals & costs
# ----------------------------------------------------------------------------

@dataclass
class Signal:
    i: int                 # decision bar index (signal known at this bar's close)
    side: int              # +1 long, -1 short
    sl_dist: float         # stop distance in PRICE units (absolute)
    tp_dist: float         # take-profit distance in PRICE units (absolute); 0 => no fixed TP
    entry_style: str = "market"   # "market" or "limit"
    limit_dist: float = 0.0       # for limit entries: offset from decision close (favorable)
    max_bars: int = 0             # time stop in bars (0 => none)
    trail_atr: float = 0.0        # if >0, trail stop at trail_atr * atr_at_entry behind peak (close-based)
    # --- optional exit management (defaults reproduce single-TP behavior exactly) ---
    be_trigger_r: float = 0.0     # arm breakeven stop once CLOSE has moved +be_trigger_r * sl_dist in favor (0 => off)
    be_offset_r: float = 0.0      # where the BE stop sits, in units of sl_dist beyond entry (+ = locks a small gain)
    tp1_frac: float = 1.0         # fraction exited at tp_dist (TP1). 1.0 => full exit (current behavior)
    tp2_dist: float = 0.0         # runner target for the (1-tp1_frac) remainder, PRICE units (0 => none)
    be_after_tp1: bool = False    # move stop to breakeven for the runner once TP1 fills
    meta: dict = field(default_factory=dict)


@dataclass
class Costs:
    taker_pct: float = 0.06        # % per side (market / stop fills)
    maker_pct: float = 0.02        # % per side (resting limit fills: entry limit, TP)
    slippage_pct: float = 0.05     # % adverse slippage on market & stop fills
    funding_pct_per_8h: float = 0.01   # rough perp funding; applied on held notional pro-rata


@dataclass
class RiskCfg:
    starting_equity: float = 1000.0
    risk_frac: float = 0.01        # fraction of equity risked to the hard stop per trade
    max_leverage: float = 30.0     # account leverage cap (notional <= equity*max_leverage)
    maint_margin_rate: float = 0.005   # BloFin tiered maint margin ~0.5% near small size
    liq_buffer: float = 2.0        # liquidation must sit >= this * stop distance away
    compounding: bool = True


@dataclass
class Trade:
    side: int
    entry_i: int
    entry_time: pd.Timestamp
    entry_price: float
    exit_i: int
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str
    notional: float
    qty: float
    risk_usd: float
    fees_usd: float
    funding_usd: float
    pnl_usd: float          # net, after fees+funding+slippage
    r_multiple: float
    equity_after: float
    bars_held: int
    liq_price: float
    eff_leverage: float     # leverage actually used for this position
    mae_frac: float         # max adverse excursion as frac of entry (risk monitoring)


# ----------------------------------------------------------------------------
# Core simulator
# ----------------------------------------------------------------------------

def simulate(df: pd.DataFrame, signals: list[Signal], costs: Costs, risk: RiskCfg,
             tf_minutes: int, entry_valid_bars: int = 3) -> list[Trade]:
    """Replay signals into honestly-filled trades. One position at a time."""
    O = df["Open"].values; H = df["High"].values; L = df["Low"].values; C = df["Close"].values
    idx = df.index
    n = len(df)
    sigs = sorted(signals, key=lambda s: s.i)
    trades: list[Trade] = []
    equity = risk.starting_equity
    busy_until = -1  # bar index up to which we are in a position (inclusive)

    slip = costs.slippage_pct / 100.0
    taker = costs.taker_pct / 100.0
    maker = costs.maker_pct / 100.0

    for sg in sigs:
        di = sg.i
        if di + 1 >= n:
            continue
        if di <= busy_until:
            continue  # already in a trade decided earlier; ignore overlapping signal

        side = sg.side

        # ---- ENTRY FILL ----
        if sg.entry_style == "market":
            entry_i = di + 1
            raw = O[entry_i]
            entry_price = raw * (1 + slip) if side > 0 else raw * (1 - slip)
            entry_fee_rate = taker
        else:  # limit (maker), resting at favorable offset from decision close
            limit_price = C[di] - sg.limit_dist if side > 0 else C[di] + sg.limit_dist
            entry_i = -1
            for j in range(di + 1, min(di + 1 + entry_valid_bars, n)):
                if side > 0 and L[j] <= limit_price:
                    entry_i = j; break
                if side < 0 and H[j] >= limit_price:
                    entry_i = j; break
            if entry_i < 0:
                continue  # limit never filled -> no trade
            entry_price = limit_price  # maker fill at the resting price, no slippage
            entry_fee_rate = maker

        # ---- SL / TP PRICES ----
        if side > 0:
            sl_price = entry_price - sg.sl_dist
            tp_price = entry_price + sg.tp_dist if sg.tp_dist > 0 else None
        else:
            sl_price = entry_price + sg.sl_dist
            tp_price = entry_price - sg.tp_dist if sg.tp_dist > 0 else None

        stop_dist_frac = abs(entry_price - sl_price) / entry_price
        if stop_dist_frac <= 0:
            continue

        # ---- POSITION SIZING (fixed fractional risk to the hard stop) ----
        eq = equity if risk.compounding else risk.starting_equity
        risk_usd = eq * risk.risk_frac
        notional = risk_usd / stop_dist_frac
        max_notional = eq * risk.max_leverage
        if notional > max_notional:
            notional = max_notional
            risk_usd = notional * stop_dist_frac  # risk shrinks under the leverage cap
        qty = notional / entry_price

        # ---- LIQUIDATION SAFETY (isolated) ----
        # Choose per-position leverage so liquidation sits >= liq_buffer * stop distance
        # away, capped by the account max. margin posted = notional / leverage.
        safe_lev = 1.0 / (stop_dist_frac * risk.liq_buffer)
        eff_leverage = min(risk.max_leverage, max(1.0, safe_lev))
        margin = notional / eff_leverage
        liq_move_frac = (1.0 / eff_leverage) * (1 - risk.maint_margin_rate)
        liq_price = entry_price * (1 - liq_move_frac) if side > 0 else entry_price * (1 + liq_move_frac)

        # ---- WALK FORWARD TO EXIT (supports breakeven-trail + scale-out) ----
        # legs: list of (qty_fraction, exit_price, fee_rate, reason). Defaults
        # (tp1_frac=1, tp2_dist=0, be_trigger_r=0) collapse to a single-leg exit
        # identical to the original single-TP simulator.
        peak = entry_price            # for ATR trailing
        trail_sl = sl_price
        be_sl = sg.be_offset_r * sg.sl_dist  # offset magnitude (price units)
        mae_frac = 0.0
        last = n - 1
        max_bars = sg.max_bars if sg.max_bars > 0 else (last - entry_i)
        tp2_price = None
        if sg.tp1_frac < 1.0 and sg.tp2_dist > 0:
            tp2_price = entry_price + sg.tp2_dist if side > 0 else entry_price - sg.tp2_dist

        legs: list[tuple] = []
        rem = 1.0                     # remaining position fraction
        tp1_done = False
        be_armed = False
        exit_i = None

        for j in range(entry_i, min(entry_i + max_bars + 1, n)):
            hi, lo, cl = H[j], L[j], C[j]
            if side > 0:
                mae_frac = max(mae_frac, (entry_price - lo) / entry_price)
            else:
                mae_frac = max(mae_frac, (hi - entry_price) / entry_price)

            cur_sl = trail_sl
            hit_sl = (lo <= cur_sl) if side > 0 else (hi >= cur_sl)
            if hit_sl:  # STOP WINS on a both-hit bar (conservative); closes remainder
                raw = cur_sl
                px = raw * (1 - slip) if side > 0 else raw * (1 + slip)
                reason = "sl" if cur_sl == sl_price else ("be" if be_armed and not tp1_done else "trail_sl")
                legs.append((rem, px, taker, reason)); rem = 0.0; exit_i = j; break

            hit_tp1 = (tp_price is not None) and (not tp1_done) and ((hi >= tp_price) if side > 0 else (lo <= tp_price))
            if hit_tp1:
                if sg.tp1_frac >= 1.0 or tp2_price is None:
                    legs.append((rem, tp_price, maker, "tp")); rem = 0.0; exit_i = j; break
                legs.append((sg.tp1_frac, tp_price, maker, "tp1"))
                rem -= sg.tp1_frac; tp1_done = True
                if sg.be_after_tp1:  # lock breakeven on the runner
                    trail_sl = max(trail_sl, entry_price) if side > 0 else min(trail_sl, entry_price)
                    be_armed = True
                # runner continues; TP2 handled on subsequent bars (conservative)

            if tp1_done and tp2_price is not None:
                hit_tp2 = (hi >= tp2_price) if side > 0 else (lo <= tp2_price)
                if hit_tp2:
                    legs.append((rem, tp2_price, maker, "tp2")); rem = 0.0; exit_i = j; break

            # arm breakeven stop off the CLOSE (live bridge can't see wicks)
            if sg.be_trigger_r > 0 and not be_armed:
                fav = (cl - entry_price) if side > 0 else (entry_price - cl)
                if fav >= sg.be_trigger_r * sg.sl_dist:
                    be_level = entry_price + be_sl if side > 0 else entry_price - be_sl
                    trail_sl = max(trail_sl, be_level) if side > 0 else min(trail_sl, be_level)
                    be_armed = True

            if sg.trail_atr > 0:  # ATR trailing off the CLOSE
                if side > 0:
                    peak = max(peak, cl); trail_sl = max(trail_sl, peak - sg.trail_atr)
                else:
                    peak = min(peak, cl); trail_sl = min(trail_sl, peak + sg.trail_atr)

        if rem > 0:  # ran out of bars / time stop -> close remainder at last close
            j = exit_i if exit_i is not None else min(entry_i + max_bars, n - 1)
            raw = C[j]
            px = raw * (1 - slip) if side > 0 else raw * (1 + slip)
            legs.append((rem, px, taker, "time" if sg.max_bars > 0 else "eod"))
            exit_i = j; rem = 0.0

        # ---- PnL (aggregate over legs) ----
        gross = 0.0
        exit_fees = 0.0
        for frac, px, fee_rate, _ in legs:
            leg_qty = qty * frac
            gross += (px - entry_price) * leg_qty if side > 0 else (entry_price - px) * leg_qty
            exit_fees += (leg_qty * px) * fee_rate
        exit_price = legs[-1][1]               # representative (final leg) for the record
        exit_reason = legs[-1][3]
        fees = notional * entry_fee_rate + exit_fees
        bars_held = exit_i - entry_i
        hours = bars_held * tf_minutes / 60.0
        funding = notional * (costs.funding_pct_per_8h / 100.0) * (hours / 8.0)
        pnl = gross - fees - funding
        equity += pnl
        r_mult = pnl / risk_usd if risk_usd > 0 else 0.0

        trades.append(Trade(
            side=side, entry_i=entry_i, entry_time=idx[entry_i], entry_price=entry_price,
            exit_i=exit_i, exit_time=idx[exit_i], exit_price=exit_price, exit_reason=exit_reason,
            notional=notional, qty=qty, risk_usd=risk_usd, fees_usd=fees, funding_usd=funding,
            pnl_usd=pnl, r_multiple=r_mult, equity_after=equity, bars_held=bars_held,
            liq_price=liq_price, eff_leverage=eff_leverage, mae_frac=mae_frac))
        busy_until = exit_i

    return trades


# ----------------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------------

def metrics(trades: list[Trade], starting_equity: float) -> dict:
    if not trades:
        return {"n": 0, "net_pnl": 0.0, "net_pct": 0.0, "profit_factor": 0.0,
                "win_rate": 0.0, "avg_r": 0.0, "max_dd_pct": 0.0, "worst_streak": 0,
                "worst_streak_usd": 0.0, "ret": 0.0, "expectancy_r": 0.0,
                "max_leverage_used": 0.0, "liq_hits": 0, "avg_bars": 0.0,
                "final_equity": starting_equity}
    pnls = np.array([t.pnl_usd for t in trades])
    rs = np.array([t.r_multiple for t in trades])
    wins = pnls[pnls > 0]; losses = pnls[pnls < 0]
    gross_win = wins.sum(); gross_loss = -losses.sum()
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # equity curve & max drawdown (%)
    eq = np.concatenate([[starting_equity], starting_equity + np.cumsum(pnls)])
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak
    max_dd = dd.max() * 100.0

    # worst losing streak (count + cumulative $)
    streak = worst = 0; cur_usd = worst_usd = 0.0
    for p in pnls:
        if p < 0:
            streak += 1; cur_usd += p
            worst = max(worst, streak); worst_usd = min(worst_usd, cur_usd)
        else:
            streak = 0; cur_usd = 0.0

    # leverage actually used / liquidation breaches
    max_lev = max(t.eff_leverage for t in trades) if trades else 0.0
    liq_hits = sum(1 for t in trades if (
        (t.side > 0 and t.mae_frac * t.entry_price >= (t.entry_price - t.liq_price)) or
        (t.side < 0 and t.mae_frac * t.entry_price >= (t.liq_price - t.entry_price))))

    net = pnls.sum()
    return {
        "n": len(trades),
        "net_pnl": net,
        "net_pct": net / starting_equity * 100.0,
        "final_equity": starting_equity + net,
        "profit_factor": pf,
        "win_rate": len(wins) / len(trades) * 100.0,
        "avg_r": rs.mean(),
        "expectancy_r": rs.mean(),
        "max_dd_pct": max_dd,
        "worst_streak": worst,
        "worst_streak_usd": worst_usd,
        "max_leverage_used": max_lev,
        "liq_hits": liq_hits,
        "avg_bars": np.mean([t.bars_held for t in trades]),
    }


def summarize(tag: str, trades: list[Trade], starting_equity: float) -> dict:
    m = metrics(trades, starting_equity)
    m["tag"] = tag
    return m


def fmt(m: dict) -> str:
    pf = m["profit_factor"]
    pfs = "inf" if pf == float("inf") else f"{pf:.2f}"
    return (f"n={m['n']:>4}  PF={pfs:>5}  WR={m['win_rate']:>4.0f}%  "
            f"avgR={m['avg_r']:+.3f}  net={m['net_pnl']:+8.0f} ({m['net_pct']:+.0f}%)  "
            f"maxDD={m['max_dd_pct']:.1f}%  streak={m['worst_streak']}({m['worst_streak_usd']:.0f})  "
            f"liq={m['liq_hits']}  bars~{m['avg_bars']:.0f}")


# ----------------------------------------------------------------------------
# Walk-forward splitting
# ----------------------------------------------------------------------------

def split_is_oos(df: pd.DataFrame, is_frac: float = 0.70) -> tuple[pd.DataFrame, pd.DataFrame]:
    k = int(len(df) * is_frac)
    return df.iloc[:k], df.iloc[k:]


def walk_forward_folds(df: pd.DataFrame, n_folds: int = 4) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """Anchored-free rolling folds: each fold trains on a chunk, tests on the next."""
    bounds = np.linspace(0, len(df), n_folds + 1).astype(int)
    folds = []
    for k in range(n_folds):
        seg = df.iloc[bounds[k]:bounds[k + 1]]
        cut = int(len(seg) * 0.7)
        folds.append((seg.iloc[:cut], seg.iloc[cut:]))
    return folds
