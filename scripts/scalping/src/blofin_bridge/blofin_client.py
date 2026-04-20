"""BloFin REST client wrapper (ccxt under the hood)."""
from __future__ import annotations
from typing import Any, Optional, TypedDict

import ccxt


class Instrument(TypedDict):
    instId: str
    contractValue: float
    minSize: float
    lotSize: float
    tickSize: float


def _instid_to_ccxt(inst_id: str) -> str:
    """'SOL-USDT' -> 'SOL/USDT:USDT' (ccxt's linear-swap symbol shape)."""
    base, quote = inst_id.split("-")
    return f"{base}/{quote}:{quote}"


def build_ccxt_client(
    *, api_key: str, secret: str, passphrase: str, env: str,
) -> ccxt.Exchange:
    cls = ccxt.blofin
    client = cls({
        "apiKey": api_key,
        "secret": secret,
        "password": passphrase,           # ccxt maps 'password' to BloFin passphrase
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
    })
    if env == "demo":
        # ccxt's set_sandbox_mode(True) raises NotSupported for BloFin,
        # so override the REST URL manually to point at the demo endpoint.
        # Default shape is {'rest': 'https://openapi.blofin.com'}.
        client.urls["api"] = {
            "rest": "https://demo-trading-openapi.blofin.com",
        }
    return client


class BloFinClient:
    def __init__(self, *, ccxt_client: ccxt.Exchange) -> None:
        self._ccxt = ccxt_client
        self._instruments: dict[str, Instrument] = {}

    def load_instruments(self) -> None:
        markets = self._ccxt.load_markets()
        self._instruments.clear()
        for ccxt_sym, m in markets.items():
            inst_id = m.get("id")
            if not inst_id or "-" not in inst_id:
                continue
            limits_amt = (m.get("limits") or {}).get("amount") or {}
            precision = m.get("precision") or {}
            self._instruments[inst_id] = Instrument(
                instId=inst_id,
                contractValue=float(m.get("contractSize") or 1.0),
                minSize=float(limits_amt.get("min") or 1.0),
                lotSize=float(precision.get("amount") or 1.0),
                tickSize=float(precision.get("price") or 0.001),
            )

    def get_instrument(self, inst_id: str) -> Instrument:
        if inst_id not in self._instruments:
            raise KeyError(f"instrument {inst_id} not loaded")
        return self._instruments[inst_id]

    def set_leverage(
        self, inst_id: str, *, leverage: int, margin_mode: str,
    ) -> None:
        ccxt_sym = _instid_to_ccxt(inst_id)
        self._ccxt.set_leverage(
            leverage, ccxt_sym,
            params={"marginMode": margin_mode, "positionSide": "net"},
        )

    def place_market_entry(
        self, *, inst_id: str, side: str, contracts: float,
        safety_sl_trigger: float,
    ) -> dict[str, Any]:
        """Market entry with an attached safety SL (OCO-style).

        BloFin's create_order response does not populate ``average``/``filled``
        for market orders, so we resolve the actual fill price by reading the
        resulting position via ``fetch_positions``.
        """
        ccxt_sym = _instid_to_ccxt(inst_id)
        params = {
            "marginMode": "isolated",
            "positionSide": "net",
            "slTriggerPrice": safety_sl_trigger,
            "slOrderPrice": "-1",         # -1 => market execution of SL
        }
        order = self._ccxt.create_order(
            symbol=ccxt_sym, type="market", side=side,
            amount=contracts, price=None, params=params,
        )
        fill_price = float(order.get("average") or order.get("price") or 0)
        if fill_price == 0:
            # Resolve by reading the live position the order just created.
            fill_price = self._fetch_position_entry(ccxt_sym) or 0.0
        return {
            "orderId": order.get("id"),
            "fill_price": fill_price,
            "filled": float(order.get("filled") or contracts),
        }

    def _fetch_position_entry(self, ccxt_sym: str) -> float | None:
        try:
            for p in self._ccxt.fetch_positions([ccxt_sym]):
                if (p.get("contracts") or 0) != 0:
                    ep = p.get("entryPrice")
                    if ep:
                        return float(ep)
        except Exception:
            return None
        return None

    def place_sl_order(
        self, *, inst_id: str, side: str, trigger_price: float,
        margin_mode: str,
    ) -> str:
        """Standalone SL on the entire position. Returns tpslId."""
        if trigger_price <= 0:
            raise ValueError(
                f"refusing to place SL with non-positive trigger {trigger_price}"
            )
        # Round to instrument tick precision so BloFin accepts the request.
        try:
            tick = self._instruments[inst_id]["tickSize"]
        except KeyError:
            tick = 0.0
        if tick > 0:
            steps = round(trigger_price / tick)
            trigger_price = round(steps * tick, 10)
        resp = self._ccxt.private_post_trade_order_tpsl({
            "instId": inst_id,
            "marginMode": margin_mode,
            "positionSide": "net",
            "side": side,
            "slTriggerPrice": str(trigger_price),
            "slOrderPrice": "-1",
            "size": "-1",                  # -1 = full position
            "reduceOnly": "true",
        })
        if resp.get("code") not in ("0", 0):
            raise RuntimeError(f"place_sl_order failed: {resp}")
        data = resp.get("data")
        if isinstance(data, list):
            data = data[0] if data else {}
        return (data or {}).get("tpslId", "")

    def place_limit_reduce_only(
        self, *, inst_id: str, side: str, contracts: float, price: float,
    ) -> str:
        """Place a reduce-only limit order at a specific price. Returns order id.

        Used for TP1/TP2/TP3 placement by the entry handler.
        `side` is the closing direction: "sell" closes a long, "buy" closes a short.
        """
        if price <= 0:
            raise ValueError(f"price must be positive, got {price}")
        # Round price to instrument tick precision
        try:
            tick = self._instruments[inst_id]["tickSize"]
        except KeyError:
            tick = 0.0
        if tick > 0:
            steps = round(price / tick)
            price = round(steps * tick, 10)

        ccxt_sym = _instid_to_ccxt(inst_id)
        params = {
            "marginMode": "isolated",
            "positionSide": "net",
            "reduceOnly": "true",
        }
        order = self._ccxt.create_order(
            symbol=ccxt_sym, type="limit", side=side,
            amount=contracts, price=price, params=params,
        )
        order_id = order.get("id")
        if not order_id:
            raise RuntimeError(f"place_limit_reduce_only returned no order id: {order}")
        return order_id

    def cancel_order(self, order_id: str, inst_id: str) -> None:
        """Cancel a regular limit/market order (not a tpsl algo)."""
        ccxt_sym = _instid_to_ccxt(inst_id)
        try:
            self._ccxt.cancel_order(order_id, ccxt_sym)
        except Exception as exc:
            raise RuntimeError(f"cancel_order failed for {order_id}: {exc}")

    def cancel_tpsl(self, inst_id: str, tpsl_id: str) -> None:
        # BloFin expects a JSON array body for cancel-tpsl, even for one id.
        resp = self._ccxt.private_post_trade_cancel_tpsl(
            [{"tpslId": tpsl_id, "instId": inst_id}]
        )
        if resp.get("code") not in ("0", 0):
            raise RuntimeError(f"cancel_tpsl failed: {resp}")

    def list_pending_tpsl(self, inst_id: str) -> list[dict[str, Any]]:
        """Return raw pending TP/SL algo orders for the instrument.

        Used after a market entry with attached SL to capture the tpslId that
        BloFin creates implicitly (the create_order response does not expose it).
        """
        try:
            listing = self._ccxt.private_get_trade_orders_tpsl_pending(
                {"instId": inst_id}
            )
        except Exception:
            return []
        return listing.get("data") or []

    def cancel_all_tpsl(self, inst_id: str) -> int:
        """Cancel every pending TP/SL order on the given instrument.

        Returns the number of orders cancelled. BloFin attaches an OCO SL to
        market entries which is not directly tracked by the bridge state, so
        we sweep on every SL replacement to avoid the 102114 'already set'
        error.
        """
        try:
            listing = self._ccxt.private_get_trade_orders_tpsl_pending(
                {"instId": inst_id}
            )
        except Exception:
            return 0
        items = listing.get("data") or []
        cancelled = 0
        for o in items:
            tpsl_id = o.get("tpslId")
            if not tpsl_id:
                continue
            try:
                self.cancel_tpsl(inst_id, tpsl_id)
                cancelled += 1
            except Exception:
                pass
        return cancelled

    def close_position_market(
        self, *, inst_id: str, side: str, contracts: float,
    ) -> dict[str, Any]:
        """Reduce-only market order to close N contracts.

        BloFin omits ``average`` for market fills; fall back to the current
        last price as a close-fill proxy so downstream policies see a usable
        price.
        """
        ccxt_sym = _instid_to_ccxt(inst_id)
        params = {
            "marginMode": "isolated",
            "positionSide": "net",
            "reduceOnly": "true",
        }
        order = self._ccxt.create_order(
            symbol=ccxt_sym, type="market", side=side,
            amount=contracts, price=None, params=params,
        )
        fill_price = float(order.get("average") or order.get("price") or 0)
        if fill_price == 0:
            try:
                t = self._ccxt.fetch_ticker(ccxt_sym)
                fill_price = float(t.get("last") or t.get("close") or 0)
            except Exception:
                fill_price = 0.0
        return {
            "orderId": order.get("id"),
            "fill_price": fill_price,
        }

    def fetch_last_price(self, inst_id: str) -> float:
        ccxt_sym = _instid_to_ccxt(inst_id)
        ticker = self._ccxt.fetch_ticker(ccxt_sym)
        return float(ticker.get("last") or ticker.get("close"))

    def fetch_positions(self) -> list[dict[str, Any]]:
        return self._ccxt.fetch_positions()

    def fetch_order(self, order_id: str, inst_id: str) -> dict[str, Any]:
        """Fetch a single order by id.

        NOTE: ccxt's BloFin adapter does not implement fetchOrder (raises
        'not supported yet'). Prefer fetch_closed_orders / fetch_open_orders
        which ARE supported. This method is kept only for backward-compat
        with any remaining callers and will raise.
        """
        ccxt_sym = _instid_to_ccxt(inst_id)
        return self._ccxt.fetch_order(order_id, ccxt_sym)

    def fetch_closed_orders(
        self, inst_id: str, *, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent closed/filled orders for the instrument.

        Used by the poller to detect TP fills since BloFin's ccxt adapter
        does not support per-id fetchOrder.
        """
        ccxt_sym = _instid_to_ccxt(inst_id)
        return self._ccxt.fetch_closed_orders(ccxt_sym, limit=limit)

    def fetch_recent_ohlcv(
        self, inst_id: str, *, timeframe: str = "5m", limit: int = 20,
    ) -> list[list[float]]:
        """Return the last `limit` OHLCV bars for the instrument.

        Each bar is [timestamp, open, high, low, close, volume].
        """
        ccxt_sym = _instid_to_ccxt(inst_id)
        return self._ccxt.fetch_ohlcv(ccxt_sym, timeframe=timeframe, limit=limit)
