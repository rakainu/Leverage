"""Jupiter Swap API client for price quotes and trade execution."""

import logging

import httpx

from utils.constants import SOL_MINT

logger = logging.getLogger("smc.executor.jupiter")


class JupiterClient:
    """Client for Jupiter Swap API — quotes, prices, and swap execution."""

    QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
    SWAP_URL = "https://quote-api.jup.ag/v6/swap"

    def __init__(self, api_key: str = "", http: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.http = http or httpx.AsyncClient(timeout=30)

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 300,
    ) -> dict | None:
        """Get a swap quote from Jupiter."""
        try:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "slippageBps": str(slippage_bps),
            }
            resp = await self.http.get(self.QUOTE_URL, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"Jupiter quote failed: {resp.status_code}")
        except Exception as e:
            logger.error(f"Jupiter quote error: {e}")
        return None

    async def get_price_sol(self, token_mint: str) -> float | None:
        """Get the current price of a token in SOL.

        Returns SOL per token (e.g., 0.000001 SOL per token).
        """
        # Quote: how many tokens do we get for 0.1 SOL?
        test_lamports = 100_000_000  # 0.1 SOL
        quote = await self.get_quote(SOL_MINT, token_mint, test_lamports)
        if not quote:
            return None

        out_amount = int(quote.get("outAmount", 0))
        if out_amount == 0:
            return None

        # Price = SOL spent / tokens received
        # But we need to account for decimals
        # outAmount is in raw token units
        # We return price in SOL per raw-unit for consistency with entry tracking
        return 0.1 / out_amount

    async def get_swap_transaction(
        self,
        quote_response: dict,
        user_pubkey: str,
    ) -> str | None:
        """Build a swap transaction from a quote. Returns base64-encoded transaction."""
        try:
            payload = {
                "quoteResponse": quote_response,
                "userPublicKey": user_pubkey,
                "wrapUnwrapSOL": True,
            }
            resp = await self.http.post(
                self.SWAP_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("swapTransaction")
            logger.warning(f"Jupiter swap build failed: {resp.status_code}")
        except Exception as e:
            logger.error(f"Jupiter swap error: {e}")
        return None
