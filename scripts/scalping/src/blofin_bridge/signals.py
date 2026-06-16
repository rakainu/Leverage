"""V3.2 self-generated entry signal — the proven HA-V3 model, in pure Python.

V3.1 depended on a TradingView "Pro V3" webhook for its buy/sell calls. The
audit showed that signal was both sparse (only ~36% of alerts filled) and that
its long entries lost money even with a perfect exit. The backtest engine's
own signal — Heikin-Ashi smoothed-trend crossings with a fakeout (body/ATR) and
ADX range filter — is what produces the +$19k / PF 2.80 result. V3.2 generates
*that* signal itself, on every closed 5m bar, removing the TradingView
dependency entirely.

This module is the bridge-side port of the engine's `generate_v3_signals`. The
formulas match TradingView exactly (EMA: 2/(L+1) with an SMA seed; SMMA/RMA:
Wilder's smoothing) so `tests/test_signals.py` can prove it reproduces the
engine bar-for-bar against a golden ZEC fixture.

Pure functions only — no I/O, no clock, no exchange calls. The runtime wiring
that fetches bars and queues pending signals lives in `signal_engine.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class Bar:
    """One OHLC candle. Volume is not needed by the signal."""
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class SignalParams:
    """HA-V3 signal inputs — defaults mirror the engine (sens=8, fakeout=0.2,
    range=0.2). `smooth_len = 16 - sensitivity`."""
    sensitivity: int = 8
    fakeout: float = 0.2
    range_filt: float = 0.2
    atr_period: int = 14


@dataclass(frozen=True)
class BarSignal:
    """Per-bar signal output, aligned 1:1 with the input bars."""
    buy: bool
    sell: bool
    adx: float
    body_atr_ratio: float


@dataclass(frozen=True)
class Signal:
    """The latest bar's decision for the runtime: side is 'buy', 'sell' or None."""
    side: Optional[str]
    adx: float
    body_atr_ratio: float


# --------------------------------------------------------------------------
# Indicator primitives (TradingView-matching, NaN/None-aware seeding)
# --------------------------------------------------------------------------
def _seed_index(vals: Sequence[Optional[float]], length: int) -> int:
    """Index of the last bar of the first `length` consecutive non-None values.

    Returns -1 if the series never has `length` consecutive valid values. This
    mirrors the engine's "SMA of the first `length` valid values" seeding.
    """
    count = 0
    for i, v in enumerate(vals):
        if v is not None:
            count += 1
            if count == length:
                return i
        else:
            count = 0
    return -1


def ema(vals: Sequence[Optional[float]], length: int) -> list[Optional[float]]:
    """EMA matching `ta.ema()`: multiplier 2/(L+1), seeded by the SMA of the
    first `length` valid values. Warm-up positions are None."""
    if length <= 0:
        raise ValueError(f"length must be positive, got {length}")
    out: list[Optional[float]] = [None] * len(vals)
    seed_end = _seed_index(vals, length)
    if seed_end < 0:
        return out
    seed_start = seed_end - length + 1
    prev = sum(vals[seed_start : seed_end + 1]) / length  # type: ignore[arg-type]
    out[seed_end] = prev
    mult = 2.0 / (length + 1)
    for i in range(seed_end + 1, len(vals)):
        x = vals[i]
        if x is None:
            out[i] = prev
            continue
        prev = (x - prev) * mult + prev
        out[i] = prev
    return out


def smma(vals: Sequence[Optional[float]], length: int) -> list[Optional[float]]:
    """Wilder's SMMA/RMA matching `ta.rma()`: smma[i] = (smma[i-1]*(L-1)+x)/L,
    seeded by the SMA of the first `length` valid values."""
    if length <= 0:
        raise ValueError(f"length must be positive, got {length}")
    out: list[Optional[float]] = [None] * len(vals)
    seed_end = _seed_index(vals, length)
    if seed_end < 0:
        return out
    seed_start = seed_end - length + 1
    prev = sum(vals[seed_start : seed_end + 1]) / length  # type: ignore[arg-type]
    out[seed_end] = prev
    for i in range(seed_end + 1, len(vals)):
        x = vals[i]
        if x is None:
            out[i] = prev
            continue
        prev = (prev * (length - 1) + x) / length
        out[i] = prev
    return out


def heikin_ashi(bars: Sequence[Bar]) -> tuple[list[float], list[float]]:
    """Return (ha_open, ha_close). ha_open seeds from (open+close)/2 then
    recurses as the mean of the previous HA open and close."""
    n = len(bars)
    ha_close = [0.0] * n
    ha_open = [0.0] * n
    for i, b in enumerate(bars):
        ha_close[i] = (b.open + b.high + b.low + b.close) / 4.0
        if i == 0:
            ha_open[i] = (b.open + b.close) / 2.0
        else:
            ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0
    return ha_open, ha_close


def _true_range(bars: Sequence[Bar]) -> list[float]:
    tr = [0.0] * len(bars)
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
    return tr


def atr(bars: Sequence[Bar], length: int) -> list[Optional[float]]:
    """ATR = Wilder SMMA of true range."""
    return smma(_true_range(bars), length)


def adx(bars: Sequence[Bar], length: int) -> list[Optional[float]]:
    """ADX matching the engine: directional movement → SMMA → DX → SMMA(DX)."""
    n = len(bars)
    dm_p = [0.0] * n
    dm_m = [0.0] * n
    for i in range(1, n):
        up = bars[i].high - bars[i - 1].high
        dn = bars[i - 1].low - bars[i].low
        dm_p[i] = up if (up > dn and up > 0) else 0.0
        dm_m[i] = dn if (dn > up and dn > 0) else 0.0
    sdm_p = smma(dm_p, length)
    sdm_m = smma(dm_m, length)
    s_tr = smma(_true_range(bars), length)
    dx = [0.0] * n
    for i in range(n):
        tr_i, p_i, m_i = s_tr[i], sdm_p[i], sdm_m[i]
        if tr_i and p_i is not None and m_i is not None:
            dip = p_i / tr_i * 100.0
            dim = m_i / tr_i * 100.0
            denom = dip + dim
            if denom != 0:
                dx[i] = abs(dip - dim) / denom * 100.0
    return smma(dx, length)


# --------------------------------------------------------------------------
# Signal generation
# --------------------------------------------------------------------------
def generate_signal_series(
    bars: Sequence[Bar], params: SignalParams = SignalParams(),
) -> list[BarSignal]:
    """Compute the HA-V3 buy/sell signal for every bar (aligned 1:1).

    buy = HA-trend turns up (smoothed cross) AND body > fakeout·ATR AND
          ADX > 20·range_filt. sell is the symmetric down-cross.
    """
    n = len(bars)
    if n == 0:
        return []

    _, ha_close = heikin_ashi(bars)
    smooth_len = 16 - params.sensitivity
    smoothed = ha_close if smooth_len <= 1 else [
        v for v in ema(ha_close, smooth_len)
    ]

    atr14 = atr(bars, params.atr_period)
    adx14 = adx(bars, params.atr_period)
    thresh = 20.0 * params.range_filt

    ha_bull = [False] * n
    ha_bear = [False] * n
    for i in range(1, n):
        a, b = smoothed[i], smoothed[i - 1]
        if a is not None and b is not None:
            ha_bull[i] = a > b
            ha_bear[i] = a < b

    out: list[BarSignal] = []
    for i in range(n):
        body = abs(bars[i].close - bars[i].open)
        a14 = atr14[i]
        fakeout_pass = True if params.fakeout <= 0 or a14 is None else body > params.fakeout * a14
        adx_val = adx14[i]
        range_pass = True if params.range_filt <= 0 or adx_val is None else adx_val > thresh
        body_ratio = (body / a14) if (a14 is not None and a14 > 0) else 0.0

        buy = sell = False
        if i >= 1:
            buy = ha_bull[i] and not ha_bull[i - 1] and fakeout_pass and range_pass
            sell = ha_bear[i] and not ha_bear[i - 1] and fakeout_pass and range_pass

        out.append(BarSignal(
            buy=buy, sell=sell,
            adx=adx_val if adx_val is not None else 0.0,
            body_atr_ratio=body_ratio,
        ))
    return out


def latest_signal(
    bars: Sequence[Bar], params: SignalParams = SignalParams(),
) -> Signal:
    """Return the decision for the most recent bar in `bars`.

    The runtime passes a rolling window (most recent bar last). Feed enough
    warm-up (≥150 bars) so the recursive EMA/SMMA seeds wash out and the result
    matches the engine.
    """
    series = generate_signal_series(bars, params)
    if not series:
        return Signal(side=None, adx=0.0, body_atr_ratio=0.0)
    last = series[-1]
    side = "buy" if last.buy else "sell" if last.sell else None
    return Signal(side=side, adx=last.adx, body_atr_ratio=last.body_atr_ratio)
