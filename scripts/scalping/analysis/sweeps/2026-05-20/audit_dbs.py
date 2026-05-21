"""Audit the local bridge DBs — symbol breakdown, date ranges, trade counts."""
import sqlite3
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent.parent / "data"
DBS = ["v1_bridge.db", "v2_bridge.db", "v3_bridge.db"]

print(f"DATA dir: {DATA}")
print()
for db in DBS:
    p = DATA / db
    if not p.exists():
        print(f"{db}: MISSING")
        continue
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT symbol, COUNT(*) as n,
               MIN(opened_at) as first_open,
               MAX(opened_at) as last_open,
               SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) as wins,
               ROUND(SUM(pnl_usdt), 2) as net_pnl
        FROM trade_log
        GROUP BY symbol
        ORDER BY symbol
    """)
    print(f"=== {db} ===")
    for r in cur.fetchall():
        d = dict(r)
        print(f"  {d['symbol']:>12}  n={d['n']:>4}  pnl=${d['net_pnl']:>+10}  "
              f"wins={d['wins']:>3}  {d['first_open']} -> {d['last_open']}")
    conn.close()
print()
