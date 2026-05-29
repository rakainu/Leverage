"""Consolidate the REAL Pro V3 [SMRT Algo] webhook signals from the BloFin-bridge DBs.

Why this exists
---------------
The Pro V3 indicator is a *protected* TradingView script and its on-chart TP/SL labels
are capped at ~500 objects (~1.5 days of a chatty signal). That is far too little, and
chart labels can repaint. BUT every Pro V3 buy/sell webhook the bridge ever received is
stored verbatim in each era's `pending_signals` table — the true, no-repaint signal stream.

Sources (BloFin bridge eras; all receive the SAME Pro V3 webhooks):
  v1_bridge.db      2026-04-10 .. 2026-04-29
  v2_bridge.db      2026-04-29 .. 2026-05-12
  v3_bridge.db      2026-05-12 .. 2026-05-14
  live_v31_bridge.db 2026-05-16 .. now

EXCLUDED: lighter_paper.db — that bridge regenerates V3 signals locally (HA-flip replica),
it is NOT the Pro V3 webhook stream.

`pending_signals` columns we use (common to all eras):
  symbol, action(buy/sell), signal_price, created_at, status, filled_at, fill_price
  status in {filled, expired, cancelled} — 'filled' = passed the live EMA9-retest+slope gate.

Output: pro_v3_signals.csv — one row per raw webhook, deduped across eras by (symbol,action,created_at).
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"   # v1/v2/v3 archives live here

SOURCES = [
    ("v1",   DATA / "v1_bridge.db"),
    ("v2",   DATA / "v2_bridge.db"),
    ("v3",   DATA / "v3_bridge.db"),
    ("live", HERE / "live_v31_bridge.db"),
]

COLS = ["symbol", "action", "signal_price", "created_at", "status", "filled_at", "fill_price"]


def load_one(era: str, path: Path) -> pd.DataFrame:
    if not path.exists():
        print(f"  !! missing {path}")
        return pd.DataFrame()
    con = sqlite3.connect(path)
    df = pd.read_sql_query(f"SELECT {', '.join(COLS)} FROM pending_signals", con)
    con.close()
    df["era"] = era
    return df


def main():
    frames = [load_one(era, p) for era, p in SOURCES]
    frames = [f for f in frames if not f.empty]
    allsig = pd.concat(frames, ignore_index=True)

    # Normalize timestamps
    allsig["created_at"] = pd.to_datetime(allsig["created_at"], utc=True)
    allsig["filled_at"] = pd.to_datetime(allsig["filled_at"], utc=True)
    allsig = allsig.sort_values("created_at").reset_index(drop=True)

    # Dedup across overlapping eras: same symbol+action within 60s = same webhook
    allsig["dedup_key"] = (
        allsig["symbol"] + "|" + allsig["action"] + "|"
        + (allsig["created_at"].astype("int64") // 60_000_000_000).astype(str)
    )
    before = len(allsig)
    allsig = allsig.drop_duplicates("dedup_key").drop(columns="dedup_key").reset_index(drop=True)

    out = HERE / "pro_v3_signals.csv"
    allsig.to_csv(out, index=False)

    print(f"Consolidated {before} rows -> {len(allsig)} unique signals")
    print(f"Window: {allsig['created_at'].min()} .. {allsig['created_at'].max()}")
    print("\nBy symbol/action:")
    print(allsig.groupby(["symbol", "action"]).size().to_string())
    print("\nBy era:")
    print(allsig.groupby("era").size().to_string())
    print("\nBy status:")
    print(allsig.groupby("status").size().to_string())
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
