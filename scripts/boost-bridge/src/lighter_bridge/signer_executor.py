"""Real-order executor for the Booster testnet book.

Mirrors PaperExecutor's interface so the existing strategy/state-machine code drives
it unchanged, but instead of paper fills it places REAL signed orders on Lighter via
SignerClient. Market DATA still comes from a PaperClient (order-book feed, market
configs) — it needs no funds and reuses the scalper's battle-tested mark/quantize/
watchdog plumbing. Only EXECUTION is real.

Key difference from paper: maker entries don't fill instantly. open_position places a
POST_ONLY limit at the favorable offset (this maker fill IS the edge — taker entry was
shown to lose), then polls the account position for the fill over a validity window,
cancelling if it never fills. Every entry records fill quality (requested vs filled
price, maker-fill yes/no) — the whole reason this testnet book exists.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import lighter
from lighter.api.account_api import AccountApi

from .executor import OpenPosition, FillResult  # reuse dataclasses

log = logging.getLogger(__name__)


@dataclass
class FillQuality:
    """Per-entry execution telemetry — the data this book exists to collect."""
    symbol: str
    side: str
    requested_price: float      # the maker limit we posted
    mark_at_request: float      # order-book mid when we decided
    filled_price: float         # realized avg entry
    maker: bool                 # did the post-only limit fill (vs cancelled/repriced)?
    slippage_bps: float         # (filled - requested)/requested in bps, signed adverse
    wait_seconds: float         # how long until fill


class SignerExecutor:
    """Real-order execution via SignerClient; market data via PaperClient."""

    def __init__(self, paper: "lighter.PaperClient", signer: "lighter.SignerClient",
                 api: "lighter.ApiClient", account_index: int, symbols: dict[str, dict],
                 fill_wait_s: float = 60.0, poll_s: float = 4.0):
        self.paper = paper          # DATA only (order books, market_configs)
        self.signer = signer        # real orders
        self.api = api              # AccountApi for fills/positions
        self.account_index = account_index
        self.symbols = symbols
        self.positions: dict[str, OpenPosition] = {}
        self._pending: dict[str, dict] = {}     # symbol -> resting maker entry
        self.fill_wait_s = fill_wait_s
        self.poll_s = poll_s
        self._coid = int(time.time() * 1000) % (2 ** 31)
        self._mark_value: dict[int, float] = {}
        self._mark_change_monotonic: dict[int, float] = {}

    # ---- ids / precision -------------------------------------------------
    def _next_coid(self) -> int:
        self._coid = (self._coid + 1) % (2 ** 31)
        return self._coid

    def _mc(self, market_id: int):
        return self.paper.market_configs.get(market_id)

    def _to_int(self, value: float, decimals: int) -> int:
        return int(round(value * (10 ** decimals)))

    def quantize_size(self, market_id: int, value: float) -> float:
        mc = self._mc(market_id)
        return round(value, mc.size_decimals) if mc else value

    # ---- market data (reused from the paper feed) ------------------------
    def get_mark_price(self, symbol: str) -> Optional[float]:
        market_id = self.symbols[symbol]["market_id"]
        book = self.paper.order_books.get(market_id)
        if book is None:
            return None
        ba, bb = book.best_ask, book.best_bid
        if ba is None or bb is None or ba.price_float <= 0 or bb.price_float <= 0:
            return None
        val = (ba.price_float + bb.price_float) / 2.0
        prior = self._mark_value.get(market_id)
        if prior is None or prior != val:
            self._mark_value[market_id] = val
            self._mark_change_monotonic[market_id] = time.monotonic()
        return val

    def mark_age_seconds(self, symbol: str) -> Optional[float]:
        sym_cfg = self.symbols.get(symbol)
        if sym_cfg is None:
            return None
        _ = self.get_mark_price(symbol)
        ts = self._mark_change_monotonic.get(sym_cfg["market_id"])
        return None if ts is None else time.monotonic() - ts

    def is_open(self, symbol: str) -> bool:
        return symbol in self.positions

    # ---- account / position reads ----------------------------------------
    async def read_position(self, market_id: int) -> Optional[dict]:
        """Live on-chain position for a market, or None if flat."""
        r = await AccountApi(self.api).account(by="index", value=str(self.account_index))
        for p in r.to_dict()["accounts"][0].get("positions", []):
            if int(p["market_id"]) == market_id and abs(float(p["position"])) > 0:
                return {"size": abs(float(p["position"])), "sign": int(p["sign"]),
                        "avg_entry": float(p["avg_entry_price"]),
                        "upnl": float(p.get("unrealized_pnl", 0) or 0)}
        return None

    async def collateral(self) -> Optional[float]:
        try:
            r = await AccountApi(self.api).account(by="index", value=str(self.account_index))
            return float(r.to_dict()["accounts"][0]["collateral"])
        except Exception as exc:
            log.error("collateral read failed: %s", exc)
            return None

    # ---- order primitives ------------------------------------------------
    async def _post_only_limit(self, market_id, base_amount, limit_price, is_ask):
        mc = self._mc(market_id)
        size_int = max(self._to_int(base_amount, mc.size_decimals),
                       self._to_int(mc.min_base_amount, mc.size_decimals))
        px_int = self._to_int(limit_price, mc.price_decimals)
        coid = self._next_coid()
        tx, txh, err = await self.signer.create_order(
            market_index=market_id, client_order_index=coid, base_amount=size_int,
            price=px_int, is_ask=is_ask, order_type=self.signer.ORDER_TYPE_LIMIT,
            time_in_force=self.signer.ORDER_TIME_IN_FORCE_POST_ONLY, reduce_only=False,
            order_expiry=self.signer.DEFAULT_28_DAY_ORDER_EXPIRY)
        return coid, err

    async def _market_reduce(self, market_id, base_amount, is_ask):
        """Close/reduce via a market order with bounded slippage (taker exit)."""
        mc = self._mc(market_id)
        size_int = self._to_int(round(base_amount, mc.size_decimals), mc.size_decimals)
        coid = self._next_coid()
        mark = (self._mark_value.get(market_id)
                or float(self.paper.order_books[market_id].best_bid.price_float))
        # aggressive limit so the IOC market crosses; 1% bound
        px = mark * (0.99 if is_ask else 1.01)
        tx, txh, err = await self.signer.create_order(
            market_index=market_id, client_order_index=coid, base_amount=size_int,
            price=self._to_int(px, mc.price_decimals), is_ask=is_ask,
            order_type=self.signer.ORDER_TYPE_MARKET,
            time_in_force=self.signer.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
            reduce_only=True, order_expiry=self.signer.DEFAULT_IOC_EXPIRY)
        return err

    async def cancel(self, market_id, coid):
        try:
            await self.signer.cancel_order(market_index=market_id, order_index=coid)
        except Exception as exc:
            log.warning("cancel %s/%s failed: %s", market_id, coid, exc)

    # ---- maker entry lifecycle: arm -> poll -> cancel --------------------
    # Real maker orders fill continuously on the exchange, not on bar close. So we
    # ARM a resting post-only limit when the signal fires, POLL the real position on
    # the fast (mark) loop to detect the fill, and CANCEL if it expires unfilled.
    def has_pending(self, symbol: str) -> bool:
        return symbol in self._pending

    def pending_age_seconds(self, symbol: str) -> Optional[float]:
        pe = self._pending.get(symbol)
        return None if pe is None else time.monotonic() - pe["armed_at"]

    async def arm_maker_entry(self, symbol: str, side: str, base_amount: float,
                              limit_price: float) -> bool:
        """Post a resting POST_ONLY limit at `limit_price`. Returns True if placed."""
        if self.is_open(symbol) or self.has_pending(symbol):
            return False
        market_id = self.symbols[symbol]["market_id"]
        mark = self.get_mark_price(symbol)
        coid, err = await self._post_only_limit(market_id, base_amount, limit_price,
                                                is_ask=(side == "short"))
        if err:
            log.error("%s: maker entry rejected: %s", symbol, err)
            return False
        self._pending[symbol] = {"coid": coid, "side": side, "base_amount": base_amount,
                                 "limit_price": limit_price, "mark": mark,
                                 "market_id": market_id, "armed_at": time.monotonic()}
        log.info("%s: ARMED maker %s %.6f @ %.6f (mark %.6f, coid %d)",
                 symbol, side.upper(), base_amount, limit_price, mark or -1, coid)
        return True

    async def poll_entry_fill(self, symbol: str) -> Optional[tuple[OpenPosition, FillQuality]]:
        """Check whether the resting maker entry has filled on-chain. On fill, build
        and store the OpenPosition and return it with fill telemetry; else None."""
        pe = self._pending.get(symbol)
        if pe is None:
            return None
        pos = await self.read_position(pe["market_id"])
        if not (pos and pos["size"] >= pe["base_amount"] * 0.95):
            return None
        fp, side, lim = pos["avg_entry"], pe["side"], pe["limit_price"]
        adverse = (fp - lim) if side == "long" else (lim - fp)
        fq = FillQuality(symbol=symbol, side=side, requested_price=lim,
                         mark_at_request=pe["mark"] or lim, filled_price=fp, maker=True,
                         slippage_bps=adverse / lim * 1e4,
                         wait_seconds=time.monotonic() - pe["armed_at"])
        op = OpenPosition(symbol=symbol, market_id=pe["market_id"], side=side, entry_price=fp,
                          base_amount=self.quantize_size(pe["market_id"], pos["size"]),
                          margin_usdt=0.0, leverage=0.0, opened_at=time.time(),
                          notional=fp * pos["size"], trail_high=fp)
        self.positions[symbol] = op
        self._pending.pop(symbol, None)
        log.info("%s: FILLED maker %s %.6f @ %.6f (%.1f bps adverse, %.0fs)", symbol,
                 side.upper(), op.base_amount, fp, fq.slippage_bps, fq.wait_seconds)
        return op, fq

    async def cancel_entry(self, symbol: str) -> None:
        pe = self._pending.pop(symbol, None)
        if pe is not None:
            await self.cancel(pe["market_id"], pe["coid"])
            log.info("%s: cancelled resting maker entry (coid %d)", symbol, pe["coid"])

    async def close_position(self, symbol: str, reason: str) -> Optional[FillResult]:
        pos = self.positions.get(symbol)
        if pos is None:
            return None
        is_ask = (pos.side == "long")  # close long = sell
        err = await self._market_reduce(pos.market_id, pos.base_amount, is_ask)
        if err:
            log.error("%s: close failed: %s", symbol, err)
            return None
        await asyncio.sleep(self.poll_s)
        after = await self.read_position(pos.market_id)
        exit_px = self.get_mark_price(symbol) or pos.entry_price
        if after is None:  # fully flat
            pnl = ((exit_px - pos.entry_price) if pos.side == "long"
                   else (pos.entry_price - exit_px)) * pos.base_amount
            del self.positions[symbol]
            log.info("%s: CLOSED ~%.4f pnl~$%+.2f reason=%s", symbol, exit_px, pnl, reason)
            return FillResult(filled_size=pos.base_amount, avg_price=exit_px, total_fee=0.0)
        log.warning("%s: close left residual %.4f", symbol, after["size"])
        pos.base_amount = self.quantize_size(pos.market_id, after["size"])
        return None

    async def reduce_position(self, symbol: str, base_to_close: float,
                              reason: str) -> Optional[FillResult]:
        pos = self.positions.get(symbol)
        if pos is None:
            return None
        size = min(base_to_close, pos.base_amount)
        mc = self._mc(pos.market_id)
        size = round(size, mc.size_decimals)
        if size <= 0:
            return None
        if 0 < pos.base_amount - size < mc.min_base_amount:
            size = pos.base_amount  # don't strand dust
        is_ask = (pos.side == "long")
        err = await self._market_reduce(pos.market_id, size, is_ask)
        if err:
            log.error("%s: reduce failed: %s", symbol, err)
            return None
        exit_px = self.get_mark_price(symbol) or pos.entry_price
        pos.base_amount = round(pos.base_amount - size, 8)
        if pos.base_amount <= mc.min_base_amount:
            del self.positions[symbol]
            log.info("%s: fully closed via scale-out", symbol)
        return FillResult(filled_size=size, avg_price=exit_px, total_fee=0.0)

    def pnl_at_mark(self, symbol: str) -> Optional[float]:
        pos = self.positions.get(symbol)
        if pos is None:
            return None
        mark = self.get_mark_price(symbol)
        if mark is None:
            return None
        return ((mark - pos.entry_price) if pos.side == "long"
                else (pos.entry_price - mark)) * pos.base_amount

    async def account_summary(self) -> dict:
        col = await self.collateral()
        return {"collateral": col or 0.0, "portfolio_value": col or 0.0,
                "open_positions": len(self.positions)}
