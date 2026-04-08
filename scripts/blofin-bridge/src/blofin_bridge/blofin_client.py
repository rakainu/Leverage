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
