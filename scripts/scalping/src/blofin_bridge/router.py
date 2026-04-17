"""Action dispatch: webhook payload -> correct handler.

For buy/sell we now capture a rich signal snapshot so the poller can later
revalidate the setup before firing an entry. See signal_validator.py for the
validation logic and docs/SIGNAL_LIFECYCLE.md for the full flow.
"""
from __future__ import annotations
import logging
from typing import Any, Optional, TYPE_CHECKING

from .atr import compute_atr
from .blofin_client import BloFinClient
from .ema import compute_ema, compute_ema_slope
from .handlers.entry import handle_entry
from .handlers.reversal import handle_reversal
from .handlers.sl import handle_sl
from .state import Store

if TYPE_CHECKING:
    from .entry_gate import EntryGate

log = logging.getLogger(__name__)


class UnknownAction(ValueError):
    pass


VALID_ACTIONS = {
    "buy", "sell", "sl",
    "reversal_buy", "reversal_sell",
}


def _capture_snapshot(
    *, symbol: str, sym_cfg: dict[str, Any], blofin: BloFinClient,
    payload_price: Optional[float],
    payload_high: Optional[float],
    payload_low: Optional[float],
    payload_timeframe: Optional[str],
) -> dict[str, Any]:
    """Gather the per-signal snapshot at webhook-receipt time.

    Uses payload values where present, falls back to live market data
    for anything missing. Any field we genuinely can't compute ends up
    as None and will be treated defensively by the validator.
    """
    ema_period = int(sym_cfg.get("ema_retest_period", 9))
    ema_slope_lookback = int(sym_cfg.get("ema_slope_lookback", 1))
    atr_period = int(sym_cfg.get("atr_length", 14))
    timeframe = payload_timeframe or sym_cfg.get("ema_retest_timeframe", "5m")

    # Always fetch the last price so we have a floor for signal_price.
    try:
        market_last = blofin.fetch_last_price(symbol)
    except Exception as exc:
        log.warning("snapshot: fetch_last_price failed for %s: %s", symbol, exc)
        market_last = None

    signal_price = payload_price if payload_price is not None else market_last

    # Bars: need enough for EMA + slope + ATR.
    needed = max(ema_period + ema_slope_lookback + 2, atr_period + 2)
    try:
        bars = blofin.fetch_recent_ohlcv(symbol, timeframe=timeframe, limit=needed + 5)
    except Exception as exc:
        log.warning("snapshot: fetch_recent_ohlcv failed for %s: %s", symbol, exc)
        bars = []

    closes = [float(b[4]) for b in bars]

    signal_ema_value: Optional[float] = None
    signal_ema_slope: Optional[float] = None
    if len(closes) >= ema_period + ema_slope_lookback:
        try:
            signal_ema_value = compute_ema(closes, ema_period)
            signal_ema_slope = compute_ema_slope(
                closes, period=ema_period, lookback=ema_slope_lookback,
            )
        except Exception as exc:
            log.warning("snapshot: EMA/slope compute failed for %s: %s", symbol, exc)

    signal_atr: Optional[float] = None
    if len(bars) >= atr_period + 1:
        try:
            signal_atr = compute_atr(bars, period=atr_period)
        except Exception as exc:
            log.warning("snapshot: ATR compute failed for %s: %s", symbol, exc)

    last_bar = bars[-1] if bars else None
    signal_candle_high = payload_high
    signal_candle_low = payload_low
    signal_bar_ts: Optional[int] = None
    if last_bar is not None:
        if signal_candle_high is None:
            signal_candle_high = float(last_bar[2])
        if signal_candle_low is None:
            signal_candle_low = float(last_bar[3])
        signal_bar_ts = int(last_bar[0])

    return dict(
        signal_price=signal_price,
        signal_timeframe=timeframe,
        signal_candle_high=signal_candle_high,
        signal_candle_low=signal_candle_low,
        signal_ema_value=signal_ema_value,
        signal_ema_slope=signal_ema_slope,
        signal_atr=signal_atr,
        signal_bar_ts=signal_bar_ts,
    )


def dispatch(
    *,
    action: str,
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    symbol_configs: dict[str, dict[str, Any]],
    gate: "EntryGate | None" = None,
    # --- optional payload snapshot fields (TV webhook) ---
    payload_price: Optional[float] = None,
    payload_high: Optional[float] = None,
    payload_low: Optional[float] = None,
    payload_timeframe: Optional[str] = None,
) -> dict[str, Any]:
    if action not in VALID_ACTIONS:
        raise UnknownAction(action)

    sym_cfg = symbol_configs.get(symbol)
    if sym_cfg is None:
        return {"opened": False, "handled": False,
                "reason": f"unknown symbol {symbol}"}
    if not sym_cfg.get("enabled", False):
        return {"opened": False, "handled": False,
                "reason": f"symbol {symbol} disabled in config"}

    # Operator-initiated pause: block new entries but always allow SL (close-safe).
    entry_actions = ("buy", "sell", "reversal_buy", "reversal_sell")
    if gate is not None and action in entry_actions and gate.is_paused(symbol):
        return {
            "paused": True,
            "symbol": symbol,
            "action": action,
            "reason": "entries paused by operator",
        }

    if action in ("buy", "sell"):
        # Supersession rules: per the signal-lifecycle spec, a new buy/sell
        # only cancels an existing pending when direction OPPOSES it
        # ("opposite signal arrives" invalidation). Same-direction resignals
        # (Pro V3 reaffirming the trend every few bars) keep the first setup
        # intact — otherwise each reaffirmation would reset the retest clock
        # and nothing ever enters in a trending market.
        existing = next(
            (s for s in store.list_pending_signals() if s["symbol"] == symbol),
            None,
        )
        if existing is not None:
            if existing["action"] == action:
                log.info(
                    "signal_duplicate_ignored id=%d %s %s (pending since %s)",
                    existing["id"], action, symbol, existing.get("created_at"),
                )
                return {
                    "pending": True,
                    "duplicate": True,
                    "signal_id": existing["id"],
                    "action": action,
                    "signal_price": existing.get("signal_price"),
                    "reason": "same-direction signal already pending",
                }
            store.invalidate_pending_signal(
                existing["id"], reason="invalidated_opposite_signal",
            )
            log.info(
                "pending_invalidated id=%d %s %s reason=invalidated_opposite_signal",
                existing["id"], existing["action"], symbol,
            )

        snap = _capture_snapshot(
            symbol=symbol, sym_cfg=sym_cfg, blofin=blofin,
            payload_price=payload_price, payload_high=payload_high,
            payload_low=payload_low, payload_timeframe=payload_timeframe,
        )

        max_age = int(sym_cfg.get("max_signal_age_seconds", 1800))
        max_bars = int(sym_cfg.get("max_signal_bars", 6))
        timeout_minutes = max(1, (max_age + 59) // 60)  # ceil to minutes for expires_at

        sig_id = store.create_pending_signal(
            symbol=symbol, action=action,
            signal_price=snap["signal_price"] or 0.0,
            timeout_minutes=timeout_minutes,
            signal_timeframe=snap["signal_timeframe"],
            signal_candle_high=snap["signal_candle_high"],
            signal_candle_low=snap["signal_candle_low"],
            signal_ema_value=snap["signal_ema_value"],
            signal_ema_slope=snap["signal_ema_slope"],
            signal_atr=snap["signal_atr"],
            signal_bar_ts=snap["signal_bar_ts"],
            max_age_seconds=max_age,
            max_bars=max_bars,
        )
        log.info(
            "signal_created id=%d %s %s price=%s ema=%s slope=%s atr=%s "
            "high=%s low=%s tf=%s bar_ts=%s",
            sig_id, action, symbol,
            snap["signal_price"], snap["signal_ema_value"], snap["signal_ema_slope"],
            snap["signal_atr"], snap["signal_candle_high"], snap["signal_candle_low"],
            snap["signal_timeframe"], snap["signal_bar_ts"],
        )
        return {
            "pending": True,
            "signal_id": sig_id,
            "action": action,
            "signal_price": snap["signal_price"],
            "reason": "waiting for EMA retest",
        }

    if action == "sl":
        pending_cancelled = store.cancel_pending_signals_for_symbol(symbol)
        sl_result = handle_sl(
            symbol=symbol, store=store, blofin=blofin,
            margin_usdt=sym_cfg["margin_usdt"], leverage=sym_cfg["leverage"],
        )
        sl_result["pending_cancelled"] = pending_cancelled
        return sl_result

    if action.startswith("reversal_"):
        new_action = action.split("_", 1)[1]
        store.cancel_pending_signals_for_symbol(symbol)
        closed = handle_sl(
            symbol=symbol, store=store, blofin=blofin,
            margin_usdt=sym_cfg["margin_usdt"], leverage=sym_cfg["leverage"],
        )
        snap = _capture_snapshot(
            symbol=symbol, sym_cfg=sym_cfg, blofin=blofin,
            payload_price=payload_price, payload_high=payload_high,
            payload_low=payload_low, payload_timeframe=payload_timeframe,
        )
        max_age = int(sym_cfg.get("max_signal_age_seconds", 1800))
        max_bars = int(sym_cfg.get("max_signal_bars", 6))
        timeout_minutes = max(1, (max_age + 59) // 60)
        sig_id = store.create_pending_signal(
            symbol=symbol, action=new_action,
            signal_price=snap["signal_price"] or 0.0,
            timeout_minutes=timeout_minutes,
            signal_timeframe=snap["signal_timeframe"],
            signal_candle_high=snap["signal_candle_high"],
            signal_candle_low=snap["signal_candle_low"],
            signal_ema_value=snap["signal_ema_value"],
            signal_ema_slope=snap["signal_ema_slope"],
            signal_atr=snap["signal_atr"],
            signal_bar_ts=snap["signal_bar_ts"],
            max_age_seconds=max_age,
            max_bars=max_bars,
        )
        log.info(
            "signal_created_reversal id=%d %s %s price=%s",
            sig_id, new_action, symbol, snap["signal_price"],
        )
        return {
            "closed_previous": closed.get("closed", False),
            "pending_new": True,
            "signal_id": sig_id,
            "action": new_action,
            "signal_price": snap["signal_price"],
            "close_result": closed,
        }

    raise UnknownAction(action)  # unreachable
