"""GMGN wallet discovery via Apify — the good version.

Pulls top profitable wallets from multiple GMGN trader categories, ranks them
with time-windowed data, and filters aggressively. Aims for quality over quantity.

Usage:
    python -m scripts.gmgn_discover            # dry run — shows what it would add
    python -m scripts.gmgn_discover --apply     # writes to wallets.json + DB
"""

import asyncio
import json
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import Settings
from curation.apify_gmgn import ApifyGMGNClient
from curation.gmgn_ranker import GMGNRanker
from curation.dedupe import dedupe_wallets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gmgn_discover")

# ── Filters ──
MIN_SCORE = 65.0               # composite score threshold
MIN_PROFIT_7D_USD = 500        # at least $500 profit in last 7 days
MIN_WINRATE_7D_PCT = 45        # 45%+ win rate over 7d
MIN_TXS_7D = 10                # at least 10 trades last 7 days (active)
MAX_TXS_7D = 1000              # bot filter
MAX_WALLETS_TO_ADD = 100       # cap total additions per run

# ── Discovery sources ──
# Pull from multiple GMGN trader categories, each sorted different ways,
# to get diverse coverage of top performers.
DISCOVERY_QUERIES = [
    {"trader_type": "smart_degen", "sort_by": "profit_7days", "label": "SmartDegen-Profit7d"},
    {"trader_type": "smart_degen", "sort_by": "win_rate_7days", "label": "SmartDegen-WinRate7d"},
    {"trader_type": "pump_smart", "sort_by": "profit_7days", "label": "PumpSmart-Profit7d"},
    {"trader_type": "pump_smart", "sort_by": "win_rate_7days", "label": "PumpSmart-WinRate7d"},
    {"trader_type": "renowned", "sort_by": "profit_7days", "label": "Renowned-Profit7d"},
]


async def discover(apply: bool = False):
    settings = Settings()
    if not settings.apify_api_token:
        logger.error("No Apify API token — set SMC_APIFY_API_TOKEN in .env")
        return

    http = httpx.AsyncClient(timeout=900)
    apify = ApifyGMGNClient(settings.apify_api_token, http)
    ranker = GMGNRanker()

    # ── Step 1: Pull from each discovery query ──
    logger.info(f"Step 1: Pulling from {len(DISCOVERY_QUERIES)} GMGN discovery queries...")
    all_candidates: dict[str, dict] = {}  # address -> raw GMGN data

    for q in DISCOVERY_QUERIES:
        logger.info(f"  Running: {q['label']}")
        items = await apify.discover_copytrade_wallets(
            trader_type=q["trader_type"],
            sort_by=q["sort_by"],
            min_winrate_7d=MIN_WINRATE_7D_PCT,
            min_profit_7d_usd=MIN_PROFIT_7D_USD,
            min_txs_7d=MIN_TXS_7D,
            max_items=100,
        )
        new = 0
        for item in items:
            addr = item.get("wallet_address") or item.get("address")
            if addr and addr not in all_candidates:
                item["_source_query"] = q["label"]
                all_candidates[addr] = item
                new += 1
        logger.info(f"    → {len(items)} returned, {new} new (total unique: {len(all_candidates)})")

    logger.info(f"\nTotal unique candidates: {len(all_candidates)}")

    if not all_candidates:
        logger.error("No candidates returned — check Apify token and actor availability")
        return

    # ── Step 2: Rank and filter ──
    logger.info("\nStep 2: Ranking with GMGN scorer...")
    ranked = []
    rejected = {
        "score": 0, "dormant": 0, "low_activity": 0, "losing_7d": 0,
        "losing_30d": 0, "bot": 0, "low_winrate": 0,
    }

    for addr, data in all_candidates.items():
        result = ranker.score(data)
        composite = result["composite"]
        flags = result["flags"]

        # Bucket rejections
        if not ranker.meets_minimum(data, min_composite=MIN_SCORE):
            if composite < MIN_SCORE:
                rejected["score"] += 1
            if any("dormant" in f for f in flags):
                rejected["dormant"] += 1
            if "low_activity" in flags:
                rejected["low_activity"] += 1
            if "losing_7d" in flags:
                rejected["losing_7d"] += 1
            if "losing_30d" in flags:
                rejected["losing_30d"] += 1
            if any("bot_like" in f for f in flags):
                rejected["bot"] += 1
            if any("low_winrate" in f for f in flags):
                rejected["low_winrate"] += 1
            continue

        ranked.append({
            "address": addr,
            "score": composite,
            "breakdown": result["breakdown"],
            "winrate_7d": float(data.get("winrate_7d", 0)),
            "winrate_30d": float(data.get("winrate_30d", 0)),
            "profit_7d_usd": float(data.get("realized_profit_7d", 0)),
            "profit_30d_usd": float(data.get("realized_profit_30d", 0)),
            "txs_7d": int(data.get("txs_7d", 0) or 0),
            "last_active": data.get("last_active", 0),
            "tags": data.get("tags", []),
            "source_query": data.get("_source_query", ""),
            "raw": data,
        })

    ranked.sort(key=lambda w: w["score"], reverse=True)
    ranked = ranked[:MAX_WALLETS_TO_ADD]

    # ── Step 3: Report ──
    logger.info(f"\n{'='*100}")
    logger.info("RESULTS")
    logger.info(f"{'='*100}")
    logger.info(f"Total unique candidates:     {len(all_candidates)}")
    logger.info(f"QUALIFIED (score ≥ {MIN_SCORE}):    {len(ranked)}")
    logger.info(f"")
    logger.info("Rejected breakdown (multi-cause possible):")
    for k, v in rejected.items():
        logger.info(f"  {k:<18} {v}")

    if ranked:
        logger.info(f"\nTop {min(25, len(ranked))} qualified wallets:")
        logger.info(
            f"{'Address':<46} {'Score':>6} {'WR7d':>6} {'WR30d':>6} "
            f"{'Profit7d':>12} {'Profit30d':>12} {'Tx7d':>6} {'Source':<20}"
        )
        logger.info("-" * 120)
        for w in ranked[:25]:
            logger.info(
                f"{w['address']:<46} {w['score']:>6.1f} "
                f"{w['winrate_7d']*100:>5.1f}% {w['winrate_30d']*100:>5.1f}% "
                f"${w['profit_7d_usd']:>10,.0f} ${w['profit_30d_usd']:>10,.0f} "
                f"{w['txs_7d']:>6} {w['source_query']:<20}"
            )
        if len(ranked) > 25:
            logger.info(f"  ... and {len(ranked) - 25} more")

    if not ranked:
        logger.warning("No wallets passed filters.")
        return

    # ── Step 4: Apply ──
    if not apply:
        logger.info(f"\nDRY RUN — {len(ranked)} wallets would be added/updated.")
        logger.info("Run with --apply to write to wallets.json and sync to DB.")
        return

    logger.info(f"\nApplying: merging {len(ranked)} wallets into wallets.json...")
    wallets_path = Path(settings.wallets_json_path)
    data = json.loads(wallets_path.read_text())
    existing = {w["address"]: w for w in data["wallets"]}

    now = datetime.now(timezone.utc).isoformat()
    added = 0
    updated = 0

    for w in ranked:
        pnl_sol_est = w["profit_30d_usd"] / 130.0
        stats_block = {
            "total_trades": w["txs_7d"],
            "win_rate": w["winrate_7d"],
            "total_pnl_sol": pnl_sol_est,
            "avg_hold_minutes": 0,
            "profit_7d_usd": w["profit_7d_usd"],
            "profit_30d_usd": w["profit_30d_usd"],
            "winrate_7d": w["winrate_7d"],
            "winrate_30d": w["winrate_30d"],
        }
        label = f"gmgn-{w['score']:.0f}-wr{w['winrate_7d']*100:.0f}-${w['profit_7d_usd']/1000:.0f}k7d"

        if w["address"] in existing:
            # Don't overwrite manual entries
            if existing[w["address"]].get("source") != "manual":
                existing[w["address"]]["score"] = w["score"]
                existing[w["address"]]["stats"] = stats_block
                existing[w["address"]]["updated_at"] = now
                existing[w["address"]]["active"] = True
                existing[w["address"]]["label"] = label
                updated += 1
        else:
            existing[w["address"]] = {
                "address": w["address"],
                "label": label,
                "source": "gmgn-apify",
                "added_at": now,
                "score": w["score"],
                "stats": stats_block,
                "active": True,
            }
            added += 1

    # Dedupe defensively — should never trigger but guards against race conditions
    merged_list = list(existing.values())
    deduped, removed = dedupe_wallets(merged_list)
    if removed:
        logger.warning(f"Dedupe removed {removed} duplicate entries")
    data["wallets"] = deduped
    data["updated_at"] = now
    data["version"] = data.get("version", 0) + 1
    wallets_path.write_text(json.dumps(data, indent=2))

    logger.info(f"+{added} added, ~{updated} updated")
    logger.info(f"Total wallets in file: {len(data['wallets'])}")

    # Sync to DB
    try:
        from db.database import get_db
        db = await get_db()
        for w in data["wallets"]:
            stats = w.get("stats", {})
            await db.execute(
                """INSERT OR REPLACE INTO tracked_wallets
                   (address, label, source, score, total_trades, win_rate,
                    total_pnl_sol, avg_hold_minutes, active, added_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    w["address"], w.get("label"), w.get("source", "gmgn-apify"),
                    w.get("score", 0), stats.get("total_trades", 0),
                    stats.get("win_rate", 0), stats.get("total_pnl_sol", 0),
                    stats.get("avg_hold_minutes", 0),
                    1 if w.get("active", True) else 0,
                    w.get("added_at", now), now,
                ),
            )
        await db.commit()
        logger.info("Synced all wallets to DB")
    except Exception as e:
        logger.error(f"DB sync failed (wallets.json updated): {e}")


if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv
    asyncio.run(discover(apply=apply_flag))
