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
        self._rate_limit = 12  # Free tier: ~15 rpm for wallet endpoints, stay safe

    async def _rate_wait(self):
        """Simple rate limiter — wait if approaching limit."""
        self._call_count += 1
        if self._call_count % self._rate_limit == 0:
            logger.debug("Rate limit pause (1 min)...")
            await asyncio.sleep(62)

    async def _get(self, endpoint: str, params: dict | None = None) -> dict | None:
        """Make authenticated GET request to Birdeye API with retry on 429."""
        for attempt in range(3):
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
                if resp.status_code == 429:
                    wait = 65 * (attempt + 1)
                    logger.warning(f"Rate limited on {endpoint} — waiting {wait}s (attempt {attempt+1}/3)")
                    await asyncio.sleep(wait)
                    continue
                logger.warning(f"Birdeye {endpoint} returned {resp.status_code}")
                return None
            except Exception as e:
                logger.error(f"Birdeye {endpoint} error: {e}")
                return None
        return None

    # --- Wallet endpoints ---

    async def wallet_pnl(self, address: str) -> dict | None:
        """Get wallet profit/loss summary.

        Returns: {total_pnl_usd, win_rate, total_trades, ...}
        """
        data = await self._get("/wallet/v2/pnl/summary", {"wallet": address})
        if not data:
            return None
        summary = data.get("data", {}).get("summary", data.get("data", {}))
        counts = summary.get("counts", {})
        pnl = summary.get("pnl", {})
        cashflow = summary.get("cashflow_usd", {})

        total_trades = int(counts.get("total_trade", 0) or 0)
        wins = int(counts.get("total_win", 0) or 0)
        losses = int(counts.get("total_loss", 0) or 0)
        win_rate = float(counts.get("win_rate", 0) or 0)
        realized_pnl = float(pnl.get("realized_profit_usd", 0) or 0)
        total_pnl = float(pnl.get("total_usd", 0) or 0)
        total_invested = float(cashflow.get("total_invested", 0) or 0)

        return {
            "address": address,
            "total_pnl_usd": total_pnl,
            "realized_pnl_usd": realized_pnl,
            "total_invested_usd": total_invested,
            "win_rate": win_rate,
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "unique_tokens": int(summary.get("unique_tokens", 0) or 0),
        }

    async def wallet_portfolio(self, address: str) -> dict | None:
        """Get wallet current holdings."""
        data = await self._get("/v1/wallet/token_list", {"wallet": address})
        if not data or not data.get("success"):
            return None
        return data.get("data", {})

    # --- Token endpoints ---

    async def trending_tokens(self, limit: int = 20) -> list[dict]:
        """Get trending tokens on Solana. Paginates in chunks of 20 (API max)."""
        all_tokens = []
        fetched = 0
        while fetched < limit:
            page_size = min(20, limit - fetched)
            data = await self._get(
                "/defi/token_trending",
                {"sort_by": "rank", "sort_type": "asc", "offset": str(fetched), "limit": str(page_size)},
            )
            if not data:
                break
            tokens = data.get("data", {}).get("tokens", data.get("data", {}).get("items", []))
            if not tokens:
                break
            for t in tokens:
                if t.get("address"):
                    all_tokens.append({
                        "address": t["address"],
                        "symbol": t.get("symbol", ""),
                        "name": t.get("name", ""),
                        "price": float(t.get("price", 0) or 0),
                        "volume_24h": float(t.get("volume24hUSD", 0) or t.get("v24hUSD", 0) or 0),
                        "price_change_24h": float(t.get("volume24hChangePercent", 0) or t.get("v24hChangePercent", 0) or 0),
                    })
            fetched += len(tokens)
        return all_tokens

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

    async def get_top_traders(self, token_address: str, pages: int = 1) -> list[dict]:
        """Get top traders for a token (max 10 per page, API limit).

        pages=1 for fast discovery (10 traders), pages=3 for deep (30 traders).
        """
        all_traders = []
        for offset in range(0, pages * 10, 10):
            data = await self._get(
                "/defi/v2/tokens/top_traders",
                {
                    "address": token_address,
                    "time_frame": "24h",
                    "sort_type": "desc",
                    "sort_by": "volume",
                    "offset": str(offset),
                    "limit": "10",
                },
            )
            if not data:
                break
            items = data.get("data", {}).get("items", [])
            if not items:
                break
            for t in items:
                owner = t.get("owner", "")
                if owner:
                    all_traders.append({
                        "address": owner,
                        "volume": float(t.get("volume", 0) or 0),
                        "trades": int(t.get("trade", 0) or 0),
                        "buys": int(t.get("tradeBuy", 0) or 0),
                        "sells": int(t.get("tradeSell", 0) or 0),
                    })
        return all_traders
