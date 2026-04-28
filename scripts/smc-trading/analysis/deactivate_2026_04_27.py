#!/usr/bin/env python3
"""One-shot wallet deactivation triggered by the 2026-04-27 cause-of-loss audit.

Kills:
  - All source='nansen-live' wallets (avg pnl when present: -22.6% vs gmgn -14.5%)
  - 5 named GMGN wallets with 6+ closed positions and 0 wins

Writes wallets.json atomically (.tmp + os.replace) and mirrors the change into
the tracked_wallets table so the next curation cycle sees consistent state.

Run on the VPS only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

WALLETS_JSON = Path("/docker/smc-trading/config/wallets.json")
DB_PATH = Path("/var/lib/docker/volumes/smc-trading_smc-data/_data/smc.db")

# 5 GMGN wallets from audit top-20 with 6+ closed positions and zero wins.
DEAD_GMGN_ADDRS = {
    "DYAn4XpAkN5mhiXkRB7dGq4Jadnx6XYgu8L5b3WGhbrt",  # 12 trades, 0 wins, -0.37 SOL
    "6SM6A8WuvryrpAXrt4qTuXadYk7aPvSff2HkS4XNpVzP",  #  9 trades, 0 wins, -0.32 SOL
    "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk",  # 11 trades, 0 wins, -0.37 SOL
    "5QLUCUFA62q1b2y8TKfFf31rL4Qj8zsKTQzuQ8xbTa1o",  #  7 trades, 0 wins, -0.22 SOL
    "3nG9zBc6fTne3j9kkS1CB5quyt25CS29GEjvMGNKmDSz",  #  6 trades, 0 wins, -0.19 SOL
}


def deactivate_in_json() -> tuple[int, int]:
    data = json.loads(WALLETS_JSON.read_text())
    nansen_killed = 0
    gmgn_killed = 0
    for w in data.get("wallets", []):
        if not w.get("active", True):
            continue
        if w.get("source") == "nansen-live":
            w["active"] = False
            nansen_killed += 1
        elif w.get("address") in DEAD_GMGN_ADDRS:
            w["active"] = False
            gmgn_killed += 1
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["version"] = data.get("version", 0) + 1
    tmp = WALLETS_JSON.with_suffix(WALLETS_JSON.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, WALLETS_JSON)
    return nansen_killed, gmgn_killed


def deactivate_in_db() -> tuple[int, int]:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        nansen = conn.execute(
            "UPDATE tracked_wallets SET active=0, updated_at=CURRENT_TIMESTAMP "
            "WHERE source='nansen-live' AND active=1"
        ).rowcount
        placeholders = ",".join("?" * len(DEAD_GMGN_ADDRS))
        gmgn = conn.execute(
            f"UPDATE tracked_wallets SET active=0, updated_at=CURRENT_TIMESTAMP "
            f"WHERE address IN ({placeholders}) AND active=1",
            tuple(DEAD_GMGN_ADDRS),
        ).rowcount
        conn.commit()
        return nansen, gmgn
    finally:
        conn.close()


def main() -> int:
    print(f"=== wallets.json deactivation @ {datetime.now(timezone.utc).isoformat()} ===")
    nj, gj = deactivate_in_json()
    print(f"  json: nansen-live={nj}  gmgn-deadweight={gj}")
    print("=== tracked_wallets DB deactivation ===")
    nd, gd = deactivate_in_db()
    print(f"  db  : nansen-live={nd}  gmgn-deadweight={gd}")
    # Reload-time sanity: count what's left active in json
    data = json.loads(WALLETS_JSON.read_text())
    by_src: dict[str, int] = {}
    for w in data["wallets"]:
        if w.get("active", True):
            by_src[w.get("source", "manual")] = by_src.get(w.get("source", "manual"), 0) + 1
    print("=== remaining active in wallets.json ===")
    for k, v in sorted(by_src.items()):
        print(f"  {k}: {v}")
    print(f"  TOTAL: {sum(by_src.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
