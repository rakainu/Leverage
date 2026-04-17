"""Background position poller with trailing stop logic.

Every poll cycle (default 2s):
  1. Drift check: archive positions gone from BloFin.
  2. For each open position, fetch current price and compute unrealized P&L.
  3. SL state machine: 0→1→2→3→4 (see _process_position).
  4. SL never moves backward (only tightens).
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .atr import compute_atr
from .blofin_client import BloFinClient
from .ema import compute_ema, compute_ema_slope
from .entry_gate import EntryGate
from .handlers.entry import handle_entry, _dollar_to_price_distance
from .notify import (
    Notifier, format_entry, format_trail_activated, format_trail_update,
    format_pending_filled, format_pending_expired,
)
from .signal_validator import (
    MarketContext, SignalSnapshot, ValidationConfig,
    check_invalidation, check_retest, check_revalidation,
)
from .state import Store


# Timeframe string → seconds, for bar-based limits.
_TIMEFRAME_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
    # Some TV servers send just a number.
    "1": 60, "3": 180, "5": 300, "15": 900, "30": 1800, "60": 3600,
}


def _timeframe_to_seconds(tf: str | None, default: int = 300) -> int:
    if not tf:
        return default
    return _TIMEFRAME_SECONDS.get(tf.strip().lower(), default)

log = logging.getLogger(__name__)


class PositionPoller:
    """Polls BloFin and manages trailing stops."""

    def __init__(
        self,
        *,
        store: Store,
        blofin: BloFinClient,
        interval_seconds: int,
        # Trail config (injected from settings at startup)
        breakeven_usdt: float = 15.0,
        lock_profit_activate_usdt: float = 20.0,
        lock_profit_usdt: float = 15.0,
        trail_activate_usdt: float = 25.0,
        trail_start_usdt: float = 30.0,
        trail_distance_usdt: float = 10.0,
        sl_loss_usdt: float = 13.0,
        margin_usdt: float = 100.0,
        leverage: float = 30.0,
        notifier: Optional[Notifier] = None,
        # EMA retest config
        ema_retest_period: int = 9,
        ema_retest_timeframe: str = "5m",
        ema_retest_max_overshoot_pct: float = 0.2,
        # --- Signal revalidation config (2026-04-16) ---
        max_signal_age_seconds: int = 1800,
        max_signal_bars: int = 6,
        max_price_drift_percent: float = 0.35,
        use_atr_drift_filter: bool = True,
        max_price_drift_atr: float = 0.5,
        require_retest_confirmation_candle: bool = True,
        cancel_on_slope_flip: bool = True,
        atr_length: int = 14,
        ema_slope_lookback: int = 1,
        # Symbol configs for executing pending entries
        symbol_configs: Optional[dict[str, dict[str, Any]]] = None,
        # Operator-initiated per-symbol pause
        gate: Optional[EntryGate] = None,
    ) -> None:
        self.store = store
        self.blofin = blofin
        self.interval_seconds = interval_seconds
        self.breakeven_usdt = breakeven_usdt
        self.lock_profit_activate_usdt = lock_profit_activate_usdt
        self.lock_profit_usdt = lock_profit_usdt
        self.trail_activate_usdt = trail_activate_usdt
        self.trail_start_usdt = trail_start_usdt
        self.trail_distance_usdt = trail_distance_usdt
        self.sl_loss_usdt = sl_loss_usdt
        self.margin_usdt = margin_usdt
        self.leverage = leverage
        self.notifier = notifier
        self.ema_retest_period = ema_retest_period
        self.ema_retest_timeframe = ema_retest_timeframe
        self.ema_retest_max_overshoot_pct = ema_retest_max_overshoot_pct
        # Revalidation config — kept as a single immutable ValidationConfig
        # instance; per-signal bar_seconds is injected when evaluating.
        self.max_signal_age_seconds = max_signal_age_seconds
        self.max_signal_bars = max_signal_bars
        self.max_price_drift_percent = max_price_drift_percent
        self.use_atr_drift_filter = use_atr_drift_filter
        self.max_price_drift_atr = max_price_drift_atr
        self.require_retest_confirmation_candle = require_retest_confirmation_candle
        self.cancel_on_slope_flip = cancel_on_slope_flip
        self.atr_length = atr_length
        self.ema_slope_lookback = ema_slope_lookback
        self.symbol_configs = symbol_configs or {}
        self.gate = gate
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

    async def poll_once(self) -> None:
        # --- Check pending signals for EMA retest ---
        try:
            self._process_pending_signals()
        except Exception:
            log.exception("failed to process pending signals")

        # --- Check open positions ---
        try:
            positions = self.store.list_open_positions()
        except Exception:
            log.exception("failed to list open positions")
            return

        if not positions:
            return

        # Fetch BloFin positions ONCE per cycle for drift detection.
        blofin_open_symbols: Optional[set[str]] = None
        try:
            blofin_positions = self.blofin.fetch_positions()
            blofin_open_symbols = set()
            for p in blofin_positions:
                info = p.get("info") or {}
                inst_id = info.get("instId") or p.get("symbol", "").replace("/", "-").split(":")[0]
                if float(p.get("contracts") or 0) != 0:
                    blofin_open_symbols.add(inst_id)
        except Exception as exc:
            log.warning(
                "fetch_positions failed in poller, skipping drift check: %s", exc,
            )

        for pos in positions:
            try:
                self._process_position(pos, blofin_open_symbols=blofin_open_symbols)
            except Exception:
                log.exception("poller failed for position id=%s", pos.id)

    def _process_pending_signals(self) -> None:
        """Three-phase signal pipeline: invalidate → retest → revalidate → enter.

        Every phase emits structured log lines keyed on a reason code so ops
        can grep for `invalidated_*`, `expired_*`, `retest_failed_*`, or
        `executed_*` events.
        """
        signals = self.store.list_pending_signals()
        now = datetime.now(timezone.utc)

        for sig in signals:
            try:
                self._process_single_pending_signal(sig, now)
            except Exception:
                log.exception("Failed processing pending signal %s", sig.get("id"))

    def _process_single_pending_signal(self, sig: dict[str, Any], now: datetime) -> None:
        sig_id = sig["id"]
        symbol = sig["symbol"]
        action = sig["action"]

        # --- PRE-PHASE: honor stored expires_at (hard wall-clock backup) ---
        # This is evaluated BEFORE any market-data fetch so a single broken
        # exchange call can't keep a zombie signal alive past its timeout.
        try:
            expires_at = datetime.fromisoformat(sig["expires_at"])
            if now >= expires_at:
                self.store.expire_pending_signal(sig_id, reason="expired_time_limit")
                log.info(
                    "pending_expired id=%d %s %s reason=expired_time_limit",
                    sig_id, action, symbol,
                )
                if self.notifier:
                    try:
                        self.notifier.send(format_pending_expired(action, symbol))
                    except Exception:
                        log.exception("notifier send failed")
                return
        except (ValueError, KeyError):
            pass  # malformed expires_at — let the main pipeline handle

        # Operator pause: drop without firing.
        if self.gate is not None and self.gate.is_paused(symbol):
            self.store.invalidate_pending_signal(sig_id, reason="invalidated_operator_pause")
            log.info("pending_invalidated id=%d %s %s reason=invalidated_operator_pause",
                     sig_id, action, symbol)
            return

        sym_cfg = self.symbol_configs.get(symbol) or {}
        timeframe = sig.get("signal_timeframe") or sym_cfg.get(
            "ema_retest_timeframe", self.ema_retest_timeframe,
        )
        bar_seconds = _timeframe_to_seconds(timeframe, default=300)

        # --- fetch market context ---
        try:
            last_price = self.blofin.fetch_last_price(symbol)
        except Exception as exc:
            log.warning("pending_skip id=%d %s fetch_last_price failed: %s",
                        sig_id, symbol, exc)
            return

        try:
            needed = max(self.ema_retest_period + self.ema_slope_lookback + 2,
                         self.atr_length + 2, 30)
            bars = self.blofin.fetch_recent_ohlcv(
                symbol, timeframe=timeframe, limit=needed,
            )
        except Exception as exc:
            log.warning("pending_skip id=%d %s fetch_recent_ohlcv failed: %s",
                        sig_id, symbol, exc)
            return

        if not bars:
            log.warning("pending_skip id=%d %s no bars returned", sig_id, symbol)
            return

        closes = [float(b[4]) for b in bars]
        try:
            current_ema = compute_ema(closes, self.ema_retest_period)
            current_ema_slope = compute_ema_slope(
                closes, period=self.ema_retest_period,
                lookback=self.ema_slope_lookback,
            )
        except ValueError:
            log.warning("pending_skip id=%d %s insufficient bars for EMA/slope",
                        sig_id, symbol)
            return

        latest_bar_ts = int(bars[-1][0])
        last_closed_bar_close = float(bars[-1][4])

        signal_bar_ts = sig.get("signal_bar_ts")
        if signal_bar_ts is None:
            # Legacy signal without snapshot — fall back to received_at as the anchor.
            signal_bar_ts = latest_bar_ts  # effectively disables bar-limit
        closes_since_signal = [
            float(b[4]) for b in bars if int(b[0]) > int(signal_bar_ts)
        ]

        position_open = self.store.get_open_position(symbol) is not None

        # --- build snapshot from stored row (fill in sane defaults for legacy rows) ---
        snap = SignalSnapshot(
            symbol=symbol,
            action=action,
            signal_price=float(sig.get("signal_price") or last_price),
            signal_candle_high=float(sig.get("signal_candle_high") or float("inf")),
            signal_candle_low=float(sig.get("signal_candle_low") or float("-inf")),
            signal_ema_value=float(sig.get("signal_ema_value") or current_ema),
            signal_ema_slope=float(sig.get("signal_ema_slope") or 0.0),
            signal_atr=sig.get("signal_atr"),
            signal_bar_ts=int(signal_bar_ts),
            signal_timeframe=timeframe,
            received_at=datetime.fromisoformat(sig["created_at"]),
            max_age_seconds=int(sig.get("max_age_seconds") or self.max_signal_age_seconds),
            max_bars=int(sig.get("max_bars") or self.max_signal_bars),
        )

        cfg = ValidationConfig(
            ema_length=self.ema_retest_period,
            max_signal_age_seconds=snap.max_age_seconds,
            max_signal_bars=snap.max_bars,
            max_price_drift_percent=self.max_price_drift_percent,
            use_atr_drift_filter=self.use_atr_drift_filter,
            max_price_drift_atr=self.max_price_drift_atr,
            require_retest_confirmation_candle=self.require_retest_confirmation_candle,
            cancel_on_slope_flip=self.cancel_on_slope_flip,
            bar_seconds=bar_seconds,
            ema_retest_max_overshoot_pct=self.ema_retest_max_overshoot_pct,
        )

        ctx = MarketContext(
            now=now,
            last_price=last_price,
            current_ema=current_ema,
            current_ema_slope=current_ema_slope,
            latest_bar_ts=latest_bar_ts,
            last_closed_bar_close=last_closed_bar_close,
            closes_since_signal=closes_since_signal,
            position_open=position_open,
        )

        # ---- PHASE 1: invalidation ----
        reason = check_invalidation(snap, ctx, cfg)
        if reason is not None:
            if reason.startswith("expired_"):
                self.store.expire_pending_signal(sig_id, reason=reason)
            else:
                self.store.invalidate_pending_signal(sig_id, reason=reason)
            log.info(
                "pending_invalidated id=%d %s %s reason=%s price=%.6f ema=%.6f slope=%.6f",
                sig_id, action, symbol, reason, last_price, current_ema, current_ema_slope,
            )
            if self.notifier and reason.startswith("expired_"):
                try:
                    self.notifier.send(format_pending_expired(action, symbol))
                except Exception:
                    log.exception("notifier send failed")
            return

        # ---- PHASE 2: retest detection ----
        if not check_retest(snap, ctx, cfg):
            # No retest yet — keep waiting silently (next tick).
            return

        log.info(
            "pending_retest_seen id=%d %s %s price=%.6f ema=%.6f",
            sig_id, action, symbol, last_price, current_ema,
        )

        # ---- PHASE 3: revalidation ----
        revalidation_reason = check_revalidation(snap, ctx, cfg)
        if revalidation_reason is not None:
            log.info(
                "pending_revalidation_failed id=%d %s %s reason=%s close=%.6f ema=%.6f slope=%.6f",
                sig_id, action, symbol, revalidation_reason,
                last_closed_bar_close, current_ema, current_ema_slope,
            )
            # Stay pending — a later bar might satisfy the confirmation.
            # But hard failures (structure/slope) will reappear in phase 1 next tick.
            return

        log.info(
            "pending_revalidation_passed id=%d %s %s price=%.6f ema=%.6f slope=%.6f",
            sig_id, action, symbol, last_price, current_ema, current_ema_slope,
        )

        # ---- EXECUTE ----
        if sym_cfg is None or not sym_cfg:
            log.warning("pending_skip id=%d %s no sym_cfg", sig_id, symbol)
            return

        result = handle_entry(
            action=action, symbol=symbol,
            store=self.store, blofin=self.blofin,
            margin_usdt=sym_cfg["margin_usdt"],
            leverage=sym_cfg["leverage"],
            margin_mode=sym_cfg["margin_mode"],
            sl_policy_name=sym_cfg["sl_policy"],
            sl_loss_usdt=sym_cfg["sl_loss_usdt"],
            trail_activate_usdt=sym_cfg["trail_activate_usdt"],
            trail_distance_usdt=sym_cfg["trail_distance_usdt"],
            tp_limit_margin_pct=sym_cfg["tp_limit_margin_pct"],
        )

        if result.get("opened"):
            self.store.fill_pending_signal(sig_id, last_price)
            log.info(
                "executed_retest_validated id=%d %s %s entry=%.6f size=%s",
                sig_id, action, symbol, result["entry_price"], result.get("size"),
            )
            if self.notifier:
                self.notifier.send(format_pending_filled(
                    action, symbol, result["entry_price"], snap.signal_price,
                ))
                result["symbol"] = symbol
                self.notifier.send(format_entry(result))
        else:
            log.warning(
                "pending_entry_failed id=%d %s %s reason=%s",
                sig_id, action, symbol, result.get("reason"),
            )

    def _archive_stale_position(self, pos) -> None:
        """Position is gone from BloFin. Cancel leftover orders, archive row."""
        log.warning(
            "Position id=%d (%s %s) gone from BloFin — archiving stale row",
            pos.id, pos.symbol, pos.side,
        )
        for oid in (pos.tp1_order_id, pos.tp2_order_id, pos.tp3_order_id):
            if not oid:
                continue
            try:
                self.blofin.cancel_order(oid, pos.symbol)
            except Exception:
                pass
        if pos.sl_order_id:
            try:
                self.blofin.cancel_tpsl(pos.symbol, pos.sl_order_id)
            except Exception:
                pass

        # Try to get last price for trade log
        exit_price = None
        try:
            exit_price = self.blofin.fetch_last_price(pos.symbol)
        except Exception:
            pass

        # Compute the initial SL price from entry + configured $ loss so we can
        # distinguish an SL fill (exit near SL price) from a true drift
        # (external close / manual / unknown).
        sl_price_distance = _dollar_to_price_distance(
            self.sl_loss_usdt, self.margin_usdt, self.leverage, pos.entry_price,
        )
        if pos.side == "long":
            initial_sl_price = pos.entry_price - sl_price_distance
        else:
            initial_sl_price = pos.entry_price + sl_price_distance

        if pos.trail_active:
            exit_reason = "trail_sl"
        elif exit_price is not None and abs(exit_price - initial_sl_price) / pos.entry_price <= 0.003:
            # Within 0.3% of the initial SL price — treat as SL hit.
            exit_reason = "sl"
        else:
            exit_reason = "drift"

        self.store.log_trade(
            position_id=pos.id, exit_price=exit_price, exit_reason=exit_reason,
            margin_usdt=self.margin_usdt, leverage=self.leverage,
            initial_sl=initial_sl_price, tp_ceiling=None,
        )
        self.store.close_position(pos.id, realized_pnl=None)

    def _compute_unrealized_pnl_usdt(self, pos, current_price: float) -> float:
        """Compute unrealized P&L in USDT for this position."""
        notional = self.margin_usdt * self.leverage
        if pos.side == "long":
            pct_move = (current_price - pos.entry_price) / pos.entry_price
        else:
            pct_move = (pos.entry_price - current_price) / pos.entry_price
        return pct_move * notional

    def _trail_distance_as_price(self, current_price: float) -> float:
        """Convert trail_distance_usdt to a price distance."""
        return _dollar_to_price_distance(
            self.trail_distance_usdt, self.margin_usdt,
            self.leverage, current_price,
        )

    def _process_position(
        self, pos, *, blofin_open_symbols: Optional[set[str]] = None,
    ) -> None:
        # Drift check
        if blofin_open_symbols is not None and pos.symbol not in blofin_open_symbols:
            self._archive_stale_position(pos)
            return

        # Fetch current price
        try:
            current_price = self.blofin.fetch_last_price(pos.symbol)
        except Exception as exc:
            log.warning("fetch_last_price failed for %s: %s", pos.symbol, exc)
            return

        pnl = self._compute_unrealized_pnl_usdt(pos, current_price)

        # --- Trail logic ---
        # trail_active: 0=inactive, 1=breakeven, 2=lock profit, 3=jumped/locked (dead zone), 4=trailing
        if pos.trail_active == 0:
            if pnl >= self.breakeven_usdt:
                log.info(
                    "Breakeven for %s (pos %d): pnl=$%.2f >= $%.2f",
                    pos.symbol, pos.id, pnl, self.breakeven_usdt,
                )
                self._move_to_breakeven(pos)
        elif pos.trail_active == 1:
            # Breakeven — waiting for lock_profit threshold
            if pnl >= self.lock_profit_activate_usdt:
                log.info(
                    "Lock profit for %s (pos %d): pnl=$%.2f >= $%.2f, locking $%.0f",
                    pos.symbol, pos.id, pnl, self.lock_profit_activate_usdt,
                    self.lock_profit_usdt,
                )
                self._lock_profit(pos)
        elif pos.trail_active == 2:
            # Profit locked — waiting for trail_activate threshold
            if pnl >= self.trail_activate_usdt:
                log.info(
                    "Trail jump for %s (pos %d): pnl=$%.2f >= $%.2f, locking profit",
                    pos.symbol, pos.id, pnl, self.trail_activate_usdt,
                )
                self._activate_trail(pos, current_price)
        elif pos.trail_active == 3:
            # Dead zone: SL is locked, waiting for trail_start_usdt
            if pnl >= self.trail_start_usdt:
                log.info(
                    "Trail starting for %s (pos %d): pnl=$%.2f >= $%.2f, now trailing",
                    pos.symbol, pos.id, pnl, self.trail_start_usdt,
                )
                self.store.update_trail(pos.id, trail_high_price=current_price, trail_active=4)
                self._update_trail(pos, current_price)
        else:
            # trail_active == 4: actively trailing
            self._update_trail(pos, current_price)

    def _move_to_breakeven(self, pos) -> None:
        """Move SL to entry price — zero risk."""
        new_sl = pos.entry_price
        self._replace_sl(pos, new_sl)
        self.store.update_trail(pos.id, trail_high_price=0, trail_active=1)
        log.info("Breakeven SL for %s: SL=%.4f (entry)", pos.symbol, new_sl)
        if self.notifier:
            self.notifier.send(
                f"🟡 BREAKEVEN {pos.symbol}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🛑 SL moved to entry: ${new_sl:,.2f}\n"
                f"💰 Zero risk"
            )

    def _lock_profit(self, pos) -> None:
        """At +$20: SL moves to lock in $15 profit."""
        lock_price_dist = _dollar_to_price_distance(
            self.lock_profit_usdt, self.margin_usdt, self.leverage, pos.entry_price,
        )
        if pos.side == "long":
            new_sl = pos.entry_price + lock_price_dist
        else:
            new_sl = pos.entry_price - lock_price_dist

        self._replace_sl(pos, new_sl)
        self.store.update_trail(pos.id, trail_high_price=0, trail_active=2)
        log.info(
            "Lock profit SL for %s: locking $%.0f, SL=%.4f",
            pos.symbol, self.lock_profit_usdt, new_sl,
        )
        if self.notifier:
            self.notifier.send(
                f"🔒 LOCK PROFIT {pos.symbol}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🛑 SL moved to +${self.lock_profit_usdt:.0f}: ${new_sl:,.2f}\n"
                f"💰 ${self.lock_profit_usdt:.0f} locked in"
            )

    def _activate_trail(self, pos, current_price: float) -> None:
        """At +$25: SL jumps to lock in $20 profit. Dead zone until +$30."""
        # Lock in = trail_start - trail_distance = $30 - $10 = $20 profit
        lock_in_usdt = self.trail_start_usdt - self.trail_distance_usdt
        lock_in_price_dist = _dollar_to_price_distance(
            lock_in_usdt, self.margin_usdt, self.leverage, pos.entry_price,
        )

        if pos.side == "long":
            new_sl = pos.entry_price + lock_in_price_dist
            high_price = current_price
        else:
            new_sl = pos.entry_price - lock_in_price_dist
            high_price = current_price

        self._replace_sl(pos, new_sl)

        # trail_active=3 means SL jumped but locked (dead zone)
        self.store.update_trail(pos.id, trail_high_price=high_price, trail_active=3)
        log.info(
            "Trail jumped for %s: locking $%.0f profit, SL=%.4f (dead zone until +$%.0f)",
            pos.symbol, lock_in_usdt, new_sl, self.trail_start_usdt,
        )
        if self.notifier:
            pnl = self._compute_unrealized_pnl_usdt(pos, current_price)
            self.notifier.send(format_trail_activated(pos.symbol, pnl, new_sl))

    def _update_trail(self, pos, current_price: float) -> None:
        """Trail is active. Move SL if price made a new high."""
        old_high = pos.trail_high_price or pos.entry_price
        trail_dist = self._trail_distance_as_price(current_price)

        if pos.side == "long":
            if current_price <= old_high:
                return  # no new high
            new_sl = current_price - trail_dist
            new_high = current_price
        else:
            if current_price >= old_high:
                return  # no new low (for shorts, "high" means best price = lowest)
            new_sl = current_price + trail_dist
            new_high = current_price

        self._replace_sl(pos, new_sl)
        self.store.update_trail(pos.id, trail_high_price=new_high, trail_active=4)
        log.info(
            "Trail updated for %s: high=%.4f → %.4f, SL=%.4f",
            pos.symbol, old_high, new_high, new_sl,
        )
        if self.notifier:
            self.notifier.send(format_trail_update(pos.symbol, new_high, new_sl))

    def _replace_sl(self, pos, new_trigger: float) -> None:
        """Cancel existing SL and place a new one."""
        # Cancel all existing SL/TP algos on this symbol
        try:
            self.blofin.cancel_all_tpsl(pos.symbol)
        except Exception as exc:
            log.warning("cancel_all_tpsl failed for %s: %s", pos.symbol, exc)

        if pos.sl_order_id:
            self.store.record_sl_order_id(pos.id, None)

        close_side = "sell" if pos.side == "long" else "buy"
        try:
            new_sl_id = self.blofin.place_sl_order(
                inst_id=pos.symbol, side=close_side,
                trigger_price=new_trigger, margin_mode="isolated",
            )
            self.store.record_sl_order_id(pos.id, new_sl_id)
        except Exception as exc:
            log.warning(
                "Trail SL placement failed for %s at %.4f: %s",
                pos.symbol, new_trigger, exc,
            )

    async def run(self) -> None:
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        log.info("PositionPoller started (interval=%ds)", self.interval_seconds)
        while not self._stop_event.is_set():
            await self.poll_once()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(self.interval_seconds, 0.001),
                )
            except asyncio.TimeoutError:
                continue
        log.info("PositionPoller stopped")

    def start(self) -> None:
        if self._task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("PositionPoller.start() called outside event loop; skipping")
            return
        self._stop_event = asyncio.Event()
        self._task = loop.create_task(self.run())

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:
                log.exception("PositionPoller task raised on shutdown")
            self._task = None
