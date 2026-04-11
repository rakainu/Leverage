"""Parse Solana transactions into BuyEvents using Helius RPC getTransaction."""
from datetime import datetime, timezone
from typing import Any

from runner.ingest.events import BuyEvent
from runner.ingest.rpc_pool import RpcPool
from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger
from runner.utils.solana import is_quote_mint

logger = get_logger("runner.ingest.parser")


class TransactionParser:
    """Fetch a signed transaction via RPC and, if it's a buy, return a BuyEvent.

    "Buy" = wallet's SOL/stablecoin balance went DOWN and a non-quote token
    balance went UP within the same transaction.
    """

    def __init__(self, rpc_pool: RpcPool, http: RateLimitedClient):
        self.rpc_pool = rpc_pool
        self.http = http

    async def parse_transaction(
        self, signature: str, wallet_address: str
    ) -> BuyEvent | None:
        for attempt in range(3):
            rpc_url = self.rpc_pool.next()
            try:
                resp = await self.http.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [
                            signature,
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0,
                                "commitment": "confirmed",
                            },
                        ],
                    },
                )
                data = resp.json()
                if "result" not in data or data["result"] is None:
                    return None
                self.rpc_pool.mark_healthy(rpc_url)
                return self._extract_buy_event(data["result"], signature, wallet_address)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "parse_transaction_retry",
                    signature=signature,
                    attempt=attempt,
                    error=str(e),
                )
                self.rpc_pool.mark_unhealthy(rpc_url)
        return None

    def _extract_buy_event(
        self, result: dict[str, Any], signature: str, wallet_address: str
    ) -> BuyEvent | None:
        meta = result.get("meta") or {}
        if meta.get("err") is not None:
            return None

        pre = meta.get("preTokenBalances") or []
        post = meta.get("postTokenBalances") or []

        deltas: dict[str, dict[str, float]] = {}

        for entry in pre:
            if entry.get("owner") != wallet_address:
                continue
            mint = entry.get("mint")
            if not mint:
                continue
            amount = float((entry.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            deltas.setdefault(mint, {"pre": 0.0, "post": 0.0})["pre"] = amount

        for entry in post:
            if entry.get("owner") != wallet_address:
                continue
            mint = entry.get("mint")
            if not mint:
                continue
            amount = float((entry.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            deltas.setdefault(mint, {"pre": 0.0, "post": 0.0})["post"] = amount

        sol_out: float | None = None
        token_in_mint: str | None = None
        token_in_amount: float | None = None

        for mint, d in deltas.items():
            change = d["post"] - d["pre"]
            if is_quote_mint(mint) and change < 0:
                sol_out = (sol_out or 0.0) + (-change)
            elif not is_quote_mint(mint) and change > 0:
                if token_in_amount is None or change > token_in_amount:
                    token_in_mint = mint
                    token_in_amount = change

        if not sol_out or not token_in_mint or not token_in_amount:
            return None

        price_sol = sol_out / token_in_amount if token_in_amount > 0 else 0.0
        block_time = datetime.fromtimestamp(
            int(result.get("blockTime") or 0), tz=timezone.utc
        )

        return BuyEvent(
            signature=signature,
            wallet_address=wallet_address,
            token_mint=token_in_mint,
            sol_amount=sol_out,
            token_amount=token_in_amount,
            price_sol=price_sol,
            block_time=block_time,
        )
