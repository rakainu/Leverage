"""Bootstrap script: seed wallet_tiers from wallets.json as Tier A.

Idempotent — only inserts wallets that don't already have a tier row.
Run once before the first main.py start, or whenever new wallets are added.

Usage:
    python -m runner.scripts.bootstrap_wallet_tiers
"""
import asyncio
import json
import sys
from pathlib import Path

from runner.db.database import Database
from runner.utils.logging import configure_logging, get_logger

logger = get_logger("runner.scripts.bootstrap_wallet_tiers")


async def bootstrap_wallet_tiers(db: Database, wallets_json_path: Path | str) -> int:
    """Insert every active wallet from wallets.json into wallet_tiers as Tier A.

    Returns the number of active wallets processed (not necessarily inserted —
    existing rows are skipped).
    """
    wallets_path = Path(wallets_json_path)
    if not wallets_path.exists():
        raise FileNotFoundError(f"wallets file not found: {wallets_path}")

    data = json.loads(wallets_path.read_text(encoding="utf-8"))
    wallets = data.get("wallets") or []
    active = [w for w in wallets if w.get("active") and "address" in w]

    assert db.conn is not None
    inserted = 0
    for w in active:
        result = await db.conn.execute(
            """
            INSERT INTO wallet_tiers (wallet_address, tier, source)
            VALUES (?, 'A', 'manual_bootstrap')
            ON CONFLICT(wallet_address) DO NOTHING
            """,
            (w["address"],),
        )
        if result.rowcount:
            inserted += 1
    await db.conn.commit()

    logger.info(
        "bootstrap_complete",
        active_wallets=len(active),
        newly_inserted=inserted,
    )
    return len(active)


async def _main() -> None:
    configure_logging("INFO")
    # Defer settings import so the script can be tested without env vars
    from runner.config.settings import get_settings

    settings = get_settings()
    db = Database(settings.db_path)
    await db.connect()
    try:
        count = await bootstrap_wallet_tiers(db, settings.wallets_json_path)
        print(f"Bootstrapped {count} active wallets.")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(_main())
    sys.exit(0)
