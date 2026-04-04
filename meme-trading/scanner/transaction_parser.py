"""Parse Solana transactions to extract swap/buy events."""

import logging
from datetime import datetime, timezone

import httpx

from engine.signal import BuyEvent
from scanner.rpc_pool import RpcPool
from utils.constants import DEX_PROGRAMS, SOL_MINT

logger = logging.getLogger("smc.scanner.parser")


class TransactionParser:
    """Fetches a transaction by signature via RPC, decodes swap direction and amounts."""

    def __init__(self, rpc_pool: RpcPool, http: httpx.AsyncClient):
        self.rpc_pool = rpc_pool
        self.http = http

    async def parse_transaction(self, signature: str, wallet_address: str) -> BuyEvent | None:
        """Fetch txn via RPC, return BuyEvent if it's a buy (SOL -> token)."""
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
                        },
                    ],
                },
                timeout=15,
            )
            data = resp.json()
            result = data.get("result")
            if not result:
                return None

            self.rpc_pool.mark_healthy(rpc_url)
            return self._extract_buy_event(result, signature, wallet_address)

        except Exception as e:
            self.rpc_pool.mark_failed(rpc_url)
            logger.error(f"Failed to parse txn {signature[:16]}...: {e}")
            return None

    def _extract_buy_event(self, txn: dict, signature: str, wallet_address: str) -> BuyEvent | None:
        """Extract buy event from parsed transaction data."""
        meta = txn.get("meta")
        if not meta or meta.get("err"):
            return None

        # Detect DEX
        account_keys = self._get_account_keys(txn)
        dex = self._detect_dex(account_keys)

        # Get token balance changes for our wallet
        token_changes = self._get_token_balance_changes(meta, wallet_address)
        sol_change = self._get_sol_change(meta, wallet_address, account_keys)

        if not token_changes or sol_change >= 0:
            # No token gained or didn't spend SOL — not a buy
            return None

        # Find the token they gained (positive delta, not SOL)
        gained_token = None
        gained_amount = 0.0
        for mint, delta in token_changes.items():
            if mint != SOL_MINT and delta > 0:
                gained_token = mint
                gained_amount = delta
                break

        if not gained_token:
            return None

        sol_spent = abs(sol_change)

        # Get timestamp
        block_time = txn.get("blockTime")
        ts = datetime.fromtimestamp(block_time, tz=timezone.utc) if block_time else datetime.now(timezone.utc)

        return BuyEvent(
            wallet_address=wallet_address,
            token_mint=gained_token,
            token_symbol=None,  # We'll resolve symbols later if needed
            amount_sol=sol_spent,
            amount_tokens=gained_amount,
            signature=signature,
            timestamp=ts,
            dex=dex,
        )

    def _get_account_keys(self, txn: dict) -> list[str]:
        """Extract all account keys from the transaction."""
        message = txn.get("transaction", {}).get("message", {})
        keys = []
        for key in message.get("accountKeys", []):
            if isinstance(key, dict):
                keys.append(key.get("pubkey", ""))
            else:
                keys.append(str(key))
        return keys

    def _detect_dex(self, account_keys: list[str]) -> str:
        """Identify which DEX was used from the program IDs in the transaction."""
        for key in account_keys:
            if key in DEX_PROGRAMS:
                return DEX_PROGRAMS[key]
        return "unknown"

    def _get_token_balance_changes(self, meta: dict, wallet: str) -> dict[str, float]:
        """Return {mint: delta_amount} for token balance changes of the target wallet."""
        pre = {}
        for b in meta.get("preTokenBalances", []):
            if b.get("owner") == wallet:
                amount = b.get("uiTokenAmount", {}).get("uiAmount") or 0
                pre[b["mint"]] = amount

        post = {}
        for b in meta.get("postTokenBalances", []):
            if b.get("owner") == wallet:
                amount = b.get("uiTokenAmount", {}).get("uiAmount") or 0
                post[b["mint"]] = amount

        changes = {}
        for mint in set(pre) | set(post):
            delta = (post.get(mint) or 0) - (pre.get(mint) or 0)
            if delta != 0:
                changes[mint] = delta
        return changes

    def _get_sol_change(self, meta: dict, wallet: str, account_keys: list[str]) -> float:
        """Get SOL balance change in SOL (not lamports) for the wallet."""
        try:
            idx = account_keys.index(wallet)
        except ValueError:
            return 0

        pre_balances = meta.get("preBalances", [])
        post_balances = meta.get("postBalances", [])

        if idx >= len(pre_balances) or idx >= len(post_balances):
            return 0

        delta_lamports = post_balances[idx] - pre_balances[idx]
        return delta_lamports / 1e9  # Convert to SOL
