"""Wallet deduplication — ensures wallets.json and DB have no duplicate addresses.

Dedup strategy: when duplicates exist, keep the entry with:
  1. source == "manual" (user-added, never drop)
  2. highest score
  3. most recent updated_at
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("smc.curation.dedupe")


def dedupe_wallets(wallets: list[dict]) -> tuple[list[dict], int]:
    """Remove duplicate wallet addresses, keeping the best entry for each.

    Returns (deduped_list, num_removed).
    """
    by_addr: dict[str, dict] = {}
    removed = 0

    for w in wallets:
        addr = w.get("address")
        if not addr:
            removed += 1
            continue

        if addr not in by_addr:
            by_addr[addr] = w
            continue

        # Duplicate — pick the better one
        existing = by_addr[addr]
        keeper = _pick_better(existing, w)
        by_addr[addr] = keeper
        removed += 1

    return list(by_addr.values()), removed


def _pick_better(a: dict, b: dict) -> dict:
    """Pick the better of two duplicate wallet entries."""
    # Rule 1: manual source always wins
    if a.get("source") == "manual" and b.get("source") != "manual":
        return a
    if b.get("source") == "manual" and a.get("source") != "manual":
        return b

    # Rule 2: higher score wins
    a_score = float(a.get("score", 0) or 0)
    b_score = float(b.get("score", 0) or 0)
    if a_score != b_score:
        return a if a_score > b_score else b

    # Rule 3: more recent updated_at wins
    a_updated = a.get("updated_at") or a.get("added_at") or ""
    b_updated = b.get("updated_at") or b.get("added_at") or ""
    return a if a_updated >= b_updated else b


def dedupe_wallets_file(path: Path | str) -> int:
    """Dedupe a wallets.json file in place. Returns number of duplicates removed."""
    path = Path(path)
    data = json.loads(path.read_text())
    wallets = data.get("wallets", [])
    original_count = len(wallets)

    deduped, removed = dedupe_wallets(wallets)
    if removed == 0:
        return 0

    data["wallets"] = deduped
    path.write_text(json.dumps(data, indent=2))
    logger.info(f"Deduped {path.name}: {original_count} → {len(deduped)} ({removed} removed)")
    return removed


if __name__ == "__main__":
    import sys
    from pathlib import Path

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Default path or argv[1]
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config/wallets.json")

    if not target.exists():
        print(f"File not found: {target}")
        sys.exit(1)

    removed = dedupe_wallets_file(target)
    if removed == 0:
        print(f"No duplicates in {target}")
    else:
        print(f"Removed {removed} duplicates from {target}")
