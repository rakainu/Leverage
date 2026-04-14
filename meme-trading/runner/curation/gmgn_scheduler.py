"""GMGN discovery scheduler — periodic loop that scrapes Apify, inserts raw
candidates into gmgn_candidates, and drives them through the WalletVetter.

Runs the same queries the old `scripts/gmgn_discover.py` CLI used, but as a
long-running task wired into the runner process. Discovered candidates never
enter the active pool directly — they must pass every stage in wallet_vetting.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from runner.config.weights_loader import WeightsLoader
from runner.curation.wallet_vetting import WalletVetter
from runner.db.database import Database
from runner.utils.logging import get_logger

logger = get_logger("runner.curation.gmgn_scheduler")


DEFAULT_QUERIES = [
    {"trader_type": "smart_degen", "sort_by": "profit_7days", "label": "SmartDegen-Profit7d"},
    {"trader_type": "smart_degen", "sort_by": "win_rate_7days", "label": "SmartDegen-WR7d"},
    {"trader_type": "pump_smart", "sort_by": "profit_7days", "label": "PumpSmart-Profit7d"},
    {"trader_type": "pump_smart", "sort_by": "win_rate_7days", "label": "PumpSmart-WR7d"},
    {"trader_type": "renowned", "sort_by": "profit_7days", "label": "Renowned-Profit7d"},
]


@dataclass
class DiscoveryRun:
    scraped: int = 0
    new_raw: int = 0
    vetted: int = 0
    rejected: int = 0
    shadowed: int = 0


class GMGNScheduler:
    """Periodic async loop driving Apify discovery + vetting."""

    def __init__(
        self,
        db: Database,
        weights: WeightsLoader,
        vetter: WalletVetter,
        apify_client: Any,  # ApifyGMGNClient or any obj with discover_copytrade_wallets
        ranker: Any | None = None,  # GMGNRanker (optional; adds composite_score)
    ):
        self.db = db
        self.weights = weights
        self.vetter = vetter
        self.apify = apify_client
        self.ranker = ranker

    # ── public ─────────────────────────────────────────────────────

    async def run(self) -> None:
        enabled = bool(self.weights.get("gmgn_discovery.enabled", False))
        if not enabled:
            logger.info("gmgn_scheduler_disabled")
            return
        if self.apify is None:
            logger.warning("gmgn_scheduler_no_apify_client")
            return
        interval = int(self.weights.get("gmgn_discovery.interval_hours", 24)) * 3600
        logger.info("gmgn_scheduler_start", interval_hours=interval / 3600)
        # Run once immediately so Rich sees effect on next deploy.
        await self._safe_cycle()
        while True:
            await asyncio.sleep(interval)
            await self._safe_cycle()

    async def discover_once(self) -> DiscoveryRun:
        """One full discovery+vet cycle. Exposed for CLI + tests."""
        stats = DiscoveryRun()
        queries = self.weights.get("gmgn_discovery.queries_detail", None) or DEFAULT_QUERIES
        cap_new = int(self.weights.get("gmgn_discovery.cap_new_per_run", 20))

        # Stage 1: scrape
        all_candidates: dict[str, dict] = {}
        for q in queries:
            try:
                items = await self.apify.discover_copytrade_wallets(
                    trader_type=q.get("trader_type", "smart_degen"),
                    sort_by=q.get("sort_by", "profit_7days"),
                    min_profit_7d_usd=int(self.weights.get(
                        "gmgn_discovery.gmgn_filters.min_7d_pnl_usd", 3000)),
                    min_winrate_7d=int(100 * float(self.weights.get(
                        "gmgn_discovery.gmgn_filters.min_7d_winrate", 0.55))),
                    min_txs_7d=10,
                    max_items=100,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("apify_query_failed", label=q.get("label"), error=str(e))
                continue
            for item in items:
                addr = item.get("wallet_address") or item.get("address")
                if not addr or addr in all_candidates:
                    continue
                item["_source_query"] = q.get("label", "")
                all_candidates[addr] = item
        stats.scraped = len(all_candidates)
        logger.info("gmgn_scrape_done", unique=stats.scraped)

        # Stage 1b: score + persist raw rows
        inserted = await self._persist_raw(all_candidates, cap_new)
        stats.new_raw = inserted

        # Stage 2-4: vet every raw candidate
        raw_wallets = await self._list_stage("raw")
        for wallet in raw_wallets:
            try:
                final = await self.vetter.vet_candidate(wallet)
            except Exception as e:  # noqa: BLE001
                logger.warning("vet_candidate_failed", wallet=wallet, error=str(e))
                continue
            stats.vetted += 1
            if final == "rejected":
                stats.rejected += 1
            elif final == "shadow":
                stats.shadowed += 1

        logger.info(
            "gmgn_discovery_cycle_done",
            scraped=stats.scraped, new_raw=stats.new_raw,
            vetted=stats.vetted, rejected=stats.rejected,
            shadowed=stats.shadowed,
        )
        return stats

    # ── internal ───────────────────────────────────────────────────

    async def _safe_cycle(self) -> None:
        try:
            await self.discover_once()
        except Exception as e:  # noqa: BLE001
            logger.error("gmgn_scheduler_cycle_failed", error=str(e))

    async def _persist_raw(self, candidates: dict[str, dict], cap: int) -> int:
        """Insert new candidates as stage='raw'. Ignore wallets already present
        in gmgn_candidates OR already active in wallet_tiers."""
        assert self.db.conn is not None
        if not candidates:
            return 0

        # Load existing so we skip known ones
        async with self.db.conn.execute(
            "SELECT wallet_address FROM gmgn_candidates"
        ) as cur:
            known_candidates = {row[0] async for row in cur}
        async with self.db.conn.execute(
            "SELECT wallet_address FROM wallet_tiers WHERE tier IN ('A','B','S')"
        ) as cur:
            active_or_shadow = {row[0] async for row in cur}

        inserted = 0
        for addr, raw in candidates.items():
            if inserted >= cap:
                break
            if addr in known_candidates or addr in active_or_shadow:
                continue
            composite = None
            if self.ranker is not None:
                try:
                    composite = float(self.ranker.score(raw).get("composite", 0.0))
                except Exception:  # noqa: BLE001
                    composite = None
            await self.db.conn.execute(
                """INSERT INTO gmgn_candidates
                   (wallet_address, raw_json, composite_score, source_query, stage)
                   VALUES (?, ?, ?, ?, 'raw')""",
                (addr, json.dumps(raw, default=str), composite, raw.get("_source_query")),
            )
            inserted += 1
        await self.db.conn.commit()
        return inserted

    async def _list_stage(self, stage: str) -> list[str]:
        assert self.db.conn is not None
        async with self.db.conn.execute(
            "SELECT wallet_address FROM gmgn_candidates WHERE stage = ?",
            (stage,),
        ) as cur:
            return [row[0] async for row in cur]
