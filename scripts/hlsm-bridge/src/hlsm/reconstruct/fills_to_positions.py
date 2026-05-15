"""Walk a wallet's fills chronologically to reconstruct opens/closes per coin.

Hyperliquid emits per-fill `dir` strings like "Open Long" / "Close Long" / "Open Short"
/ "Close Short". A position is open while running signed size != 0; we record a
HlPosition row for each open->close cycle.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from hlsm.db import Fill, HlPosition


def _sign(direction: str) -> int:
    """+1 if direction lengthens long (open_long), -1 if open_short, 0 otherwise."""
    if direction == "open_long":
        return 1
    if direction == "open_short":
        return -1
    if direction == "close_long":
        return -1
    if direction == "close_short":
        return 1
    return 0


def reconstruct_positions(session: Session, wallet_address: str) -> int:
    """Wipe + rewrite HlPosition rows for one wallet from its fills. Returns count written."""
    # Drop existing positions for this wallet (idempotent rebuild)
    session.query(HlPosition).filter(HlPosition.wallet_address == wallet_address).delete()
    session.flush()

    fills = list(session.execute(
        select(Fill).where(Fill.wallet_address == wallet_address).order_by(Fill.ts)
    ).scalars().all())

    written = 0
    # Per-coin running position
    open_state: dict[str, dict] = {}

    for f in fills:
        coin = f.coin
        d = f.direction
        sign = _sign(d)
        size = Decimal(f.sz)
        px = Decimal(f.px)
        ts: datetime = f.ts

        state = open_state.get(coin)
        # OPEN
        if d in ("open_long", "open_short"):
            if state is None:
                open_state[coin] = {
                    "side": "long" if d == "open_long" else "short",
                    "opened_at": ts,
                    "entry_notional": size * px,
                    "size_open": size,
                    "running_pnl": Decimal("0"),
                }
            else:
                # Scaling into existing position; update entry as VWAP
                total = state["size_open"] + size
                state["entry_notional"] += size * px
                state["size_open"] = total
        # CLOSE
        elif d in ("close_long", "close_short"):
            if state is None:
                # Close without open — likely truncated history; ignore
                continue
            close_size = size
            entry_vwap = (state["entry_notional"] / state["size_open"]) if state["size_open"] > 0 else px
            if state["side"] == "long":
                pnl = (px - entry_vwap) * close_size
            else:
                pnl = (entry_vwap - px) * close_size
            state["running_pnl"] += pnl
            state["size_open"] -= close_size

            if state["size_open"] <= 0:
                # Fully flat now → record the position
                pos = HlPosition(
                    wallet_address=wallet_address,
                    coin=coin,
                    side=state["side"],
                    opened_at=state["opened_at"],
                    closed_at=ts,
                    entry_px=entry_vwap.quantize(Decimal("0.00000001")),
                    exit_px=px,
                    sz=Decimal(str(state["size_open"] + close_size)),
                    realized_pnl=state["running_pnl"].quantize(Decimal("0.00000001")),
                    realized_pnl_pct=(
                        (state["running_pnl"] / (entry_vwap * (state["size_open"] + close_size))) * Decimal(100)
                    ).quantize(Decimal("0.0001")) if entry_vwap > 0 else Decimal("0"),
                    hold_seconds=int((ts - state["opened_at"]).total_seconds()),
                    status="closed",
                )
                session.add(pos)
                written += 1
                open_state.pop(coin, None)
        else:
            # Unknown direction; skip
            continue

    # Anything still open at the end of history — write a row marked status='open'
    for coin, state in open_state.items():
        entry_vwap = (state["entry_notional"] / state["size_open"]) if state["size_open"] > 0 else Decimal("0")
        pos = HlPosition(
            wallet_address=wallet_address,
            coin=coin,
            side=state["side"],
            opened_at=state["opened_at"],
            closed_at=None,
            entry_px=entry_vwap.quantize(Decimal("0.00000001")),
            sz=state["size_open"],
            status="open",
        )
        session.add(pos)
        written += 1

    session.flush()
    return written
