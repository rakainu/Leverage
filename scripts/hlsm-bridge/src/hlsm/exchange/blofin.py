"""BloFin perp exchange via ccxt. Demo + live mode via env flip.

Per the existing blofin-bridge reference memory:
- ccxt.set_sandbox_mode(True) raises NotSupported for BloFin
- Manually swap `client.urls["api"]["rest"]` to the demo URL when env == "demo"
- BloFin has no native trailing stop; SL/TP are placed as separate algo orders
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any

import ccxt

from hlsm.config import get_settings
from hlsm.exchange.base import Exchange, ExchangeError
from hlsm.exchange.types import (
    Balance,
    OrderRequest,
    OrderResult,
    PerpInfo,
    PositionInfo,
    SLTPResult,
    Side,
)

log = logging.getLogger(__name__)


def _build_ccxt_client() -> Any:
    s = get_settings()
    client = ccxt.blofin({
        "apiKey": s.blofin_api_key,
        "secret": s.blofin_api_secret,
        "password": s.blofin_api_passphrase,
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })
    if s.blofin_env == "demo":
        # ccxt set_sandbox_mode(True) raises NotSupported for BloFin; manual URL swap is the supported path.
        client.urls["api"] = {"rest": s.blofin_demo_rest_url}
    return client


# Tokens BloFin trades in 1000-unit batches because their price-per-token is too small
# for normal lot sizing. Hyperliquid (and most other venues) trade them 1-for-1, so we
# need to alias when crossing the boundary.
COIN_ALIAS_TO_VENUE: dict[str, str] = {
    "BONK": "1000BONK",
    "FLOKI": "1000FLOKI",
    # add more if BloFin lists 1000X-USDT for other tokens we want to trade
}
COIN_ALIAS_TO_CANONICAL: dict[str, str] = {v: k for k, v in COIN_ALIAS_TO_VENUE.items()}


def _canonical_to_venue_symbol(coin: str) -> str:
    """Canonical 'BONK' -> BloFin '1000BONK-USDT'. Plain coins pass through unchanged."""
    base = coin.upper()
    venue_base = COIN_ALIAS_TO_VENUE.get(base, base)
    return f"{venue_base}-USDT"


def _venue_to_canonical_symbol(venue_symbol: str) -> str:
    """BloFin '1000BONK-USDT' or '1000BONK/USDT:USDT' -> canonical 'BONK'."""
    base = venue_symbol.split("-")[0].split("/")[0].upper()
    return COIN_ALIAS_TO_CANONICAL.get(base, base)


class BloFinExchange(Exchange):
    """BloFin perp adapter via ccxt. Symbol convention is canonical coin name like 'PEPE'."""

    name = "blofin"

    def __init__(self, *, client: Any | None = None) -> None:
        self._client = client if client is not None else _build_ccxt_client()
        self._markets_cache: dict[str, dict[str, Any]] | None = None

    def _markets(self) -> dict[str, dict[str, Any]]:
        if self._markets_cache is None:
            self._markets_cache = self._client.load_markets()
        return self._markets_cache

    # ---- Exchange contract ----

    def list_perps(self) -> list[PerpInfo]:
        markets = self._markets()
        out: list[PerpInfo] = []
        for sym, m in markets.items():
            if not m.get("swap"):
                continue
            quote = (m.get("quote") or "").upper()
            if quote != "USDT":
                continue
            base = (m.get("base") or "").upper()
            if not base:
                continue
            canonical = COIN_ALIAS_TO_CANONICAL.get(base, base)
            out.append(PerpInfo(
                symbol=canonical,
                venue_symbol=str(m.get("id") or sym),
                contract_value=Decimal(str(m.get("contractSize") or 1)),
                min_size=Decimal(str((m.get("limits") or {}).get("amount", {}).get("min") or "0.01")),
                lot_size=Decimal(str((m.get("precision") or {}).get("amount") or "0.01")),
                tick_size=Decimal(str((m.get("precision") or {}).get("price") or "0.00000001")),
                max_leverage=int((m.get("limits") or {}).get("leverage", {}).get("max") or 50),
            ))
        return out

    def get_balance(self) -> Balance:
        bal = self._client.fetch_balance(params={"type": "swap"})
        usdt = bal.get("USDT") or bal.get("total", {}).get("USDT") or {}
        total = Decimal(str(usdt.get("total") or bal.get("total", {}).get("USDT") or 0))
        free = Decimal(str(usdt.get("free") or bal.get("free", {}).get("USDT") or 0))
        used = Decimal(str(usdt.get("used") or bal.get("used", {}).get("USDT") or 0))
        return Balance(total_usdt=total, free_usdt=free, used_usdt=used)

    def get_position(self, coin: str) -> PositionInfo | None:
        venue_sym = _canonical_to_venue_symbol(coin)
        try:
            positions = self._client.fetch_positions(symbols=[venue_sym])
        except Exception as e:  # noqa: BLE001
            raise ExchangeError(f"fetch_positions failed for {coin}: {e}") from e
        for p in positions or []:
            contracts = Decimal(str(p.get("contracts") or 0))
            if contracts == 0:
                continue
            side = Side.LONG if (p.get("side") or "").lower() == "long" else Side.SHORT
            return PositionInfo(
                coin=coin.upper(),
                side=side,
                size=contracts,
                entry_px=Decimal(str(p.get("entryPrice") or 0)),
                mark_px=Decimal(str(p.get("markPrice") or 0)),
                unrealized_pnl_usdt=Decimal(str(p.get("unrealizedPnl") or 0)),
                leverage=int(p.get("leverage") or 1),
            )
        return None

    def place_order(self, req: OrderRequest) -> OrderResult:
        venue_sym = _canonical_to_venue_symbol(req.coin)
        ccxt_side = "buy" if req.side == Side.LONG else "sell"
        # BloFin uses base-unit sizing for amount. Compute base size from margin + leverage.
        ticker = self._client.fetch_ticker(venue_sym)
        mark_px = Decimal(str(ticker.get("last") or ticker.get("close") or 0))
        if mark_px <= 0:
            raise ExchangeError(f"could not fetch mark price for {req.coin}")
        notional = req.margin_usdt * Decimal(req.leverage)
        size = (notional / mark_px).quantize(Decimal("0.01"))
        if size <= 0:
            raise ExchangeError(f"computed size <= 0 for {req.coin} (margin={req.margin_usdt} mark={mark_px})")
        # Ensure leverage is set for this market
        try:
            self._client.set_leverage(req.leverage, venue_sym, params={"marginMode": "isolated"})
        except Exception:  # noqa: BLE001
            log.warning("set_leverage failed (may be already set)", exc_info=True)

        params: dict[str, Any] = {"reduceOnly": False, "marginMode": "isolated"}
        if req.client_order_id:
            params["clientOrderId"] = req.client_order_id
        try:
            order = self._client.create_order(venue_sym, "market", ccxt_side, float(size), None, params)
        except Exception as e:  # noqa: BLE001
            raise ExchangeError(f"create_order failed for {req.coin}: {e}") from e

        filled_sz = Decimal(str(order.get("filled") or size))
        avg_px = Decimal(str(order.get("average") or order.get("price") or mark_px))
        notional_filled = (filled_sz * avg_px).quantize(Decimal("0.00000001"))
        fee_usdt = Decimal("0")
        fee = order.get("fee") or {}
        if isinstance(fee, dict) and fee.get("cost"):
            fee_usdt = Decimal(str(fee["cost"]))
        return OrderResult(
            order_id=str(order.get("id") or order.get("orderId") or f"unknown_{int(time.time())}"),
            coin=req.coin.upper(),
            side=req.side,
            filled_size=filled_sz,
            avg_fill_price=avg_px,
            notional_usdt=notional_filled,
            fee_usdt=fee_usdt,
            raw=order,
        )

    def attach_sl_tp(self, *, coin: str, side: Side, entry_px: Decimal,
                     sl_pct: Decimal, tp_pct: Decimal, size: Decimal) -> SLTPResult:
        if side == Side.LONG:
            sl_px = (entry_px * (Decimal(1) - sl_pct / Decimal(100))).quantize(Decimal("0.00000001"))
            tp_px = (entry_px * (Decimal(1) + tp_pct / Decimal(100))).quantize(Decimal("0.00000001"))
            close_side = "sell"
        else:
            sl_px = (entry_px * (Decimal(1) + sl_pct / Decimal(100))).quantize(Decimal("0.00000001"))
            tp_px = (entry_px * (Decimal(1) - tp_pct / Decimal(100))).quantize(Decimal("0.00000001"))
            close_side = "buy"

        venue_sym = _canonical_to_venue_symbol(coin)
        sl_id: str | None = None
        tp_id: str | None = None

        try:
            sl_order = self._client.create_order(
                venue_sym, "market", close_side, float(size), None,
                {"reduceOnly": True, "marginMode": "isolated", "stopLossPrice": float(sl_px)},
            )
            sl_id = str(sl_order.get("id") or sl_order.get("orderId") or "")
        except Exception:  # noqa: BLE001
            log.exception("attach SL failed for %s", coin)

        try:
            tp_order = self._client.create_order(
                venue_sym, "market", close_side, float(size), None,
                {"reduceOnly": True, "marginMode": "isolated", "takeProfitPrice": float(tp_px)},
            )
            tp_id = str(tp_order.get("id") or tp_order.get("orderId") or "")
        except Exception:  # noqa: BLE001
            log.exception("attach TP failed for %s", coin)

        return SLTPResult(sl_order_id=sl_id, tp_order_id=tp_id, sl_px=sl_px, tp_px=tp_px)

    def close_position(self, *, coin: str, reason: str = "manual") -> OrderResult | None:
        pos = self.get_position(coin)
        if pos is None:
            return None
        venue_sym = _canonical_to_venue_symbol(coin)
        close_side = "sell" if pos.side == Side.LONG else "buy"
        try:
            order = self._client.create_order(
                venue_sym, "market", close_side, float(pos.size), None,
                {"reduceOnly": True, "marginMode": "isolated"},
            )
        except Exception as e:  # noqa: BLE001
            raise ExchangeError(f"close_position failed for {coin}: {e}") from e
        avg_px = Decimal(str(order.get("average") or order.get("price") or pos.mark_px))
        size = Decimal(str(order.get("filled") or pos.size))
        return OrderResult(
            order_id=str(order.get("id") or order.get("orderId") or "close"),
            coin=coin.upper(),
            side=pos.side,
            filled_size=size,
            avg_fill_price=avg_px,
            notional_usdt=(size * avg_px).quantize(Decimal("0.00000001")),
            raw=order,
        )

    def cancel_protective_orders(self, *, coin: str) -> int:
        venue_sym = _canonical_to_venue_symbol(coin)
        try:
            opens = self._client.fetch_open_orders(venue_sym) or []
        except Exception:  # noqa: BLE001
            return 0
        cancelled = 0
        for o in opens:
            params = (o.get("info") or {})
            is_protective = params.get("stopLossPrice") or params.get("takeProfitPrice")
            if not is_protective:
                continue
            try:
                self._client.cancel_order(o.get("id"), venue_sym)
                cancelled += 1
            except Exception:  # noqa: BLE001
                continue
        return cancelled
