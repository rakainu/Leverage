"""Pure signal validation logic — no I/O, no side effects.

Three phases (see docs/SIGNAL_LIFECYCLE.md):

  1. check_invalidation(snapshot, ctx, cfg)
     -> returns a reason code string if the pending signal should be killed,
        else None.

  2. check_retest(snapshot, ctx, cfg)
     -> returns True if price has touched the EMA in the correct direction
        (within the overshoot cap). False = keep waiting.

  3. check_revalidation(snapshot, ctx, cfg)
     -> called after a retest is detected. Returns a reason code string if
        the setup has broken between signal and retest (so entry must be
        skipped), else None.

All three functions are pure; feed them a snapshot + context + config and
they produce a verdict. Reason codes are exactly as specified in the
project spec, so they can be logged / searched in operational logs.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence


# ------------------------------ data classes ------------------------------

@dataclass(frozen=True)
class SignalSnapshot:
    """Immutable snapshot captured at webhook receipt."""
    symbol: str
    action: str                       # "buy" or "sell"
    signal_price: float
    signal_candle_high: float
    signal_candle_low: float
    signal_ema_value: float
    signal_ema_slope: float
    signal_atr: Optional[float]
    signal_bar_ts: int                # unix ms of the bar in which signal fired
    signal_timeframe: str
    received_at: datetime             # UTC
    max_age_seconds: int
    max_bars: int


@dataclass(frozen=True)
class ValidationConfig:
    """Strategy-level toggles + thresholds. Stable per session."""
    ema_length: int
    max_signal_age_seconds: int
    max_signal_bars: int
    max_price_drift_percent: float    # e.g. 0.35 → 0.35%
    use_atr_drift_filter: bool
    max_price_drift_atr: float        # e.g. 0.5 → 0.5 × ATR
    require_retest_confirmation_candle: bool
    cancel_on_slope_flip: bool
    bar_seconds: int                  # duration of one bar on the signal timeframe
    ema_retest_max_overshoot_pct: float   # overshoot band around EMA, %


@dataclass(frozen=True)
class MarketContext:
    """Live market state at the current poller tick."""
    now: datetime                     # UTC
    last_price: float
    current_ema: float
    current_ema_slope: float
    latest_bar_ts: int                # unix ms of the most recent bar
    last_closed_bar_close: float      # close of the most recently closed bar
    closes_since_signal: Sequence[float]  # list of closed-bar closes AFTER the signal bar
    position_open: bool


# ------------------------------ helpers ------------------------------

def _price_drift_pct(signal_price: float, current: float) -> float:
    if signal_price <= 0:
        return 0.0
    return abs(current - signal_price) / signal_price * 100.0


def _bars_elapsed(signal_bar_ts: int, latest_bar_ts: int, bar_seconds: int) -> int:
    if bar_seconds <= 0:
        return 0
    delta_ms = max(0, latest_bar_ts - signal_bar_ts)
    return delta_ms // (bar_seconds * 1000)


def _is_long(snap: SignalSnapshot) -> bool:
    return snap.action == "buy"


# ------------------------------ phase 1: invalidation ------------------------------

def check_invalidation(
    snap: SignalSnapshot, ctx: MarketContext, cfg: ValidationConfig,
) -> Optional[str]:
    """Return a reason code if the pending signal must be killed, else None."""
    # Order: cheapest / most conclusive first.
    if ctx.position_open:
        return "invalidated_position_open"

    age_seconds = (ctx.now - snap.received_at).total_seconds()
    if age_seconds > cfg.max_signal_age_seconds:
        return "expired_time_limit"

    if _bars_elapsed(snap.signal_bar_ts, ctx.latest_bar_ts, cfg.bar_seconds) \
            > cfg.max_signal_bars:
        return "expired_bar_limit"

    # Structure break: any closed bar after the signal violates the signal candle extreme.
    if _is_long(snap):
        if any(c < snap.signal_candle_low for c in ctx.closes_since_signal):
            return "invalidated_structure_break"
    else:
        if any(c > snap.signal_candle_high for c in ctx.closes_since_signal):
            return "invalidated_structure_break"

    # Slope flip — only a REAL flip kills the pending. Pro V3 often fires
    # buy at troughs / sell at peaks, when the EMA has not yet turned; an
    # absolute slope check would kill every peak-sell before retest. We
    # require that the slope was in our favor at signal time and has since
    # flipped against the trade. The stricter absolute-slope check still runs
    # at revalidation (check_revalidation) per the signal-lifecycle spec.
    if cfg.cancel_on_slope_flip:
        if _is_long(snap) and snap.signal_ema_slope > 0 and ctx.current_ema_slope < 0:
            return "invalidated_slope_flip"
        if not _is_long(snap) and snap.signal_ema_slope < 0 and ctx.current_ema_slope > 0:
            return "invalidated_slope_flip"

    # Price drift (percent)
    drift_pct = _price_drift_pct(snap.signal_price, ctx.last_price)
    if drift_pct > cfg.max_price_drift_percent:
        return "invalidated_price_drift"

    # Price drift (ATR)
    if cfg.use_atr_drift_filter and snap.signal_atr and snap.signal_atr > 0:
        threshold = cfg.max_price_drift_atr * snap.signal_atr
        if abs(ctx.last_price - snap.signal_price) > threshold:
            return "invalidated_price_drift"

    return None


# ------------------------------ phase 2: retest detection ------------------------------

def check_retest(
    snap: SignalSnapshot, ctx: MarketContext, cfg: ValidationConfig,
) -> bool:
    """True if price has touched the EMA in the expected direction, within the
    overshoot cap. False = keep waiting (not yet retested, or blew past too far)."""
    if ctx.current_ema <= 0:
        return False
    overshoot = ctx.current_ema * (cfg.ema_retest_max_overshoot_pct / 100.0)

    if _is_long(snap):
        # Long retest: price pulled back to or below EMA but not deeper than overshoot.
        return (ctx.current_ema - overshoot) <= ctx.last_price <= ctx.current_ema
    else:
        # Short retest: price rallied to or above EMA but not higher than overshoot.
        return ctx.current_ema <= ctx.last_price <= (ctx.current_ema + overshoot)


# ------------------------------ phase 3: revalidation ------------------------------

def check_revalidation(
    snap: SignalSnapshot, ctx: MarketContext, cfg: ValidationConfig,
) -> Optional[str]:
    """Called when a retest is detected. Returns a reason code if the setup has
    broken since the signal fired, else None (= safe to enter)."""
    # Re-run structure and slope with latest data (retest-specific reason codes).
    if _is_long(snap):
        if any(c < snap.signal_candle_low for c in ctx.closes_since_signal):
            return "retest_failed_structure"
    else:
        if any(c > snap.signal_candle_high for c in ctx.closes_since_signal):
            return "retest_failed_structure"

    if cfg.cancel_on_slope_flip:
        if _is_long(snap) and ctx.current_ema_slope < 0:
            return "retest_failed_slope"
        if not _is_long(snap) and ctx.current_ema_slope > 0:
            return "retest_failed_slope"

    # Drift recheck
    drift_pct = _price_drift_pct(snap.signal_price, ctx.last_price)
    if drift_pct > cfg.max_price_drift_percent:
        return "retest_failed_drift"
    if cfg.use_atr_drift_filter and snap.signal_atr and snap.signal_atr > 0:
        threshold = cfg.max_price_drift_atr * snap.signal_atr
        if abs(ctx.last_price - snap.signal_price) > threshold:
            return "retest_failed_drift"

    # Confirmation candle: last CLOSED bar must close in our favor relative to EMA.
    if cfg.require_retest_confirmation_candle:
        if _is_long(snap):
            # Bullish rejection: close came back above EMA.
            if ctx.last_closed_bar_close <= ctx.current_ema:
                return "retest_failed_confirmation"
        else:
            # Bearish rejection: close came back below EMA.
            if ctx.last_closed_bar_close >= ctx.current_ema:
                return "retest_failed_confirmation"

    return None


__all__ = [
    "SignalSnapshot", "ValidationConfig", "MarketContext",
    "check_invalidation", "check_retest", "check_revalidation",
]
