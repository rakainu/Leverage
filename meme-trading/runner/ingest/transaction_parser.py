"""Parse Solana transactions into BuyEvents using Helius RPC getTransaction."""
import asyncio
from datetime import datetime, timezone
from typing import Any

from runner.ingest.events import BuyEvent
from runner.ingest.rpc_pool import RpcPool
from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger
from runner.utils.solana import is_quote_mint

logger = get_logger("runner.ingest.parser")

# Sleep interval between retries when RPC returns ``result: null``
# (tx not yet propagated to the node we hit). Exposed as a module-level
# constant so tests can monkeypatch it for speed.
NULL_RETRY_SLEEP_SEC = 1.5


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
        max_attempts = 3
        for attempt in range(max_attempts):
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
                if "result" not in data:
                    # Malformed response — not retryable.
                    return None
                result = data["result"]
                if result is None:
                    # Tx not yet propagated to this node — real buys often
                    # lag the logsSubscribe notification by a slot or two.
                    # Retry with a short delay before giving up.
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(NULL_RETRY_SLEEP_SEC)
                        continue
                    return None
                self.rpc_pool.mark_healthy(rpc_url)
                return self._extract_buy_event(result, signature, wallet_address)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "parse_transaction_retry",
                    signature=signature,
                    attempt=attempt,
                    error=str(e),
                )
                self.rpc_pool.mark_unhealthy(rpc_url)
        return None

    def _find_account_index(
        self, result: dict[str, Any], wallet_address: str
    ) -> int | None:
        """Return the index of ``wallet_address`` in ``transaction.message.accountKeys``.

        Handles both the modern jsonParsed shape (list of dicts with ``pubkey``)
        and the older shape (bare pubkey strings).
        """
        account_keys = (
            (result.get("transaction") or {}).get("message", {}).get("accountKeys", [])
        )
        for idx, key in enumerate(account_keys):
            if isinstance(key, dict):
                if key.get("pubkey") == wallet_address:
                    return idx
            elif key == wallet_address:
                return idx
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
            deltas.setdefault(mint, {"pre": 0.0, "post": 0.0})["pre"] += amount

        for entry in post:
            if entry.get("owner") != wallet_address:
                continue
            mint = entry.get("mint")
            if not mint:
                continue
            amount = float((entry.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            deltas.setdefault(mint, {"pre": 0.0, "post": 0.0})["post"] += amount

        sol_out: float | None = None
        token_candidates: list[tuple[str, float]] = []

        for mint, d in deltas.items():
            change = d["post"] - d["pre"]
            if is_quote_mint(mint) and change < 0:
                sol_out = (sol_out or 0.0) + (-change)
            elif not is_quote_mint(mint) and change > 0:
                token_candidates.append((mint, change))

        # Native SOL fallback: if no wSOL ATA debit was observed, check the
        # wallet's lamport balance change in meta.preBalances/postBalances.
        # Real Solana buys usually pay with native SOL, not wSOL.
        if not sol_out:
            idx = self._find_account_index(result, wallet_address)
            if idx is not None:
                pre_balances = meta.get("preBalances") or []
                post_balances = meta.get("postBalances") or []
                if idx < len(pre_balances) and idx < len(post_balances):
                    fee_lamports = int(meta.get("fee") or 0)
                    sol_change_lamports = pre_balances[idx] - post_balances[idx]
                    sol_out_candidate = (
                        sol_change_lamports - fee_lamports
                    ) / 1_000_000_000
                    # Threshold avoids flagging rent/fee-only movements
                    # as a buy when no real SOL was spent.
                    if sol_out_candidate > 0.001:
                        sol_out = sol_out_candidate

        if len(token_candidates) == 0:
            return None

        if len(token_candidates) > 1:
            logger.warning(
                "ambiguous_token_deltas",
                signature=signature,
                wallet=wallet_address,
                candidates=[(m, a) for m, a in token_candidates],
            )
            return None

        token_in_mint, token_in_amount = token_candidates[0]

        if not sol_out or token_in_amount <= 0:
            return None

        price_sol = sol_out / token_in_amount
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
