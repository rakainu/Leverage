"""Automated wallet curation pipeline — discovers, scores, and manages the wallet list."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from config.settings import Settings
from curation.discovery import WalletDiscovery
from curation.scorer import WalletScorer
from db.database import get_db

logger = logging.getLogger("smc.curation.pipeline")


class CurationPipeline:
    """Scheduled pipeline that discovers new wallets and maintains the wallet list.

    Runs every N hours:
    1. Get trending/recent winning tokens
    2. Find top traders for each
    3. Get full stats and score each candidate
    4. Merge qualified wallets into wallets.json
    5. Deactivate stale auto-wallets below threshold
    6. Sync wallet list to tracked_wallets DB table
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=30)
        self.discovery = WalletDiscovery(settings, self.http)
        self.scorer = WalletScorer()

    async def run_loop(self):
        """Run curation on a schedule."""
        logger.info(f"Curation pipeline started (interval: {self.settings.curation_interval_hours}h)")
        # Run once at startup, then on interval
        await asyncio.sleep(30)  # Let other services start first
        while True:
            try:
                await self.run_once()
            except Exception as e:
                logger.error(f"Curation pipeline error: {e}")
            await asyncio.sleep(self.settings.curation_interval_hours * 3600)

    async def run_once(self):
        """Single curation cycle."""
        logger.info("Starting curation cycle...")

        # 1. Discover traders from trending tokens
        candidates = await self.discovery.discover_from_recent_winners(top_n_tokens=10)
        logger.info(f"Found {len(candidates)} candidate wallets")

        if not candidates:
            logger.warning("No candidates found — GMGN may be rate-limiting")
            return

        # 2. Get full stats and score each
        scored = []
        checked = 0
        for candidate in candidates[:50]:  # Cap to avoid excessive API calls
            addr = candidate["address"]
            stats = await self.discovery.wallet_stats(addr)
            if not stats:
                continue

            if not self.scorer.meets_minimum(stats, self.settings):
                continue

            score = self.scorer.score(stats)
            scored.append({
                "address": addr,
                "stats": stats,
                "score": score,
            })
            checked += 1
            if checked % 10 == 0:
                logger.info(f"  Scored {checked} wallets...")
            await asyncio.sleep(1)  # Rate limit

        # 3. Filter by minimum score
        qualified = [s for s in scored if s["score"] >= self.settings.min_wallet_score]
        logger.info(f"Qualified wallets: {len(qualified)} / {len(scored)} scored")

        # 4. Merge into wallets.json
        added, updated, deactivated = await self._merge_wallets(qualified)
        logger.info(
            f"Curation complete: +{added} added, ~{updated} updated, -{deactivated} deactivated"
        )

        # 5. Sync to DB
        await self._sync_to_db()

    async def _merge_wallets(self, new_wallets: list[dict]) -> tuple[int, int, int]:
        """Update wallets.json: add new auto wallets, deactivate stale ones.

        Returns (added, updated, deactivated) counts.
        """
        path = Path(self.settings.wallets_json_path)
        data = json.loads(path.read_text())
        existing = {w["address"]: w for w in data["wallets"]}

        added = 0
        updated = 0

        for nw in new_wallets:
            addr = nw["address"]
            if addr in existing:
                # Update score and stats only (never change source or label of manual entries)
                if existing[addr].get("source") != "manual":
                    existing[addr]["score"] = nw["score"]
                    existing[addr]["stats"] = nw["stats"]
                    existing[addr]["updated_at"] = datetime.now(timezone.utc).isoformat()
                    existing[addr]["active"] = True
                    updated += 1
            else:
                # Add new auto-discovered wallet
                existing[addr] = {
                    "address": addr,
                    "label": f"auto-{addr[:8]}",
                    "source": "auto",
                    "added_at": datetime.now(timezone.utc).isoformat(),
                    "score": nw["score"],
                    "stats": nw["stats"],
                    "active": True,
                }
                added += 1

        # Deactivate auto wallets below threshold
        deactivated = 0
        for addr, w in existing.items():
            if w.get("source") == "auto" and w.get("score", 0) < self.settings.min_wallet_score:
                if w.get("active", True):
                    w["active"] = False
                    deactivated += 1

        data["wallets"] = list(existing.values())
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["version"] = data.get("version", 0) + 1
        path.write_text(json.dumps(data, indent=2))

        return added, updated, deactivated

    async def _sync_to_db(self):
        """Sync wallets.json to the tracked_wallets DB table for dashboard queries."""
        path = Path(self.settings.wallets_json_path)
        data = json.loads(path.read_text())
        db = await get_db()

        for w in data.get("wallets", []):
            stats = w.get("stats", {})
            await db.execute(
                """INSERT OR REPLACE INTO tracked_wallets
                   (address, label, source, score, total_trades, win_rate,
                    total_pnl_sol, avg_hold_minutes, active, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    w["address"],
                    w.get("label"),
                    w.get("source", "manual"),
                    w.get("score", 0),
                    stats.get("total_trades", 0),
                    stats.get("win_rate", 0),
                    stats.get("total_pnl_sol", 0),
                    stats.get("avg_hold_minutes", 0),
                    1 if w.get("active", True) else 0,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        await db.commit()
        logger.info(f"Synced {len(data.get('wallets', []))} wallets to DB")
