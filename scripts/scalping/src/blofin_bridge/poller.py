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
from typing import Optional

from .blofin_client import BloFinClient
from .handlers.entry import _dollar_to_price_distance
from .notify import Notifier, format_trail_activated, format_trail_update
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
        trail_activate_usdt: float = 30.0,
        trail_distance_usdt: float = 10.0,
        margin_usdt: float = 100.0,
        leverage: float = 30.0,
        notifier: Optional[Notifier] = None,
    ) -> None:
        self.store = store
        self.blofin = blofin
        self.interval_seconds = interval_seconds
        self.trail_activate_usdt = trail_activate_usdt
        self.trail_distance_usdt = trail_distance_usdt
        self.margin_usdt = margin_usdt
        self.leverage = leverage
        self.notifier = notifier
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

    async def poll_once(self) -> None:
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

        exit_reason = "trail_sl" if pos.trail_active else "drift"
        self.store.log_trade(
            position_id=pos.id, exit_price=exit_price, exit_reason=exit_reason,
            margin_usdt=self.margin_usdt, leverage=self.leverage,
            initial_sl=None, tp_ceiling=None,
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
        if not pos.trail_active:
            # Check if we should activate trailing
            if pnl >= self.trail_activate_usdt:
                log.info(
                    "Trail activating for %s (pos %d): pnl=$%.2f >= $%.2f threshold",
                    pos.symbol, pos.id, pnl, self.trail_activate_usdt,
                )
                self._activate_trail(pos, current_price)
        else:
            # Trail is active — check for new high and update SL
            self._update_trail(pos, current_price)

    def _activate_trail(self, pos, current_price: float) -> None:
        """First activation: move SL up tight, record high-water mark."""
        trail_dist = self._trail_distance_as_price(current_price)

        if pos.side == "long":
            new_sl = current_price - trail_dist
            high_price = current_price
        else:
            new_sl = current_price + trail_dist
            high_price = current_price

        # Cancel existing SL and place new one
        self._replace_sl(pos, new_sl)

        # Record trail state
        self.store.update_trail(pos.id, trail_high_price=high_price, trail_active=True)
        log.info(
            "Trail active for %s: high=%.4f, new SL=%.4f",
            pos.symbol, high_price, new_sl,
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
        self.store.update_trail(pos.id, trail_high_price=new_high, trail_active=True)
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
