"""Apify GMGN scrapers client — discover and rate memecoin wallets.

Two actors used:
1. gmgn-copytrade-wallet-scraper — discovers profitable wallets from GMGN's
   copytrade page. Supports filtering by trader type (smart_degen, pump_smart),
   time window (1d/7d/30d), and minimum thresholds. Real filter, real discovery.
2. gmgn-wallet-stat-scraper — fetches detailed stats for a specific wallet list.

Pricing (approximate):
  copytrade-scraper: $0.002/wallet + ~$0.03 fixed per run
  wallet-stat-scraper: $0.015/wallet + ~$0.02 fixed per run
"""

import asyncio
import logging
import json
from typing import Any

import httpx

logger = logging.getLogger("smc.curation.apify")


class ApifyGMGNClient:
    """Client for GMGN scraper actors on Apify.

    Runs actors synchronously (waits for completion) and returns dataset items.
    """

    BASE_URL = "https://api.apify.com/v2"
    COPYTRADE_ACTOR = "muhammetakkurtt~gmgn-copytrade-wallet-scraper"
    WALLET_STAT_ACTOR = "muhammetakkurtt~gmgn-wallet-stat-scraper"
    SMART_DEGEN_ACTOR = "muhammetakkurtt~gmgn-smart-degen-monitor-scraper"
    TOKEN_TRADERS_ACTOR = "muhammetakkurtt~gmgn-token-traders-scraper"

    def __init__(self, api_token: str, http: httpx.AsyncClient | None = None):
        self.api_token = api_token
        self.http = http or httpx.AsyncClient(timeout=600)  # long timeout — actors take minutes

    async def _run_actor_sync(
        self, actor_id: str, run_input: dict, max_wait_secs: int = 600
    ) -> list[dict]:
        """Run an actor synchronously and return dataset items.

        Uses run-sync-get-dataset-items endpoint which blocks until completion
        then returns the output dataset directly.
        """
        url = f"{self.BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"
        try:
            resp = await self.http.post(
                url,
                params={"token": self.api_token, "timeout": str(max_wait_secs)},
                json=run_input,
                timeout=max_wait_secs + 30,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                if isinstance(data, list):
                    return data
                logger.warning(f"Unexpected response shape: {type(data)}")
                return []
            logger.error(f"Apify actor {actor_id} failed: {resp.status_code} {resp.text[:300]}")
            return []
        except Exception as e:
            logger.error(f"Apify actor {actor_id} error: {e}")
            return []

    # ── Discovery via CopyTrade scraper ──

    async def discover_copytrade_wallets(
        self,
        trader_type: str = "smart_degen",
        sort_by: str = "profit_7days",
        min_pnl_7d: int | None = None,
        min_profit_7d_usd: int | None = None,
        min_winrate_7d: int | None = None,
        min_txs_7d: int | None = None,
        max_items: int = 100,
    ) -> list[dict]:
        """Discover top profitable wallets from GMGN copytrade page.

        Args:
            trader_type: smart_degen, pump_smart, launchpad_smart, renowned, top_followed
            sort_by: profit_7days, profit_30days, win_rate_7days, volume_7days, etc.
            min_pnl_7d: min 7-day PnL % (e.g., 20 for +20%)
            min_profit_7d_usd: min 7-day profit in USD
            min_winrate_7d: min 7-day win rate % (e.g., 50 for 50%)
            min_txs_7d: min 7-day transactions (filters dormant wallets)
            max_items: cap on results
        """
        run_input: dict[str, Any] = {
            "chain": "sol",
            "traderType": trader_type,
            "sortBy": sort_by,
            "sortDirection": "desc",
        }
        if min_pnl_7d is not None:
            run_input["min_pnl_7d"] = min_pnl_7d
        if min_profit_7d_usd is not None:
            run_input["min_profit_7d"] = min_profit_7d_usd
        if min_winrate_7d is not None:
            run_input["min_winrate_7d"] = min_winrate_7d
        if min_txs_7d is not None:
            run_input["min_txs_7d"] = min_txs_7d
        run_input["maxItems"] = max_items

        logger.info(f"Running Apify copytrade scraper: {run_input}")
        items = await self._run_actor_sync(self.COPYTRADE_ACTOR, run_input, max_wait_secs=900)
        logger.info(f"CopyTrade scraper returned {len(items)} wallets")
        return items

    # ── Detailed stats for a specific wallet list ──

    async def get_wallet_stats(
        self, wallet_addresses: list[str], time_period: str = "30d"
    ) -> list[dict]:
        """Fetch detailed GMGN stats for specific wallets.

        Args:
            wallet_addresses: list of Solana wallet addresses
            time_period: 1d, 7d, 30d, all
        """
        if not wallet_addresses:
            return []

        run_input = {
            "wallets": wallet_addresses,
            "timePeriod": time_period,
            "chain": "sol",
        }
        logger.info(f"Running Apify wallet-stat scraper on {len(wallet_addresses)} wallets ({time_period})")
        items = await self._run_actor_sync(
            self.WALLET_STAT_ACTOR, run_input, max_wait_secs=900
        )
        logger.info(f"Wallet-stat scraper returned {len(items)} results")
        return items
