"""Automated wallet curation pipeline — discovers, scores, and manages the wallet list."""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from config.settings import Settings
from curation.discovery import WalletDiscovery
from curation.scorer import WalletScorer
from curation.dedupe import dedupe_wallets
from curation.apify_gmgn import ApifyGMGNClient
from curation.gmgn_ranker import GMGNRanker
from db.database import get_db

logger = logging.getLogger("smc.curation.pipeline")


class CurationPipeline:
    """Scheduled pipeline that maintains the active wallet pool.

    Runs every `curation_interval_hours` hours:
    1. Prune dead-weight: deactivate non-manual wallets with 0 buy_events in the last
       `wallet_prune_dead_days` days.
    2. Discover new wallets via GMGN-Apify (smart_degen + pump_smart trader-type buckets),
       scored by `GMGNRanker`, capped at `gmgn_max_new_per_cycle`.
    3. Optionally pull from Nansen Smart Money if a key is configured (secondary source).
    4. Merge new candidates into wallets.json — manual wallets are immutable; existing
       auto/source wallets get their score+stats refreshed.
    5. Sync wallets.json to the `tracked_wallets` DB table for dashboard queries.
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
        """Single curation cycle: prune dead → discover GMGN → discover Nansen (opt) → merge → sync."""
        logger.info("Starting curation cycle...")

        pruned = await self._prune_dead_wallets(days=self.settings.wallet_prune_dead_days)

        gmgn_wallets = await self._discover_gmgn_apify()

        nansen_wallets: list[dict] = []
        if self.settings.nansen_api_key:
            nansen_wallets = await self._discover_nansen()

        new_wallets = gmgn_wallets + nansen_wallets

        added = updated = below_threshold = 0
        if new_wallets:
            added, updated, below_threshold = await self._merge_wallets(new_wallets)

        await self._sync_to_db()

        logger.info(
            "Curation cycle done: "
            f"gmgn={len(gmgn_wallets)} nansen={len(nansen_wallets)} "
            f"+{added} added, ~{updated} updated, "
            f"-{pruned} pruned-dead, -{below_threshold} below-threshold"
        )

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
                    "source": "nansen-live",
                })

            logger.info(f"Nansen: {len(qualified)} wallets buying memecoins out of {len(stats)} total SM")
            return qualified

        except Exception as e:
            logger.error(f"Nansen discovery failed: {e}")
            return []

    async def _discover_gmgn_apify(self) -> list[dict]:
        """Pull profitable wallets from GMGN via Apify (smart_degen + pump_smart buckets).

        Filters candidates through GMGNRanker.meets_minimum, sorts by composite score,
        caps additions at gmgn_max_new_per_cycle to keep the pool stable.
        """
        if not self.settings.apify_api_token:
            logger.warning("No Apify API token — skipping GMGN-Apify discovery")
            return []

        apify = ApifyGMGNClient(self.settings.apify_api_token, self.http)
        ranker = GMGNRanker()

        # Pull two trader-type buckets in parallel
        buckets = ("smart_degen", "pump_smart")
        results = await asyncio.gather(
            *[
                apify.discover_copytrade_wallets(
                    trader_type=tt,
                    sort_by="profit_7days",
                    min_winrate_7d=self.settings.gmgn_min_winrate_pct,
                    min_txs_7d=self.settings.gmgn_min_txs_7d,
                    max_items=self.settings.gmgn_max_per_actor,
                )
                for tt in buckets
            ],
            return_exceptions=True,
        )

        candidates: list[dict] = []
        for tt, items in zip(buckets, results):
            if isinstance(items, Exception):
                logger.error(f"Apify {tt} bucket failed: {items}")
                continue
            candidates.extend(items)
            logger.info(f"GMGN-Apify {tt}: {len(items)} raw")

        # Dedupe by address (same wallet may appear in both buckets)
        seen: dict[str, dict] = {}
        for c in candidates:
            addr = c.get("wallet_address") or c.get("address")
            if addr and addr not in seen:
                seen[addr] = c

        # Score, filter, sort. Call score() once per candidate (avoid the
        # double-compute in meets_minimum which scores then re-checks).
        now_ts = time.time()
        scored: list[tuple[float, dict, dict]] = []
        for c in seen.values():
            # Hard floors (mirrors GMGNRanker.meets_minimum, minus its score() call)
            txs_7d = int(c.get("txs_7d", 0) or 0)
            if txs_7d < 5 or txs_7d > 1000:
                continue
            last_active = c.get("last_active") or 0
            if not last_active or now_ts - float(last_active) > 7 * 86400:
                continue
            if float(c.get("realized_profit_7d", 0) or 0) <= 0:
                continue
            if float(c.get("realized_profit_30d", 0) or 0) <= 0:
                continue
            if float(c.get("winrate_7d", 0) or 0) < 0.45:
                continue
            # Now score once and gate on composite
            result = ranker.score(c)
            if result["composite"] < self.settings.gmgn_min_score:
                continue
            scored.append((result["composite"], c, result))

        scored.sort(key=lambda x: x[0], reverse=True)
        scored = scored[: self.settings.gmgn_max_new_per_cycle]

        qualified = []
        for composite, c, result in scored:
            addr = c.get("wallet_address") or c.get("address")
            wr_pct = int(float(c.get("winrate_7d", 0) or 0) * 100)
            profit_k = int(float(c.get("realized_profit_7d", 0) or 0) / 1000)
            qualified.append({
                "address": addr,
                "score": composite,
                "stats": {
                    "total_trades": int(c.get("txs_7d", 0) or 0),
                    "win_rate": float(c.get("winrate_7d", 0) or 0) * 100,
                    "total_pnl_sol": 0.0,  # GMGN reports USD, not SOL
                    "total_pnl_usd_7d": float(c.get("realized_profit_7d", 0) or 0),
                    "avg_hold_minutes": 0,
                },
                "label_hint": f"gmgn-{int(composite)}-wr{wr_pct}-${profit_k}k7d",
                "source": "gmgn-apify",
            })

        logger.info(
            f"GMGN-Apify discovery: {len(seen)} unique candidates → "
            f"{len(qualified)} qualified (score≥{self.settings.gmgn_min_score})"
        )
        return qualified

    async def _merge_wallets(self, new_wallets: list[dict]) -> tuple[int, int, int]:
        """Update wallets.json: add new auto wallets, deactivate stale ones.

        Returns (added, updated, below_threshold) counts. below_threshold = auto wallets newly
        deactivated due to score < min_wallet_score (separate from prune-by-inactivity, which
        lives in _prune_dead_wallets).
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
                # Add new discovered wallet — provenance from candidate
                existing[addr] = {
                    "address": addr,
                    "label": nw.get("label_hint", f"auto-{addr[:8]}"),
                    "source": nw.get("source", "auto"),
                    "added_at": datetime.now(timezone.utc).isoformat(),
                    "score": nw["score"],
                    "stats": nw["stats"],
                    "active": True,
                }
                added += 1

        # Deactivate auto wallets below threshold
        below_threshold = 0
        for addr, w in existing.items():
            if w.get("source") == "auto" and w.get("score", 0) < self.settings.min_wallet_score:
                if w.get("active", True):
                    w["active"] = False
                    below_threshold += 1

        # Dedupe before write — defensive guard against race conditions
        merged_list = list(existing.values())
        deduped, removed = dedupe_wallets(merged_list)
        if removed:
            logger.warning(f"Dedupe removed {removed} duplicate entries during merge")

        data["wallets"] = deduped
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["version"] = data.get("version", 0) + 1
        path.write_text(json.dumps(data, indent=2))

        return added, updated, below_threshold

    async def _prune_dead_wallets(self, days: int) -> int:
        """Deactivate auto-source wallets with 0 buy_events in the last `days` days.

        Manual wallets are never touched. Returns count of wallets newly deactivated.
        Updates both DB and wallets.json.
        """
        db = await get_db()
        rows = await db.execute_fetchall(
            f"""SELECT w.address FROM tracked_wallets w
                LEFT JOIN buy_events b
                  ON b.wallet_address = w.address
                  AND b.timestamp > datetime('now', '-{int(days)} days')
                WHERE w.active = 1 AND (w.source IS NULL OR w.source != 'manual')
                GROUP BY w.address
                HAVING COUNT(b.id) = 0"""
        )
        dead = [r["address"] for r in (rows or [])]
        if not dead:
            return 0

        # Deactivate in DB
        placeholders = ",".join("?" for _ in dead)
        await db.execute(
            f"UPDATE tracked_wallets SET active = 0 WHERE address IN ({placeholders})",
            dead,
        )
        await db.commit()

        # Mirror change in wallets.json (preserve order, skip manual defensively)
        path = Path(self.settings.wallets_json_path)
        data = json.loads(path.read_text())
        dead_set = set(dead)
        for w in data.get("wallets", []):
            if w.get("address") in dead_set and w.get("source") != "manual":
                w["active"] = False
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["version"] = data.get("version", 0) + 1
        path.write_text(json.dumps(data, indent=2))

        return len(dead)

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
