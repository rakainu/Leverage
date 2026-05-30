"""Paper executor — wraps lighter.PaperClient with per-symbol sizing.

Each symbol has its own margin × leverage budget; this module computes the
base_amount the PaperClient needs and submits market orders.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import lighter

log = logging.getLogger(__name__)


@dataclass
class OpenPosition:
    """One open position for a symbol, tracked for the state machine."""
    symbol: str
    market_id: int
    side: str            # "long" or "short"
    entry_price: float
    base_amount: float
    margin_usdt: float
    leverage: float
    opened_at: float     # unix seconds
    notional: float = 0.0
    state: int = 0       # 0=initial, 1=BE, 2=lock, 3=trail_set, 4=trailing
    sl_price: float = 0.0
    trail_high: float = 0.0   # best favorable price seen (high for long, low for short)
    max_state: int = 0
    # --- squeeze / atr_trail mode (unused in other modes) ---
    atr_entry: float = 0.0    # ATR(14) at entry bar (fixes the trail distance)
    best_close: float = 0.0   # best CLOSE since entry (long: max, short: min) — trail anchor
    bars_held: int = 0        # closed bars since entry (for the time stop)


@dataclass
class FillResult:
    """Wraps PaperOrderResult for downstream use."""
    filled_size: float
    avg_price: float
    total_fee: float
    success: bool = True


class PaperExecutor:
    """Thin layer between strategy logic and lighter.PaperClient."""

    def __init__(self, paper: lighter.PaperClient, symbols: dict[str, dict]):
        """
        Args:
            paper: an initialized PaperClient already track_market'd for each symbol
            symbols: {SymbolName: {"market_id": int, "margin_usdt": float, "leverage": float}}
        """
        self.paper = paper
        self.symbols = symbols
        self.positions: dict[str, OpenPosition] = {}   # symbol -> OpenPosition
        # Mark-feed freshness tracking. Pure diagnostic — does NOT affect
        # what get_mark_price returns. Watchdog uses mark_age_seconds() to
        # detect a silently-dead Lighter WS trade subscription (see incident
        # 2026-05-23, position #16). Timestamp updates only when value changes.
        self._mark_value: dict[int, float] = {}
        self._mark_change_monotonic: dict[int, float] = {}

    def get_mark_price(self, symbol: str) -> Optional[float]:
        """Current best-known price for `symbol`.

        Reads the live order book mid (mean of best bid + best ask) from
        PaperClient.order_books, which IS kept fresh by the Lighter WS
        subscription `order_book/{market_id}`. Two prior approaches failed:

        - `pos.mark_price`: frozen at position's fill price, never updates
          (incident 2026-05-22, positions #13/#14 stuck >14h).
        - `market_configs[mid].last_trade_price`: populated once at
          subscription time from a REST snapshot and never refreshed by
          the WS. The order book listener only subscribes to the
          `order_book/{market_id}` channel — there is no trade channel.
          (incident 2026-05-23, position #16 stuck on a constant mark.)

        Order book mid is the right source because (1) every WS update
        mutates it, (2) it's what paper fills match against in the SDK,
        and (3) bid/ask prices are quantized to the market's tick size,
        so any genuine market move surfaces here.
        """
        market_id = self.symbols[symbol]["market_id"]
        book = self.paper.order_books.get(market_id)
        if book is None:
            return None
        best_ask = book.best_ask
        best_bid = book.best_bid
        if best_ask is None or best_bid is None:
            return None
        ask = best_ask.price_float
        bid = best_bid.price_float
        if ask <= 0 or bid <= 0:
            return None
        val = (ask + bid) / 2.0
        # Freshness bookkeeping — only update timestamp on actual value
        # changes. For an active market like ZEC/SOL the top of book
        # churns several times a second, so the mid ticks through here
        # continuously when the WS is healthy.
        prior = self._mark_value.get(market_id)
        if prior is None or prior != val:
            self._mark_value[market_id] = val
            self._mark_change_monotonic[market_id] = time.monotonic()
        return val

    def mark_age_seconds(self, symbol: str) -> Optional[float]:
        """Seconds since this symbol's mark value last changed.

        Pure diagnostic for the watchdog. Returns None if we have never
        recorded a non-zero mark for this symbol. Does NOT block trading.
        """
        sym_cfg = self.symbols.get(symbol)
        if sym_cfg is None:
            return None
        # Poke get_mark_price first so any just-arrived update is captured.
        _ = self.get_mark_price(symbol)
        ts = self._mark_change_monotonic.get(sym_cfg["market_id"])
        if ts is None:
            return None
        return time.monotonic() - ts

    def size_for_margin(self, symbol: str, ref_price: float) -> float:
        """Compute base_amount for a position with the symbol's margin × leverage."""
        sym_cfg = self.symbols[symbol]
        notional = sym_cfg["margin_usdt"] * sym_cfg["leverage"]
        raw = notional / ref_price
        market_id = sym_cfg["market_id"]
        mc = self.paper.market_configs.get(market_id)
        if mc:
            raw = max(round(raw, mc.size_decimals), mc.min_base_amount)
        return raw

    def is_open(self, symbol: str) -> bool:
        return symbol in self.positions

    async def open_position(self, symbol: str, side: str,
                            base_amount: Optional[float] = None) -> Optional[OpenPosition]:
        """Place a market buy/sell. Returns the new OpenPosition on success.

        If `base_amount` is given (risk-based sizing from the caller), it is used
        directly (rounded to the market's size precision, floored at min); else
        the per-symbol margin x leverage sizing is used.
        """
        if self.is_open(symbol):
            log.warning("%s: already have an open position; ignoring %s entry", symbol, side)
            return None
        sym_cfg = self.symbols[symbol]
        price = self.get_mark_price(symbol)
        if price is None or price <= 0:
            log.error("%s: no mark price available, cannot size order", symbol)
            return None
        if base_amount is not None:
            size = base_amount
            mc = self.paper.market_configs.get(sym_cfg["market_id"])
            if mc:
                size = max(round(size, mc.size_decimals), mc.min_base_amount)
        else:
            size = self.size_for_margin(symbol, price)
        if size <= 0:
            log.error("%s: computed size <= 0 (price %.4f)", symbol, price)
            return None

        order_side = lighter.PaperOrderSide.BUY if side == "long" else lighter.PaperOrderSide.SELL
        log.info("%s: OPEN %s size=%.4f @ ~$%.4f (margin $%.0f x%.0f)",
                 symbol, side.upper(), size, price, sym_cfg["margin_usdt"], sym_cfg["leverage"])
        try:
            result = await self.paper.create_paper_order(lighter.PaperOrderRequest(
                market_id=sym_cfg["market_id"],
                side=order_side,
                base_amount=size,
            ))
        except Exception as exc:
            log.error("%s: open order failed: %s", symbol, exc, exc_info=True)
            return None

        import time
        pos = OpenPosition(
            symbol=symbol,
            market_id=sym_cfg["market_id"],
            side=side,
            entry_price=float(result.avg_price),
            base_amount=float(result.filled_size),
            margin_usdt=sym_cfg["margin_usdt"],
            leverage=sym_cfg["leverage"],
            opened_at=time.time(),
            notional=float(result.avg_price) * float(result.filled_size),
            trail_high=float(result.avg_price),
        )
        self.positions[symbol] = pos
        log.info("%s: FILLED %s %.4f @ $%.4f", symbol, side.upper(),
                 pos.base_amount, pos.entry_price)
        return pos

    async def close_position(self, symbol: str, reason: str) -> Optional[FillResult]:
        """Close the open position via opposite market order."""
        pos = self.positions.get(symbol)
        if pos is None:
            log.warning("%s: close requested but no position open", symbol)
            return None
        close_side = lighter.PaperOrderSide.SELL if pos.side == "long" else lighter.PaperOrderSide.BUY
        log.info("%s: CLOSE %s (reason=%s) size=%.4f", symbol, pos.side.upper(), reason, pos.base_amount)
        try:
            result = await self.paper.create_paper_order(lighter.PaperOrderRequest(
                market_id=pos.market_id,
                side=close_side,
                base_amount=pos.base_amount,
            ))
        except Exception as exc:
            log.error("%s: close order failed: %s", symbol, exc, exc_info=True)
            return None

        exit_price = float(result.avg_price)
        if pos.side == "long":
            pnl = (exit_price - pos.entry_price) * pos.base_amount
        else:
            pnl = (pos.entry_price - exit_price) * pos.base_amount
        log.info("%s: CLOSED at $%.4f  pnl=$%+.2f  reason=%s", symbol, exit_price, pnl, reason)
        del self.positions[symbol]
        return FillResult(
            filled_size=float(result.filled_size),
            avg_price=exit_price,
            total_fee=float(result.total_fee),
            success=True,
        )

    async def reduce_position(self, symbol: str, base_to_close: float,
                              reason: str) -> Optional[FillResult]:
        """Partially close `base_to_close` of the open position (scale-out).

        Submits an opposite market order for the partial size, decrements
        pos.base_amount, and removes the position once it is fully flat.
        Returns the FillResult (avg_price = the partial exit price), or None.
        """
        pos = self.positions.get(symbol)
        if pos is None:
            return None
        size = min(base_to_close, pos.base_amount)
        mc = self.paper.market_configs.get(pos.market_id)
        if mc:
            size = round(size, mc.size_decimals)
        if size <= 0:
            return None
        # If what's left would be below the exchange minimum, close it all so we
        # never strand an untradeable dust position.
        remaining_after = pos.base_amount - size
        if mc and 0 < remaining_after < mc.min_base_amount:
            size = pos.base_amount
            remaining_after = 0.0

        close_side = lighter.PaperOrderSide.SELL if pos.side == "long" else lighter.PaperOrderSide.BUY
        log.info("%s: REDUCE %s %.4f/%.4f (reason=%s)", symbol, pos.side.upper(),
                 size, pos.base_amount, reason)
        try:
            result = await self.paper.create_paper_order(lighter.PaperOrderRequest(
                market_id=pos.market_id, side=close_side, base_amount=size,
            ))
        except Exception as exc:
            log.error("%s: reduce order failed: %s", symbol, exc, exc_info=True)
            return None

        exit_price = float(result.avg_price)
        pos.base_amount = round(pos.base_amount - float(result.filled_size), 8)
        if pos.base_amount <= (mc.min_base_amount if mc else 0) or remaining_after == 0.0:
            del self.positions[symbol]
            log.info("%s: fully closed via scale-out", symbol)
        return FillResult(filled_size=float(result.filled_size), avg_price=exit_price,
                          total_fee=float(result.total_fee), success=True)

    def pnl_at_mark(self, symbol: str) -> Optional[float]:
        """Current unrealized PnL in USDT for the open position on `symbol`."""
        pos = self.positions.get(symbol)
        if pos is None:
            return None
        mark = self.get_mark_price(symbol)
        if mark is None:
            return None
        if pos.side == "long":
            return (mark - pos.entry_price) * pos.base_amount
        return (pos.entry_price - mark) * pos.base_amount

    def account_summary(self) -> dict:
        return {
            "collateral": float(self.paper.get_collateral()),
            "portfolio_value": float(self.paper.get_portfolio_value()),
            "open_positions": len(self.positions),
        }
