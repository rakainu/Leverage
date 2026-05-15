"""Fills -> position reconstruction tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from hlsm.db import Fill, HlPosition, Wallet
from hlsm.reconstruct import reconstruct_positions


def _add_fill(session, *, wallet, ts, coin, direction, px, sz, side="buy", hash_=None):
    session.add(Fill(
        wallet_address=wallet, ts=ts, coin=coin, side=side, direction=direction,
        px=Decimal(str(px)), sz=Decimal(str(sz)),
        hash=hash_ or f"{wallet}-{ts.isoformat()}-{direction}",
    ))


def test_long_open_close_creates_one_position(session):
    session.add(Wallet(address="0xaaa"))
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    _add_fill(session, wallet="0xaaa", ts=t0, coin="PEPE",
              direction="open_long", px="0.00001", sz="1000000", side="buy")
    _add_fill(session, wallet="0xaaa", ts=t0 + timedelta(hours=2), coin="PEPE",
              direction="close_long", px="0.000013", sz="1000000", side="sell")
    session.flush()

    n = reconstruct_positions(session, "0xaaa")
    assert n == 1

    rows = session.query(HlPosition).filter_by(wallet_address="0xaaa").all()
    assert len(rows) == 1
    pos = rows[0]
    assert pos.coin == "PEPE"
    assert pos.side == "long"
    assert pos.status == "closed"
    assert pos.hold_seconds == 2 * 3600
    # +30% move on $10 notional = $3 profit
    assert Decimal(pos.realized_pnl) == Decimal("3")
    assert pos.realized_pnl_pct is not None


def test_short_open_close_creates_loss_when_price_rises(session):
    session.add(Wallet(address="0xaaa"))
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    _add_fill(session, wallet="0xaaa", ts=t0, coin="WIF",
              direction="open_short", px="3.00", sz="100", side="sell")
    _add_fill(session, wallet="0xaaa", ts=t0 + timedelta(hours=1), coin="WIF",
              direction="close_short", px="3.30", sz="100", side="buy")
    session.flush()

    n = reconstruct_positions(session, "0xaaa")
    assert n == 1
    pos = session.query(HlPosition).filter_by(wallet_address="0xaaa").one()
    assert pos.side == "short"
    # Price rose 0.30 on 100 contracts short -> -30 PnL
    assert Decimal(pos.realized_pnl) == Decimal("-30")


def test_position_still_open_at_end_of_history(session):
    session.add(Wallet(address="0xaaa"))
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    _add_fill(session, wallet="0xaaa", ts=t0, coin="BONK",
              direction="open_long", px="0.000020", sz="1000000", side="buy")
    session.flush()
    n = reconstruct_positions(session, "0xaaa")
    assert n == 1
    pos = session.query(HlPosition).filter_by(wallet_address="0xaaa").one()
    assert pos.status == "open"
    assert pos.closed_at is None


def test_idempotent_reconstruct(session):
    session.add(Wallet(address="0xaaa"))
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    _add_fill(session, wallet="0xaaa", ts=t0, coin="PEPE",
              direction="open_long", px="0.00001", sz="1000000", side="buy")
    _add_fill(session, wallet="0xaaa", ts=t0 + timedelta(hours=2), coin="PEPE",
              direction="close_long", px="0.000013", sz="1000000", side="sell")
    session.flush()

    reconstruct_positions(session, "0xaaa")
    first_count = session.query(HlPosition).filter_by(wallet_address="0xaaa").count()
    reconstruct_positions(session, "0xaaa")
    second_count = session.query(HlPosition).filter_by(wallet_address="0xaaa").count()
    assert first_count == second_count == 1
