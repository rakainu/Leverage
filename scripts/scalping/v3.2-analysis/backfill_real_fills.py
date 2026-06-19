"""One-time backfill: rewrite historical trade_log rows with the REAL exchange
close fill (actual fill price + fees) instead of the last-price estimate the
poller recorded before the honest-accounting fix.

For each closed trade, we match the venue's closed-position history (by
instrument + open time, with an entry-price sanity gate — same matcher the live
bridge now uses) and rewrite:
  - exit_price : real close fill (closeAveragePrice)
  - pnl_usdt   : GROSS, recomputed from the real fill (never from fees)
  - fee_usdt   : venue fee (0 on zero-fee venues)
  - pnl_pct    : gross %, recomputed
  - exit_reason: re-derived from the real fill (fixes SL hits mislabeled 'drift')

Dry-run by default — prints an old-vs-new diff and the net delta. Pass --commit
to write. Reads BloFin demo creds from the container env (run inside the
scalping-v3.2 container, same as the live bridge).

Usage (inside the container):
    python backfill_real_fills.py            # dry-run
    python backfill_real_fills.py --commit   # write
"""
from __future__ import annotations
import os
import sqlite3
import sys
from datetime import datetime

import ccxt

# Reuse the exact matcher the live bridge uses, so backfill == live semantics.
sys.path.insert(0, "/app/src")
from blofin_bridge.blofin_client import match_closed_position  # noqa: E402

DB = os.environ.get("V32_DB", "/app/data/bridge.db")
NOTIONAL_FROM = lambda m, l: (m or 0) * (l or 0)


def _client() -> ccxt.Exchange:
    c = ccxt.blofin({
        "apiKey": os.environ["BLOFIN_DEMO_API_KEY"],
        "secret": os.environ["BLOFIN_DEMO_API_SECRET"],
        "password": os.environ["BLOFIN_DEMO_PASSPHRASE"],
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
    })
    c.urls["api"] = {"rest": "https://demo-trading-openapi.blofin.com"}
    return c


def _history_rows(c: ccxt.Exchange) -> list[dict]:
    resp = c.private_get_account_positions_history({"limit": "100"})
    return resp.get("data") or []


def _gross(side: str, entry: float, exit_px: float, notional: float) -> float:
    move = (exit_px - entry) / entry if side == "long" else (entry - exit_px) / entry
    return move * notional


def _relabel(row: dict, exit_px: float) -> str:
    """Mirror the poller's exit-reason logic against the REAL fill."""
    ta = row["trail_activated"] or 0
    if ta >= 2:
        return "trail_sl"
    if ta == 1:
        return "sl_be"
    initial_sl = row["initial_sl"]
    entry = row["entry_price"]
    if initial_sl and entry and abs(exit_px - initial_sl) / entry <= 0.003:
        return "sl"
    return "drift"


def main(commit: bool) -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM trade_log ORDER BY closed_at")]
    hist = _history_rows(_client())
    print(f"trade_log rows: {len(rows)} | positions-history rows: {len(hist)}\n")

    old_net = new_net = 0.0
    matched = unmatched = 0
    updates = []
    for r in rows:
        try:
            opened_ms = datetime.fromisoformat(r["opened_at"]).timestamp() * 1000
        except Exception:
            opened_ms = 0.0
        m = match_closed_position(
            hist, inst_id=r["symbol"], opened_at_ms=opened_ms,
            entry_price=r["entry_price"],
        )
        old = r["pnl_usdt"] or 0.0
        old_net += old
        if not m:
            unmatched += 1
            new_net += old
            print(f"  UNMATCHED id={r['id']:>4} {r['symbol']:<10} {r['side']:<5} "
                  f"keep pnl={old:+.2f}")
            continue
        matched += 1
        notional = NOTIONAL_FROM(r["margin_usdt"], r["leverage"])
        exit_px = m["close_price"]
        fee = m["fee"]
        gross = _gross(r["side"], r["entry_price"], exit_px, notional)
        reason = _relabel(r, exit_px)
        new_net += gross + fee
        updates.append((round(exit_px, 6), round(gross, 6), round(fee, 6),
                        round(gross / r["margin_usdt"] * 100, 4) if r["margin_usdt"] else None,
                        reason, r["id"]))
        flag = " *RELABEL*" if reason != r["exit_reason"] else ""
        print(f"  id={r['id']:>4} {r['symbol']:<10} {r['side']:<5} "
              f"px {r['exit_price']:.4f}->{exit_px:.4f}  "
              f"pnl {old:+.2f}->{gross:+.2f} fee {fee:+.2f}  "
              f"{r['exit_reason']}->{reason}{flag}")

    print(f"\nmatched={matched} unmatched={unmatched}")
    print(f"OLD net (gross, last-price est): {old_net:+.2f}")
    print(f"NEW net (real gross + fees):     {new_net:+.2f}")
    print(f"delta:                           {new_net - old_net:+.2f}")

    if not commit:
        print("\nDRY-RUN — no changes written. Re-run with --commit to apply.")
        return
    cur = con.cursor()
    cur.executemany(
        "UPDATE trade_log SET exit_price=?, pnl_usdt=?, fee_usdt=?, pnl_pct=?, "
        "exit_reason=? WHERE id=?", updates)
    con.commit()
    print(f"\nCOMMITTED {len(updates)} rows.")


if __name__ == "__main__":
    main(commit="--commit" in sys.argv[1:])
