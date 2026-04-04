"""Birdeye API client for wallet PnL data, trending tokens, and token security."""

import asyncio
import logging

import httpx

logger = logging.getLogger("smc.curation.birdeye")


class BirdeyeClient:
    """Birdeye Data Services API client.

    Free tier: 60 rpm. Provides wallet PnL, trending tokens, token security,
    and price data across Solana and other chains.
    """

    BASE_URL = "https://public-api.birdeye.so"

    def __init__(self, api_key: str, http: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.http = http or httpx.AsyncClient(timeout=30)
        self._headers = {
            "X-API-KEY": api_key,
            "x-chain": "solana",
            "Accept": "application/json",
        }
        self._call_count = 0
        self._rate_limit = 55  # Stay under 60 rpm

    async def _rate_wait(self):
        """Simple rate limiter — wait if approaching limit."""
        self._call_count += 1
        if self._call_count % self._rate_limit == 0:
            logger.debug("Rate limit pause (1 min)...")
            await asyncio.sleep(62)

    async def _get(self, endpoint: str, params: dict | None = None) -> dict | None:
        """Make authenticated GET request to Birdeye API."""
        await self._rate_wait()
        try:
            resp = await self.http.get(
                f"{self.BASE_URL}{endpoint}",
                headers=self._headers,
                params=params,
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"Birdeye {endpoint} returned {resp.status_code}")
            if resp.status_code == 429:
                logger.warning("Rate limited — waiting 60s")
                await asyncio.sleep(62)
            return None
        except Exception as e:
            logger.error(f"Birdeye {endpoint} error: {e}")
            return None

    # --- Wallet endpoints ---

    async def wallet_pnl(self, address: str) -> dict | None:
        """Get wallet profit/loss summary.

        Returns: {total_pnl, win_rate, total_trades, ...}
        """
        data = await self._get(f"/wallet/v2/pnl/summary", {"wallet": address})
        if not data or not data.get("success"):
            return None
        pnl = data.get("data", {})
        return {
            "address": address,
            "total_pnl_sol": float(pnl.get("total_pnl", 0) or 0),
            "total_pnl_usd": float(pnl.get("total_pnl_usd", 0) or 0),
            "win_rate": float(pnl.get("win_rate", 0) or 0),
            "total_trades": int(pnl.get("total_trades", 0) or 0),
            "wins": int(pnl.get("total_wins", 0) or 0),
            "losses": int(pnl.get("total_losses", 0) or 0),
        }

    async def wallet_portfolio(self, address: str) -> dict | None:
        """Get wallet current holdings."""
        data = await self._get("/v1/wallet/token_list", {"wallet": address})
        if not data or not data.get("success"):
            return None
        return data.get("data", {})

    # --- Token endpoints ---

    async def trending_tokens(self, limit: int = 20) -> list[dict]:
        """Get trending tokens on Solana."""
        data = await self._get("/defi/token_trending", {"limit": str(limit)})
        if not data or not data.get("success"):
            return []
        tokens = data.get("data", {}).get("tokens", data.get("data", {}).get("items", []))
        return [
            {
                "address": t.get("address", ""),
                "symbol": t.get("symbol", ""),
                "name": t.get("name", ""),
                "price": float(t.get("price", 0) or 0),
                "volume_24h": float(t.get("v24hUSD", 0) or 0),
                "price_change_24h": float(t.get("v24hChangePercent", 0) or 0),
            }
            for t in tokens
            if t.get("address")
        ]

    async def token_security(self, address: str) -> dict | None:
        """Get token security assessment."""
        data = await self._get("/defi/token_security", {"address": address})
        if not data or not data.get("success"):
            return None
        sec = data.get("data", {})
        return {
            "mint_authority": sec.get("mutableMetadata"),
            "freeze_authority": sec.get("freezeable"),
            "top_10_holder_pct": float(sec.get("top10HolderPercent", 0) or 0),
            "is_token_2022": sec.get("isToken2022", False),
            "transfer_fee": float(sec.get("transferFeeEnable", 0) or 0),
        }

    async def token_price(self, address: str) -> float | None:
        """Get current token price in USD."""
        data = await self._get("/defi/price", {"address": address})
        if not data or not data.get("success"):
            return None
        return float(data.get("data", {}).get("value", 0) or 0)

    async def token_overview(self, address: str) -> dict | None:
        """Get full token overview (price, volume, holders, etc)."""
        data = await self._get("/defi/token_overview", {"address": address})
        if not data or not data.get("success"):
            return None
        return data.get("data", {})

    # --- Wallet discovery ---

    async def get_top_traders(self, token_address: str, limit: int = 20) -> list[dict]:
        """Get top traders for a token.

        Uses token trade data to find wallets with best PnL on this token.
        """
        data = await self._get(
            "/defi/v3/token/trade-data/single",
            {"address": token_address, "limit": str(limit), "type": "swap"},
        )
        if not data or not data.get("success"):
            return []
        trades = data.get("data", {}).get("items", [])
        # Extract unique wallet addresses from trades
        wallets = {}
        for trade in trades:
            owner = trade.get("owner", "")
            if owner and owner not in wallets:
                wallets[owner] = {
                    "address": owner,
                    "side": trade.get("side", ""),
                    "volume_usd": float(trade.get("volumeUSD", 0) or 0),
                }
        return list(wallets.values())
