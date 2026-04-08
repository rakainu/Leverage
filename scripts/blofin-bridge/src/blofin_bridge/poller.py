"""Background position poller for v1.1 TP-fill detection.

Periodically queries BloFin for the status of each tracked TP order. When a TP
fills, advances the bridge's position state per P2 step-stop SL policy:
  - TP1 fill -> SL to entry (breakeven)
  - TP2 fill -> SL to TP1 fill price (locks >= TP1 profit)
  - TP3 fill -> position closed, SL cancelled

One stage per poll cycle handles one TP transition per position so state
advances atomically. If multiple TPs filled between polls, subsequent cycles
process them in sequence.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Callable, Optional

from .blofin_client import BloFinClient
from .state import Store

log = logging.getLogger(__name__)


def _detect_tp_fill(
    *,
    tp1_order_id: Optional[str],
    tp2_order_id: Optional[str],
    tp3_order_id: Optional[str],
    fetch_fn: Callable[[str], dict],
) -> list[int]:
    """Return list of TP stages [1,2,3] that have filled, in order.

    An order that was never tracked (id is None) is skipped. An order whose
    status is 'closed' or 'filled' counts as filled.
    """
    filled: list[int] = []
    pairs = [(1, tp1_order_id), (2, tp2_order_id), (3, tp3_order_id)]
    for stage, oid in pairs:
        if not oid:
            continue
        try:
            order = fetch_fn(oid)
        except Exception as exc:
            log.warning("fetch_order failed for tp%d %s: %s", stage, oid, exc)
            continue
        status = (order.get("status") or "").lower()
        if status in ("closed", "filled"):
            filled.append(stage)
    return filled


class PositionPoller:
    """Polls BloFin for TP fills and advances SLs per P2 step-stop."""

    def __init__(
        self,
        *,
        store: Store,
        blofin: BloFinClient,
        interval_seconds: int,
    ) -> None:
        self.store = store
        self.blofin = blofin
        self.interval_seconds = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

    async def poll_once(self) -> None:
        """Single poll cycle: inspect every open position, advance on TP fills."""
        try:
            positions = self.store.list_open_positions()
        except Exception:
            log.exception("failed to list open positions")
            return

        for pos in positions:
            try:
                self._process_position(pos)
            except Exception:
                log.exception("poller failed for position id=%s", pos.id)

    def _process_position(self, pos) -> None:
        filled = _detect_tp_fill(
            tp1_order_id=pos.tp1_order_id,
            tp2_order_id=pos.tp2_order_id,
            tp3_order_id=pos.tp3_order_id,
            fetch_fn=lambda oid: self.blofin.fetch_order(oid, pos.symbol),
        )
        if not filled:
            return

        # Process one stage per cycle to keep state transitions atomic.
        stage = filled[0]
        oid = {
            1: pos.tp1_order_id,
            2: pos.tp2_order_id,
            3: pos.tp3_order_id,
        }[stage]
        order = self.blofin.fetch_order(oid, pos.symbol)
        fill_price = float(order.get("average") or order.get("price") or 0.0)
        filled_contracts = float(order.get("filled") or 0.0)

        log.info(
            "TP%d filled for %s (pos %d): %s contracts @ %s",
            stage, pos.symbol, pos.id, filled_contracts, fill_price,
        )

        # Record the fill + clear the order id in SQLite first.
        self.store.record_tp_fill(
            pos.id, stage=stage, fill_price=fill_price,
            closed_contracts=filled_contracts,
        )
        self.store.clear_tp_order_id(pos.id, stage=stage)

        # Cancel the current SL (if any).
        if pos.sl_order_id:
            try:
                self.blofin.cancel_tpsl(pos.symbol, pos.sl_order_id)
            except Exception as exc:
                log.warning("cancel_tpsl failed during tp%d poll: %s", stage, exc)
            self.store.record_sl_order_id(pos.id, None)

        # Reload the updated row.
        updated = self.store.get_position(pos.id)

        if stage == 3 or (updated and updated.current_size <= 0):
            self.store.close_position(pos.id, realized_pnl=None)
            log.info("Position %s (%d) archived after TP3 fill", pos.symbol, pos.id)
            return

        # Place the new SL per P2 step-stop.
        close_side = "sell" if pos.side == "long" else "buy"
        if stage == 1:
            new_trigger = updated.entry_price   # breakeven
        elif stage == 2:
            if updated.tp1_fill_price is None:
                log.warning("tp2 advanced without tp1_fill_price; skipping SL replace")
                return
            new_trigger = updated.tp1_fill_price
        else:
            return

        try:
            new_sl_id = self.blofin.place_sl_order(
                inst_id=pos.symbol, side=close_side,
                trigger_price=new_trigger, margin_mode="isolated",
            )
            self.store.record_sl_order_id(pos.id, new_sl_id)
            log.info(
                "Poller advanced SL for %s to %.8f after TP%d (new id=%s)",
                pos.symbol, new_trigger, stage, new_sl_id,
            )
        except Exception as exc:
            log.warning(
                "Poller failed to place new SL after tp%d: %s (position left naked)",
                stage, exc,
            )

    async def run(self) -> None:
        """Run the poll loop until stop() is called."""
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
        """Fire-and-forget: create the background task. No-op if no running loop."""
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
