"""Filter a list of wallet addresses against existing tracked wallets,
then run survivors through the GMGN wallet-stat-scraper for scoring.

Usage:
    python -m scripts.filter_and_rank /path/to/input.json
    python -m scripts.filter_and_rank /path/to/input.json --apply

Input file: a JSON array of {address, name, emoji} objects (GMGN import format).
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
logger = logging.getLogger("filter_rank")

MIN_SCORE = 65.0


def normalize_stat_scraper_data(item: dict) -> dict:
    """Normalize wallet-stat-scraper output to look like copytrade-scraper output
    so the GMGN ranker can score it consistently.

    Note: stat scraper returns the period requested (we use 30d). It does NOT
    return separate 7d AND 30d winrates — only the queried period's winrate
    is in pnl_detail.winrate. We use it for both 7d and 30d in the ranker.
    """
    addr = item.get("wallet_address") or item.get("address") or ""

    pnl_detail = item.get("pnl_detail") or {}
    period_winrate = float(pnl_detail.get("winrate", 0) or 0)

    # Transaction counts: stat scraper has buy_7d + sell_7d
    buy_7d = int(item.get("buy_7d", 0) or 0)
    sell_7d = int(item.get("sell_7d", 0) or 0)
    txs_7d = buy_7d + sell_7d

    buy_30d = int(item.get("buy_30d", 0) or 0)
    sell_30d = int(item.get("sell_30d", 0) or 0)
    txs_30d = buy_30d + sell_30d

    return {
        "address": addr,
        "wallet_address": addr,
        # Stat scraper uses last_active_timestamp; copytrade uses last_active
        "last_active": item.get("last_active_timestamp") or item.get("last_active", 0),
        "winrate_7d": period_winrate,
        "winrate_30d": period_winrate,
        "realized_profit_7d": float(item.get("realized_profit_7d", 0) or 0),
        "realized_profit_30d": float(item.get("realized_profit_30d", 0) or 0),
        "txs_7d": txs_7d,
        "txs_30d": txs_30d,
        "buy_7d": buy_7d,
        "sell_7d": sell_7d,
        "pnl_2x_5x_num_7d": int(pnl_detail.get("pnl_2x_5x_num", 0) or 0),
        "pnl_gt_5x_num_7d": int(pnl_detail.get("pnl_gt_5x_num", 0) or 0),
        "tags": item.get("tags", []),
    }


async def main(input_path: Path, apply: bool = False):
    settings = Settings()
    if not settings.apify_api_token:
        logger.error("No Apify API token — set SMC_APIFY_API_TOKEN")
        return

    # Load input wallet addresses
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return

    input_data = json.loads(input_path.read_text(encoding="utf-8"))
    input_addresses = [w["address"] for w in input_data if w.get("address")]
    logger.info(f"Loaded {len(input_addresses)} wallets from input")

    # Dedupe input first (handles dupes within the input file itself)
    seen = set()
    input_unique = []
    for addr in input_addresses:
        if addr not in seen:
            seen.add(addr)
            input_unique.append(addr)
    if len(input_unique) < len(input_addresses):
        logger.info(f"Removed {len(input_addresses) - len(input_unique)} duplicates within input")

    # Load existing wallets
    wallets_path = Path(settings.wallets_json_path)
    existing_data = json.loads(wallets_path.read_text())
    existing_addrs = {w["address"] for w in existing_data["wallets"]}
    logger.info(f"Existing tracked wallets: {len(existing_addrs)}")

    # Filter out wallets we already have
    new_addrs = [a for a in input_unique if a not in existing_addrs]
    overlap = len(input_unique) - len(new_addrs)
    logger.info(f"Already tracked: {overlap}")
    logger.info(f"NEW wallets to score: {len(new_addrs)}")

    if not new_addrs:
        logger.info("Nothing to score — all input wallets already tracked.")
        return

    # Score via Apify wallet-stat-scraper (batch call, 30d period)
    http = httpx.AsyncClient(timeout=900)
    apify = ApifyGMGNClient(settings.apify_api_token, http)
    ranker = GMGNRanker()

    logger.info(f"Calling Apify wallet-stat-scraper for {len(new_addrs)} wallets...")
    run_input = {
        "walletAddresses": new_addrs,
        "chain": "sol",
        "period": "30d",
    }
    items = await apify._run_actor_sync(
        apify.WALLET_STAT_ACTOR, run_input, max_wait_secs=900
    )
    logger.info(f"Stat scraper returned {len(items)} items")

    if not items:
        logger.error("Stat scraper returned nothing — actor may be down or input invalid")
        return

    # Score each
    qualified = []
    rejected = {"score": 0, "dormant": 0, "losing_7d": 0, "losing_30d": 0, "bot": 0, "no_data": 0}

    for item in items:
        normalized = normalize_stat_scraper_data(item)
        if not normalized.get("address"):
            rejected["no_data"] += 1
            continue

        result = ranker.score(normalized)
        composite = result["composite"]
        flags = result["flags"]

        if not ranker.meets_minimum(normalized, min_composite=MIN_SCORE):
            if composite < MIN_SCORE:
                rejected["score"] += 1
            if any("dormant" in f for f in flags):
                rejected["dormant"] += 1
            if "losing_7d" in flags:
                rejected["losing_7d"] += 1
            if "losing_30d" in flags:
                rejected["losing_30d"] += 1
            if any("bot_like" in f for f in flags):
                rejected["bot"] += 1
            continue

        qualified.append({
            "address": normalized["address"],
            "score": composite,
            "winrate_7d": float(normalized.get("winrate_7d", 0)),
            "winrate_30d": float(normalized.get("winrate_30d", 0)),
            "profit_7d_usd": float(normalized.get("realized_profit_7d", 0)),
            "profit_30d_usd": float(normalized.get("realized_profit_30d", 0)),
            "txs_7d": int(normalized.get("txs_7d", 0) or 0),
            "raw": normalized,
        })

    qualified.sort(key=lambda w: w["score"], reverse=True)

    # Report
    logger.info(f"\n{'='*100}")
    logger.info("RESULTS")
    logger.info(f"{'='*100}")
    logger.info(f"Input wallets:               {len(input_addresses)}")
    logger.info(f"After dedup within input:    {len(input_unique)}")
    logger.info(f"Already in our system:       {overlap}")
    logger.info(f"New (sent to scoring):       {len(new_addrs)}")
    logger.info(f"Returned by stat scraper:    {len(items)}")
    logger.info(f"QUALIFIED (score ≥ {MIN_SCORE}):    {len(qualified)}")
    logger.info("")
    logger.info("Rejected breakdown:")
    for k, v in rejected.items():
        logger.info(f"  {k:<14} {v}")

    if qualified:
        logger.info(f"\nTop {min(25, len(qualified))} qualified:")
        logger.info(
            f"{'Address':<46} {'Score':>6} {'WR7d':>6} {'WR30d':>6} "
            f"{'Profit7d':>12} {'Profit30d':>12} {'Tx7d':>6}"
        )
        logger.info("-" * 100)
        for w in qualified[:25]:
            logger.info(
                f"{w['address']:<46} {w['score']:>6.1f} "
                f"{w['winrate_7d']*100:>5.1f}% {w['winrate_30d']*100:>5.1f}% "
                f"${w['profit_7d_usd']:>10,.0f} ${w['profit_30d_usd']:>10,.0f} "
                f"{w['txs_7d']:>6}"
            )
        if len(qualified) > 25:
            logger.info(f"  ... and {len(qualified) - 25} more")

    if not apply:
        logger.info(f"\nDRY RUN — {len(qualified)} wallets would be added.")
        logger.info("Re-run with --apply to write to wallets.json + DB")
        return

    # Apply
    logger.info(f"\nApplying {len(qualified)} wallets...")
    now = datetime.now(timezone.utc).isoformat()
    by_addr = {w["address"]: w for w in existing_data["wallets"]}
    added = 0
    for w in qualified:
        pnl_sol_est = w["profit_30d_usd"] / 130.0
        by_addr[w["address"]] = {
            "address": w["address"],
            "label": f"gmgn-filter-{w['score']:.0f}",
            "source": "gmgn-apify",
            "added_at": now,
            "score": w["score"],
            "stats": {
                "total_trades": w["txs_7d"],
                "win_rate": w["winrate_7d"],
                "total_pnl_sol": pnl_sol_est,
                "avg_hold_minutes": 0,
                "profit_7d_usd": w["profit_7d_usd"],
                "profit_30d_usd": w["profit_30d_usd"],
                "winrate_7d": w["winrate_7d"],
                "winrate_30d": w["winrate_30d"],
            },
            "active": True,
        }
        added += 1

    deduped, _ = dedupe_wallets(list(by_addr.values()))
    existing_data["wallets"] = deduped
    existing_data["updated_at"] = now
    existing_data["version"] = existing_data.get("version", 0) + 1
    wallets_path.write_text(json.dumps(existing_data, indent=2))
    logger.info(f"+{added} added. Total in file: {len(deduped)}")

    # Sync to DB
    from db.database import get_db
    db = await get_db()
    for w in deduped:
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
    logger.info("Synced to DB")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.filter_and_rank <input.json> [--apply]")
        sys.exit(1)
    input_file = Path(sys.argv[1])
    apply_flag = "--apply" in sys.argv
    asyncio.run(main(input_file, apply=apply_flag))
