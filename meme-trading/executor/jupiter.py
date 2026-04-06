"""Jupiter Swap API client for price quotes and trade execution."""

import logging

import httpx

from utils.constants import SOL_MINT

logger = logging.getLogger("smc.executor.jupiter")


class JupiterClient:
    """Client for Jupiter Swap API — quotes, prices, and swap execution."""

    QUOTE_URL = "https://api.jup.ag/swap/v1/quote"
    SWAP_URL = "https://api.jup.ag/swap/v1/swap"

    def __init__(self, api_key: str = "", http: httpx.AsyncClient | None = None):
        self.api_key = api_key
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key
        self.http = http or httpx.AsyncClient(timeout=30, headers=headers)

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

        Tries Jupiter first, falls back to DexScreener.
        Returns SOL per token (e.g., 0.000001 SOL per token).
        """
        # Try Jupiter first
        price = await self._get_price_jupiter(token_mint)
        if price:
            return price

        # Fallback: DexScreener
        price = await self._get_price_dexscreener(token_mint)
        if price:
            logger.info(f"Price from DexScreener for {token_mint[:12]}..")
        return price

    async def _get_price_jupiter(self, token_mint: str) -> float | None:
        """Get price via Jupiter quote API."""
        test_lamports = 100_000_000  # 0.1 SOL
        quote = await self.get_quote(SOL_MINT, token_mint, test_lamports)
        if not quote:
            return None

        out_amount = int(quote.get("outAmount", 0))
        if out_amount == 0:
            return None

        return 0.1 / out_amount

    async def _get_price_dexscreener(self, token_mint: str) -> float | None:
        """Get price via DexScreener API (free, no key)."""
        try:
            resp = await self.http.get(
                f"https://api.dexscreener.com/tokens/v1/solana/{token_mint}",
                timeout=10,
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            pairs = data if isinstance(data, list) else data.get("pairs") or data.get("pair") or []
            if isinstance(pairs, dict):
                pairs = [pairs]

            for pair in pairs:
                price_native = pair.get("priceNative")
                if price_native:
                    return float(price_native)
        except Exception as e:
            logger.error(f"DexScreener price error: {e}")
        return None

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
