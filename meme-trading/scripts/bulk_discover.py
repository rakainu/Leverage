"""Bulk wallet discovery via Birdeye — find 100-200 quality memecoin traders.

Usage:
    python -m scripts.bulk_discover          # dry run — shows what it would add
    python -m scripts.bulk_discover --apply   # actually writes to wallets.json + DB

Pulls trending tokens → top traders per token → PnL stats per wallet → scores → filters.
Only keeps wallets that are active, profitable, and actually trading memecoins.
"""

import asyncio
import json
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from curation.birdeye import BirdeyeClient
from curation.scorer import WalletScorer
from config.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bulk_discover")

# Hard filters — wallets must pass ALL of these
MIN_WIN_RATE = 0.45        # 45%+ win rate
MIN_TRADES = 10            # at least 10 trades (not a one-hit wonder)
MIN_PNL_USD = 0.0          # must be net positive (any amount)
MIN_SCORE = 60.0           # composite score threshold
MAX_TRADES = 10000         # bot filter: humans don't make 100K+ trades
MAX_UNIQUE_TOKENS = 500    # bot filter: humans don't trade thousands of tokens


async def discover_wallets(settings: Settings, apply: bool = False):
    """Main discovery pipeline."""
    http = httpx.AsyncClient(timeout=30)
    birdeye = BirdeyeClient(settings.birdeye_api_key, http)
    scorer = WalletScorer()

    # ── Step 1: Get trending tokens ──
    logger.info("Step 1: Fetching trending tokens from Birdeye...")
    trending = await birdeye.trending_tokens(limit=20)
    if not trending:
        logger.error("No trending tokens returned — check Birdeye API key")
        return

    logger.info(f"Got {len(trending)} trending tokens")
    for t in trending[:5]:
        logger.info(f"  {t['symbol']}: ${t.get('volume_24h', 0):,.0f} vol")

    # ── Step 2: Get top traders for each token ──
    logger.info("\nStep 2: Pulling top 10 traders per token...")
    all_trader_addresses = set()
    token_count = 0

    for token in trending:
        traders = await birdeye.get_top_traders(token["address"], pages=1)
        if traders:
            # Only keep wallets that are actually buying (not just selling/dumping)
            buyers = [t for t in traders if t.get("buys", 0) > 0]
            new = set(t["address"] for t in buyers) - all_trader_addresses
            all_trader_addresses.update(new)
            token_count += 1
            logger.info(
                f"  {token['symbol']}: {len(traders)} traders, {len(buyers)} buyers, "
                f"{len(new)} new (total unique: {len(all_trader_addresses)})"
            )

    logger.info(f"\nFound {len(all_trader_addresses)} unique buyer addresses from {token_count} tokens")

    if not all_trader_addresses:
        logger.error("No traders found — Birdeye may be rate-limiting or returning empty")
        return

    # ── Step 3: Get PnL stats for each wallet ──
    logger.info(f"\nStep 3: Fetching PnL stats for {len(all_trader_addresses)} wallets...")
    logger.info("  (This takes a few minutes at 55 req/min rate limit)")

    wallet_stats = {}
    failed = 0
    for i, addr in enumerate(all_trader_addresses, 1):
        pnl = await birdeye.wallet_pnl(addr)
        if pnl and pnl.get("total_trades", 0) > 0:
            wallet_stats[addr] = pnl
        else:
            failed += 1

        if i % 20 == 0:
            logger.info(f"  Progress: {i}/{len(all_trader_addresses)} ({len(wallet_stats)} with stats, {failed} failed)")

    logger.info(f"\nGot stats for {len(wallet_stats)} wallets ({failed} failed/empty)")

    # ── Step 4: Score and filter ──
    logger.info("\nStep 4: Scoring and filtering...")
    qualified = []
    rejected = {"low_winrate": 0, "few_trades": 0, "negative_pnl": 0, "low_score": 0, "bot": 0}

    for addr, stats in wallet_stats.items():
        win_rate = stats.get("win_rate", 0)
        total_trades = stats.get("total_trades", 0)
        total_pnl_usd = stats.get("total_pnl_usd", 0)
        unique_tokens = stats.get("unique_tokens", 0)

        # Bot filter — reject MEV/sniper bots that trade everything
        if total_trades > MAX_TRADES or unique_tokens > MAX_UNIQUE_TOKENS:
            rejected["bot"] += 1
            continue

        # Hard filters
        if win_rate < MIN_WIN_RATE:
            rejected["low_winrate"] += 1
            continue
        if total_trades < MIN_TRADES:
            rejected["few_trades"] += 1
            continue
        if total_pnl_usd <= MIN_PNL_USD:
            rejected["negative_pnl"] += 1
            continue

        # Estimate PnL in SOL (~$130/SOL rough conversion for scoring)
        pnl_sol_est = total_pnl_usd / 130.0

        # Score using our existing scorer
        scorer_input = {
            "win_rate": win_rate,
            "total_pnl_sol": pnl_sol_est,
            "total_trades": total_trades,
            "last_active": datetime.now(timezone.utc).isoformat(),  # on trending tokens = active now
            "avg_hold_minutes": 60,  # default estimate for memecoin traders
        }
        score = scorer.score(scorer_input)

        if score < MIN_SCORE:
            rejected["low_score"] += 1
            continue

        qualified.append({
            "address": addr,
            "score": score,
            "win_rate": win_rate,
            "total_trades": total_trades,
            "total_pnl_usd": total_pnl_usd,
            "realized_pnl_usd": stats.get("realized_pnl_usd", 0),
            "total_invested_usd": stats.get("total_invested_usd", 0),
            "pnl_sol_est": pnl_sol_est,
            "wins": stats.get("wins", 0),
            "losses": stats.get("losses", 0),
            "unique_tokens": stats.get("unique_tokens", 0),
        })

    # Sort by score descending
    qualified.sort(key=lambda w: w["score"], reverse=True)

    # ── Step 5: Report ──
    logger.info(f"\n{'='*60}")
    logger.info(f"RESULTS")
    logger.info(f"{'='*60}")
    logger.info(f"Total unique wallets found:  {len(all_trader_addresses)}")
    logger.info(f"With PnL stats:              {len(wallet_stats)}")
    logger.info(f"QUALIFIED:                   {len(qualified)}")
    logger.info(f"")
    logger.info(f"Rejected breakdown:")
    logger.info(f"  Bot (>{MAX_TRADES} trades or >{MAX_UNIQUE_TOKENS} tokens): {rejected['bot']}")
    logger.info(f"  Win rate < {MIN_WIN_RATE*100:.0f}%:           {rejected['low_winrate']}")
    logger.info(f"  Trades < {MIN_TRADES}:              {rejected['few_trades']}")
    logger.info(f"  Negative PnL:              {rejected['negative_pnl']}")
    logger.info(f"  Score < {MIN_SCORE}:               {rejected['low_score']}")
    logger.info(f"")

    if qualified:
        logger.info(f"Top 20 qualified wallets:")
        logger.info(f"{'Address':<48} {'Score':>6} {'WR%':>6} {'Trades':>7} {'Tokens':>7} {'PnL USD':>12}")
        logger.info("-" * 95)
        for w in qualified[:20]:
            logger.info(
                f"{w['address']:<48} {w['score']:>6.1f} {w['win_rate']*100:>5.1f}% "
                f"{w['total_trades']:>7} {w['unique_tokens']:>7} ${w['total_pnl_usd']:>11,.2f}"
            )
        if len(qualified) > 20:
            logger.info(f"  ... and {len(qualified) - 20} more")

    if not qualified:
        logger.warning("No wallets passed all filters. Try relaxing thresholds.")
        return

    # ── Step 6: Apply (write to wallets.json) ──
    if not apply:
        logger.info(f"\nDRY RUN — {len(qualified)} wallets would be added.")
        logger.info("Run with --apply to write to wallets.json and sync to DB.")
        return

    logger.info(f"\nApplying: adding {len(qualified)} wallets to wallets.json...")
    wallets_path = Path(settings.wallets_json_path)
    data = json.loads(wallets_path.read_text())
    existing = {w["address"] for w in data["wallets"]}

    added = 0
    updated = 0
    now = datetime.now(timezone.utc).isoformat()

    for w in qualified:
        wallet_entry = {
            "address": w["address"],
            "label": f"birdeye-{w['win_rate']*100:.0f}wr-{w['total_trades']}t-${w['total_pnl_usd']:,.0f}",
            "source": "birdeye-bulk",
            "added_at": now,
            "score": w["score"],
            "stats": {
                "total_trades": w["total_trades"],
                "win_rate": w["win_rate"],
                "total_pnl_sol": w["pnl_sol_est"],
                "avg_hold_minutes": 0,
            },
            "active": True,
        }

        if w["address"] in existing:
            for i, ew in enumerate(data["wallets"]):
                if ew["address"] == w["address"]:
                    data["wallets"][i]["score"] = w["score"]
                    data["wallets"][i]["stats"] = wallet_entry["stats"]
                    data["wallets"][i]["updated_at"] = now
                    updated += 1
                    break
        else:
            data["wallets"].append(wallet_entry)
            added += 1

    data["updated_at"] = now
    data["version"] = data.get("version", 0) + 1
    wallets_path.write_text(json.dumps(data, indent=2))

    logger.info(f"Done: +{added} added, ~{updated} updated")
    logger.info(f"Total wallets in system: {len(data['wallets'])}")

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
                    w["address"], w.get("label"), w.get("source", "birdeye-bulk"),
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
        logger.error(f"DB sync failed (wallets.json is still updated): {e}")


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    settings = Settings()
    asyncio.run(discover_wallets(settings, apply=apply))
