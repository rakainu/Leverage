"""Parse Solana transactions to extract swap/buy events."""

import logging
from datetime import datetime, timezone

import httpx

from engine.signal import BuyEvent
from scanner.rpc_pool import RpcPool
from utils.constants import DEX_PROGRAMS, SOL_MINT, STABLECOIN_MINTS

logger = logging.getLogger("smc.scanner.parser")


class TransactionParser:
    """Fetches a transaction by signature via RPC, decodes swap direction and amounts."""

    def __init__(self, rpc_pool: RpcPool, http: httpx.AsyncClient):
        self.rpc_pool = rpc_pool
        self.http = http

    async def parse_transaction(self, signature: str, wallet_address: str) -> BuyEvent | None:
        """Fetch txn via RPC, return BuyEvent if it's a buy (SOL -> token)."""
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
                    timeout=15,
                )
                data = resp.json()
                result = data.get("result")
                if result:
                    self.rpc_pool.mark_healthy(rpc_url)
                    return self._extract_buy_event(result, signature, wallet_address)

                # Null result — tx may not be available yet, retry after delay
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue

                return None

            except Exception as e:
                self.rpc_pool.mark_failed(rpc_url)
                logger.error(f"Failed to parse txn {signature[:16]}...: {e}")
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
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

        # Debug: log all token changes for this txn
        if token_changes:
            changes_str = ", ".join(
                f"{m[:8]}..={'+'if d>0 else ''}{d:.6f}" for m, d in token_changes.items()
            )
            logger.info(
                f"TXN {signature[:12]}.. wallet={wallet_address[:8]}.. "
                f"sol_change={sol_change:.6f} tokens=[{changes_str}] dex={dex}"
            )
        else:
            logger.info(
                f"TXN {signature[:12]}.. wallet={wallet_address[:8]}.. "
                f"no token changes, sol_change={sol_change:.6f}"
            )

        # Find the memecoin they gained (positive delta, skip SOL/stablecoins/LSTs)
        gained_token = None
        gained_amount = 0.0
        for mint, delta in token_changes.items():
            if mint not in STABLECOIN_MINTS and delta > 0:
                gained_token = mint
                gained_amount = delta
                break

        if not gained_token:
            return None

        # Determine spend: SOL spent directly, or stablecoin spent (USDC/USDT → memecoin)
        sol_spent = 0.0
        if sol_change < 0:
            sol_spent = abs(sol_change)
        else:
            # Check if they spent a stablecoin (negative delta on USDC/USDT)
            for mint, delta in token_changes.items():
                if mint in STABLECOIN_MINTS and delta < 0:
                    # Approximate SOL value: treat stablecoin as ~SOL equivalent
                    sol_spent = abs(delta) / 150.0  # rough USD→SOL conversion
                    break

        if sol_spent <= 0:
            return None

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
