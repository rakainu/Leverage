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
    filled_orders: dict[str, dict],
) -> list[tuple[int, dict]]:
    """Return list of (stage, order_dict) for filled TPs, in stage order.

    `filled_orders` is a dict mapping order id -> ccxt order dict, containing
    only orders with status closed/filled and filled > 0. Orders with id=None
    (already processed) are skipped.
    """
    filled: list[tuple[int, dict]] = []
    pairs = [(1, tp1_order_id), (2, tp2_order_id), (3, tp3_order_id)]
    for stage, oid in pairs:
        if not oid:
            continue
        order = filled_orders.get(str(oid))
        if order is not None:
            filled.append((stage, order))
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
        """Single poll cycle: inspect every open position, advance on TP fills.

        Drift detection: at the start of each cycle, fetch BloFin positions
        once. If a SQLite-tracked position is no longer present on BloFin
        (closed by BloFin's safety SL, manual intervention, etc.), archive
        the row and cancel any leftover orders.
        """
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
            blofin_open_symbols = None  # default to safe: no archival

        for pos in positions:
            try:
                self._process_position(pos, blofin_open_symbols=blofin_open_symbols)
            except Exception:
                log.exception("poller failed for position id=%s", pos.id)
            # Retry any missing protective SL (covers the "BE SL rejected
            # because price drifted" case and any other transient failure).
            try:
                self._ensure_sl_in_place(pos.id)
            except Exception:
                log.exception("poller SL retry failed for position id=%s", pos.id)

    def _archive_stale_position(self, pos) -> None:
        """Position is gone from BloFin. Cancel leftover orders, archive row."""
        log.warning(
            "Position id=%d (%s %s) gone from BloFin — archiving stale row",
            pos.id, pos.symbol, pos.side,
        )
        # Cancel any leftover TP limit orders
        for stage, oid in (
            (1, pos.tp1_order_id), (2, pos.tp2_order_id), (3, pos.tp3_order_id),
        ):
            if not oid:
                continue
            try:
                self.blofin.cancel_order(oid, pos.symbol)
            except Exception:
                pass  # may already be cancelled
        # Cancel any leftover SL algo
        if pos.sl_order_id:
            try:
                self.blofin.cancel_tpsl(pos.symbol, pos.sl_order_id)
            except Exception:
                pass
        self.store.close_position(pos.id, realized_pnl=None)

    def _ensure_sl_in_place(self, position_id: int) -> None:
        """If the position is in tp_stage>=1 but has no SL tracked, try to place
        the appropriate SL. Retries across poll cycles after transient failures.

        Runs AFTER any TP-fill processing so we catch both (a) a fresh fill
        whose SL placement just failed and (b) a deferred retry from an
        earlier cycle where price was on the wrong side.
        """
        row = self.store.get_position(position_id)
        if row is None or row.closed_at is not None:
            return
        if row.sl_order_id is not None:
            return
        if row.tp_stage == 0:
            return   # attached-entry SL regime, not our job

        close_side = "sell" if row.side == "long" else "buy"
        if row.tp_stage == 1:
            target = row.entry_price                       # breakeven
        elif row.tp_stage == 2:
            if row.tp1_fill_price is None:
                return
            target = row.tp1_fill_price                    # lock tp1
        else:
            return

        try:
            new_sl_id = self.blofin.place_sl_order(
                inst_id=row.symbol, side=close_side,
                trigger_price=target, margin_mode="isolated",
            )
            self.store.record_sl_order_id(row.id, new_sl_id)
            log.info(
                "Placed deferred SL for %s at %s (stage=%d, id=%s)",
                row.symbol, target, row.tp_stage, new_sl_id,
            )
        except Exception as exc:
            msg = str(exc)
            if "102038" in msg or "102040" in msg:
                # Price on the wrong side of breakeven; wait for retrace.
                log.debug(
                    "SL at %s rejected for %s (price on wrong side), will retry next cycle",
                    target, row.symbol,
                )
            else:
                log.warning("SL placement failed for %s: %s", row.symbol, exc)

    def _process_position(
        self, pos, *, blofin_open_symbols: Optional[set[str]] = None,
    ) -> None:
        # Drift check first: if BloFin no longer holds this position, archive.
        if blofin_open_symbols is not None and pos.symbol not in blofin_open_symbols:
            self._archive_stale_position(pos)
            return

        # Pull recent closed orders for this symbol and build a map of filled
        # order ids -> ccxt order dicts. BloFin's ccxt adapter does NOT
        # implement fetchOrder, so per-id lookup is not an option.
        try:
            closed_orders = self.blofin.fetch_closed_orders(pos.symbol, limit=50)
        except Exception as exc:
            log.warning(
                "fetch_closed_orders failed in poller for %s: %s", pos.symbol, exc,
            )
            return

        filled_orders: dict[str, dict] = {}
        for o in closed_orders:
            status = (o.get("status") or "").lower()
            filled_qty = float(o.get("filled") or 0)
            if status in ("closed", "filled") and filled_qty > 0:
                filled_orders[str(o.get("id"))] = o

        filled = _detect_tp_fill(
            tp1_order_id=pos.tp1_order_id,
            tp2_order_id=pos.tp2_order_id,
            tp3_order_id=pos.tp3_order_id,
            filled_orders=filled_orders,
        )
        if not filled:
            return

        # Process one stage per cycle to keep state transitions atomic.
        stage, order = filled[0]
        fill_price = float(order.get("average") or order.get("price") or 0.0)
        filled_qty = float(order.get("filled") or 0.0)

        log.info(
            "TP%d filled for %s (pos %d): %s @ %s",
            stage, pos.symbol, pos.id, filled_qty, fill_price,
        )

        # Record the fill + clear the tracked order id in SQLite first.
        self.store.record_tp_fill(
            pos.id, stage=stage, fill_price=fill_price,
            closed_contracts=filled_qty,
        )
        self.store.clear_tp_order_id(pos.id, stage=stage)

        # Cancel any outstanding SL algo on this symbol.
        # Covers BOTH the attached entry SL (which we don't track by id) and
        # any previously placed standalone SL. Sweep mode clears everything.
        try:
            self.blofin.cancel_all_tpsl(pos.symbol)
        except Exception as exc:
            log.warning("cancel_all_tpsl failed during tp%d poll: %s", stage, exc)
        if pos.sl_order_id:
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
