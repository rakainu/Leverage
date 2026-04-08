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
        self, *, inst_id: str, side: str, contracts: int,
        safety_sl_trigger: float,
    ) -> dict[str, Any]:
        """Market entry with an attached safety SL (OCO-style)."""
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
        return {
            "orderId": order.get("id"),
            "fill_price": float(order.get("average") or order.get("price") or 0),
            "filled": float(order.get("filled") or 0),
        }

    def place_sl_order(
        self, *, inst_id: str, side: str, trigger_price: float,
        margin_mode: str,
    ) -> str:
        """Standalone SL on the entire position. Returns tpslId."""
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
        return resp["data"][0]["tpslId"]

    def cancel_tpsl(self, inst_id: str, tpsl_id: str) -> None:
        resp = self._ccxt.private_post_trade_cancel_tpsl({
            "tpslId": tpsl_id, "instId": inst_id,
        })
        if resp.get("code") not in ("0", 0):
            raise RuntimeError(f"cancel_tpsl failed: {resp}")

    def close_position_market(
        self, *, inst_id: str, side: str, contracts: int,
    ) -> dict[str, Any]:
        """Reduce-only market order to close N contracts."""
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
        return {
            "orderId": order.get("id"),
            "fill_price": float(order.get("average") or order.get("price") or 0),
        }

    def fetch_last_price(self, inst_id: str) -> float:
        ccxt_sym = _instid_to_ccxt(inst_id)
        ticker = self._ccxt.fetch_ticker(ccxt_sym)
        return float(ticker.get("last") or ticker.get("close"))

    def fetch_positions(self) -> list[dict[str, Any]]:
        return self._ccxt.fetch_positions()
