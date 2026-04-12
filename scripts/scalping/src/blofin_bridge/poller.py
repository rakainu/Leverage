"""Background position poller with trailing stop logic.

Every poll cycle (default 10s):
  1. Drift check: archive positions gone from BloFin.
  2. For each open position, fetch current price and compute unrealized P&L.
  3. If profit >= trail_activate_usdt and trail not yet active:
       - Move SL to (current_price - trail_distance) for longs.
       - Mark trail active, record high-water price.
  4. If trail already active and price made a new high:
       - Move SL to (new_high - trail_distance).
  5. SL never moves backward (only tightens).
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .blofin_client import BloFinClient
from .ema import compute_ema
from .entry_gate import EntryGate
from .handlers.entry import handle_entry, _dollar_to_price_distance
from .notify import (
    Notifier, format_entry, format_trail_activated, format_trail_update,
    format_pending_filled, format_pending_expired,
)
from .state import Store

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
        trail_activate_usdt: float = 25.0,
        trail_start_usdt: float = 30.0,
        trail_distance_usdt: float = 10.0,
        sl_loss_usdt: float = 15.0,
        margin_usdt: float = 100.0,
        leverage: float = 30.0,
        notifier: Optional[Notifier] = None,
        # EMA retest config
        ema_retest_period: int = 9,
        ema_retest_timeframe: str = "5m",
        ema_retest_max_overshoot_pct: float = 0.2,
        # Symbol configs for executing pending entries
        symbol_configs: Optional[dict[str, dict[str, Any]]] = None,
        # Operator-initiated per-symbol pause
        gate: Optional[EntryGate] = None,
    ) -> None:
        self.store = store
        self.blofin = blofin
        self.interval_seconds = interval_seconds
        self.breakeven_usdt = breakeven_usdt
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
        """Check each pending signal for EMA retest or expiry."""
        signals = self.store.list_pending_signals()
        now = datetime.now(timezone.utc)

        for sig in signals:
            try:
                # Check expiry
                expires_at = datetime.fromisoformat(sig["expires_at"])
                if now >= expires_at:
                    self.store.expire_pending_signal(sig["id"])
                    log.info("Pending signal %d expired for %s", sig["id"], sig["symbol"])
                    if self.notifier:
                        self.notifier.send(format_pending_expired(sig["action"], sig["symbol"]))
                    continue

                # Operator pause: drop the pending signal without firing.
                if self.gate is not None and self.gate.is_paused(sig["symbol"]):
                    self.store.expire_pending_signal(sig["id"])
                    log.info(
                        "Pending signal %d for %s dropped: entries paused",
                        sig["id"], sig["symbol"],
                    )
                    continue

                # Fetch current price and EMA
                current_price = self.blofin.fetch_last_price(sig["symbol"])
                bars = self.blofin.fetch_recent_ohlcv(
                    sig["symbol"],
                    timeframe=self.ema_retest_timeframe,
                    limit=self.ema_retest_period + 10,
                )
                closes = [bar[4] for bar in bars]  # index 4 = close
                ema_value = compute_ema(closes, self.ema_retest_period)

                # Check for retest (with overshoot cap)
                max_overshoot = ema_value * (self.ema_retest_max_overshoot_pct / 100)
                retest = False
                if sig["action"] == "buy" and current_price <= ema_value:
                    retest = current_price >= ema_value - max_overshoot
                elif sig["action"] == "sell" and current_price >= ema_value:
                    retest = current_price <= ema_value + max_overshoot

                if not retest:
                    continue

                log.info(
                    "EMA(%d) retest confirmed for %s %s: price=%.2f, ema=%.2f",
                    self.ema_retest_period, sig["action"], sig["symbol"],
                    current_price, ema_value,
                )

                # Execute the entry
                sym_cfg = self.symbol_configs.get(sig["symbol"])
                if sym_cfg is None:
                    log.warning("No config for %s, skipping pending signal", sig["symbol"])
                    continue

                result = handle_entry(
                    action=sig["action"], symbol=sig["symbol"],
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
                    self.store.fill_pending_signal(sig["id"], current_price)
                    if self.notifier:
                        self.notifier.send(format_pending_filled(
                            sig["action"], sig["symbol"],
                            result["entry_price"], sig["signal_price"],
                        ))
                        # Also send the full entry notification
                        result["symbol"] = sig["symbol"]
                        self.notifier.send(format_entry(result))
                else:
                    log.warning(
                        "Pending signal %d: entry failed: %s",
                        sig["id"], result.get("reason"),
                    )
            except Exception:
                log.exception("Failed processing pending signal %d", sig["id"])

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
        # trail_active: 0=inactive, 1=breakeven, 2=jumped/locked (dead zone), 3=trailing
        if pos.trail_active == 0:
            if pnl >= self.breakeven_usdt:
                log.info(
                    "Breakeven for %s (pos %d): pnl=$%.2f >= $%.2f",
                    pos.symbol, pos.id, pnl, self.breakeven_usdt,
                )
                self._move_to_breakeven(pos)
        elif pos.trail_active == 1:
            # Breakeven — waiting for trail_activate threshold
            if pnl >= self.trail_activate_usdt:
                log.info(
                    "Trail jump for %s (pos %d): pnl=$%.2f >= $%.2f, locking profit",
                    pos.symbol, pos.id, pnl, self.trail_activate_usdt,
                )
                self._activate_trail(pos, current_price)
        elif pos.trail_active == 2:
            # Dead zone: SL is locked, waiting for trail_start_usdt
            if pnl >= self.trail_start_usdt:
                log.info(
                    "Trail starting for %s (pos %d): pnl=$%.2f >= $%.2f, now trailing",
                    pos.symbol, pos.id, pnl, self.trail_start_usdt,
                )
                self.store.update_trail(pos.id, trail_high_price=current_price, trail_active=3)
                self._update_trail(pos, current_price)
        else:
            # trail_active == 3: actively trailing
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

        # trail_active=2 means SL jumped but locked (dead zone)
        self.store.update_trail(pos.id, trail_high_price=high_price, trail_active=2)
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
        self.store.update_trail(pos.id, trail_high_price=new_high, trail_active=3)
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
