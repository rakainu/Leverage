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
from curation.dedupe import dedupe_wallets
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
        """Single curation cycle — Nansen Smart Money discovery."""
        logger.info("Starting curation cycle...")

        # Discover active SM wallets from Nansen
        nansen_wallets = await self._discover_nansen()

        if not nansen_wallets:
            logger.warning("No new wallets from Nansen this cycle")
            return

        # Merge into wallets.json
        added, updated, deactivated = await self._merge_wallets(nansen_wallets)
        logger.info(
            f"Curation complete: +{added} added, ~{updated} updated, -{deactivated} deactivated"
        )

        # Sync to DB
        await self._sync_to_db()

    async def _discover_nansen(self) -> list[dict]:
        """Pull active Smart Money wallets from Nansen DEX Trades API."""
        if not self.settings.nansen_api_key:
            logger.warning("No Nansen API key — skipping SM discovery")
            return []

        try:
            resp = await self.http.post(
                "https://api.nansen.ai/api/v1/smart-money/dex-trades",
                headers={
                    "apikey": self.settings.nansen_api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "chains": ["solana"],
                    "pagination": {"page": 1, "per_page": 1000},
                },
                timeout=30,
            )
            if resp.status_code != 200:
                logger.error(f"Nansen API returned {resp.status_code}")
                return []

            data = resp.json()
            trades = data.get("data", [])
            logger.info(f"Nansen returned {len(trades)} SM trades")

            # Aggregate per wallet — only count memecoin buys
            stats = {}
            for t in trades:
                addr = t["trader_address"]
                bought_sym = t.get("token_bought_symbol", "")
                bought_age = t.get("token_bought_age_days", 999)
                trade_val = t.get("trade_value_usd", 0)

                if addr not in stats:
                    stats[addr] = {"buys": 0, "trades": 0, "usd": 0, "tokens": set()}
                stats[addr]["trades"] += 1
                stats[addr]["usd"] += trade_val
                if bought_sym not in ["USDC", "USDT", "SOL", "WSOL", ""] and bought_age <= 30:
                    stats[addr]["buys"] += 1
                    stats[addr]["tokens"].add(bought_sym[:20])

            # Only keep wallets actively buying memecoins
            qualified = []
            for addr, s in stats.items():
                if s["buys"] < 1:
                    continue
                tokens_str = ", ".join(list(s["tokens"])[:3])
                qualified.append({
                    "address": addr,
                    "score": min(95, 70 + s["buys"]),
                    "stats": {
                        "total_trades": s["trades"],
                        "win_rate": 0,
                        "total_pnl_sol": 0,
                        "avg_hold_minutes": 0,
                    },
                    "label_hint": f"nansen-sm-{s['buys']}buys-{s['usd']:.0f}usd",
                    "tokens": tokens_str,
                })

            logger.info(f"Nansen: {len(qualified)} wallets buying memecoins out of {len(stats)} total SM")
            return qualified

        except Exception as e:
            logger.error(f"Nansen discovery failed: {e}")
            return []

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
                # Add new discovered wallet
                existing[addr] = {
                    "address": addr,
                    "label": nw.get("label_hint", f"auto-{addr[:8]}"),
                    "source": "nansen-live",
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

        # Dedupe before write — defensive guard against race conditions
        merged_list = list(existing.values())
        deduped, removed = dedupe_wallets(merged_list)
        if removed:
            logger.warning(f"Dedupe removed {removed} duplicate entries during merge")

        data["wallets"] = deduped
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
                    total_pnl_sol, avg_hold_minutes, active, added_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    w.get("added_at", datetime.now(timezone.utc).isoformat()),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        await db.commit()
        logger.info(f"Synced {len(data.get('wallets', []))} wallets to DB")
