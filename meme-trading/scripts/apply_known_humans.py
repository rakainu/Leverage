"""Apply the human-pace traders identified in the bulk discovery dry run.

These 4 wallets passed the bot filter (< 10K trades, < 500 tokens) AND all
quality filters (win rate > 45%, positive PnL, score > 60).

One-off script — uses cached data from the discovery run, no API calls.
"""

import asyncio
import json
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import Settings
from db.database import get_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger("apply_humans")

# Wallets identified by bulk_discover dry run that passed bot filter
HUMAN_WALLETS = [
    {
        "address": "Bz429AezLuxgftrYKGCaTJsjZBN6LibmYp9eVfL9MXZ9",
        "score": 79.1,
        "win_rate": 0.585,
        "total_trades": 2568,
        "total_pnl_usd": 115441.52,
        "unique_tokens": 62,
    },
    {
        "address": "2Wgjf72ZiufTcbceVtqHDGz2Jc6PCbfjiJMNGBQzAgZh",
        "score": 78.9,
        "win_rate": 0.583,
        "total_trades": 439,
        "total_pnl_usd": 72002.27,
        "unique_tokens": 23,
    },
    {
        "address": "EJ8BMQuTCPoZ27ac8Pc4ehuU6wRptzKCXBC4DjQXY1VD",
        "score": 71.8,
        "win_rate": 0.500,
        "total_trades": 100,
        "total_pnl_usd": 8355.15,
        "unique_tokens": 9,
    },
    {
        "address": "75uYdSzscTypH5bs6LRN2LSn4AUn1mDqGaMVwcumwxMX",
        "score": 63.1,
        "win_rate": 0.500,
        "total_trades": 74,
        "total_pnl_usd": 1553.01,
        "unique_tokens": 6,
    },
]


async def apply():
    settings = Settings()
    wallets_path = Path(settings.wallets_json_path)
    data = json.loads(wallets_path.read_text())
    existing = {w["address"] for w in data["wallets"]}

    now = datetime.now(timezone.utc).isoformat()
    added = 0

    for w in HUMAN_WALLETS:
        if w["address"] in existing:
            logger.info(f"Skipping {w['address']} (already exists)")
            continue

        pnl_sol_est = w["total_pnl_usd"] / 130.0
        entry = {
            "address": w["address"],
            "label": f"birdeye-{w['win_rate']*100:.0f}wr-{w['total_trades']}t-${w['total_pnl_usd']:,.0f}",
            "source": "birdeye-bulk",
            "added_at": now,
            "score": w["score"],
            "stats": {
                "total_trades": w["total_trades"],
                "win_rate": w["win_rate"],
                "total_pnl_sol": pnl_sol_est,
                "avg_hold_minutes": 0,
            },
            "active": True,
        }
        data["wallets"].append(entry)
        added += 1
        logger.info(f"Added {w['address']} (score {w['score']}, {w['total_trades']} trades, ${w['total_pnl_usd']:,.0f})")

    data["updated_at"] = now
    data["version"] = data.get("version", 0) + 1
    wallets_path.write_text(json.dumps(data, indent=2))
    logger.info(f"\n+{added} added. Total wallets in file: {len(data['wallets'])}")

    # Sync to DB
    db = await get_db()
    for w in data["wallets"]:
        stats = w.get("stats", {})
        await db.execute(
            """INSERT OR REPLACE INTO tracked_wallets
               (address, label, source, score, total_trades, win_rate,
                total_pnl_sol, avg_hold_minutes, active, added_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                w["address"], w.get("label"), w.get("source", "manual"),
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
    asyncio.run(apply())
