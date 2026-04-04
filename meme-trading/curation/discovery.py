"""Discover profitable wallets via GMGN API (Dragon-style patterns)."""

import asyncio
import logging
import random

import httpx

from config.settings import Settings

logger = logging.getLogger("smc.curation.discovery")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


class WalletDiscovery:
    """Discovers profitable wallets by scraping GMGN's API endpoints."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.base_url = settings.gmgn_base_url if hasattr(settings, 'gmgn_base_url') else "https://gmgn.ai"
        self.http = http or httpx.AsyncClient(timeout=30)

    def _headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": self.base_url,
        }

    async def top_traders_for_token(self, token_mint: str) -> list[dict]:
        """Get top traders for a specific token from GMGN."""
        url = f"{self.base_url}/defi/quotation/v1/tokens/sol/{token_mint}/top_traders/swaps"
        try:
            resp = await self.http.get(url, headers=self._headers(), timeout=15)
            if resp.status_code != 200:
                logger.debug(f"GMGN top_traders {resp.status_code} for {token_mint[:12]}..")
                return []
            data = resp.json()
            items = data.get("data", {}).get("items", [])
            return [
                {
                    "address": item.get("address", ""),
                    "pnl_sol": float(item.get("realized_profit", 0) or 0),
                    "buy_amount_sol": float(item.get("total_cost", 0) or 0),
                    "sell_amount_sol": float(item.get("total_revenue", 0) or 0),
                }
                for item in items
                if item.get("address")
            ]
        except Exception as e:
            logger.warning(f"GMGN top_traders failed: {e}")
            return []

    async def wallet_stats(self, address: str) -> dict | None:
        """Get comprehensive stats for a single wallet from GMGN."""
        url = f"{self.base_url}/defi/quotation/v1/smartmoney/sol/walletNew/{address}"
        try:
            resp = await self.http.get(url, headers=self._headers(), timeout=15)
            if resp.status_code != 200:
                logger.debug(f"GMGN wallet_stats {resp.status_code} for {address[:8]}..")
                return None
            data = resp.json().get("data", {})
            if not data:
                return None
            return {
                "address": address,
                "total_pnl_sol": float(data.get("realized_profit", 0) or 0),
                "win_rate": float(data.get("winrate", 0) or 0),
                "total_trades": int(data.get("buy_count", 0) or 0) + int(data.get("sell_count", 0) or 0),
                "avg_hold_minutes": float(data.get("avg_hold_time", 0) or 0),
                "last_active": data.get("last_active_timestamp"),
            }
        except Exception as e:
            logger.warning(f"GMGN wallet_stats failed for {address[:8]}..: {e}")
            return None

    async def trending_tokens(self, timeframe: str = "1h", limit: int = 20) -> list[dict]:
        """Get currently trending tokens from GMGN."""
        url = f"{self.base_url}/defi/quotation/v1/rank/sol/swaps/{timeframe}"
        params = {
            "orderby": "swaps",
            "direction": "desc",
            "limit": str(limit),
            "min_liquidity": "50000",
        }
        try:
            resp = await self.http.get(url, headers=self._headers(), params=params, timeout=15)
            if resp.status_code != 200:
                return []
            data = resp.json()
            tokens = data.get("data", {}).get("rank", [])
            return [
                {
                    "address": t.get("address", ""),
                    "symbol": t.get("symbol", ""),
                    "price": float(t.get("price", 0) or 0),
                    "swaps": int(t.get("swaps", 0) or 0),
                    "volume": float(t.get("volume", 0) or 0),
                }
                for t in tokens
                if t.get("address")
            ]
        except Exception as e:
            logger.warning(f"GMGN trending failed: {e}")
            return []

    async def discover_from_recent_winners(self, min_gain_pct: float = 500, top_n_tokens: int = 10) -> list[dict]:
        """Find wallets that were early in recent winning tokens.

        Strategy: Get trending tokens, find their top traders, collect wallet addresses.
        """
        logger.info(f"Discovering wallets from top {top_n_tokens} trending tokens...")
        trending = await self.trending_tokens(timeframe="24h", limit=top_n_tokens)

        all_traders: dict[str, dict] = {}  # address -> best stats seen
        tokens_checked = 0

        for token in trending:
            traders = await self.top_traders_for_token(token["address"])
            for t in traders:
                addr = t["address"]
                if addr not in all_traders or t["pnl_sol"] > all_traders[addr].get("pnl_sol", 0):
                    all_traders[addr] = t
                    all_traders[addr]["found_in_tokens"] = all_traders.get(addr, {}).get("found_in_tokens", 0) + 1
            tokens_checked += 1
            logger.info(f"  Token {tokens_checked}/{len(trending)}: {token['symbol']} — {len(traders)} traders")
            await asyncio.sleep(2)  # Rate limit courtesy

        # Wallets appearing in multiple winning tokens are more interesting
        multi_token_wallets = {
            addr: info for addr, info in all_traders.items()
            if info.get("found_in_tokens", 1) >= 2
        }

        logger.info(
            f"Found {len(all_traders)} unique traders, "
            f"{len(multi_token_wallets)} in 2+ tokens"
        )
        return list(all_traders.values())
