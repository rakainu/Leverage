"""Unit test for the event-driven Pro V3 exit handler (tp1/tp2/tp3/sl, 50/25/25)."""
import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import lighter_bridge.main as main_mod
from lighter_bridge.main import Bridge


class FakeExec:
    def __init__(self, base):
        self.positions = {"SOL": types.SimpleNamespace(
            side="long", entry_price=100.0, base_amount=base, opened_at=0.0)}
        self.closed = []

    def is_open(self, s):
        return s in self.positions

    async def reduce_position(self, s, base_to_close, reason):
        pos = self.positions.get(s)
        if pos is None:
            return None
        size = round(min(base_to_close, pos.base_amount), 8)
        pos.base_amount = round(pos.base_amount - size, 8)
        if pos.base_amount <= 1e-9:
            del self.positions[s]
        self.closed.append((reason, round(size, 4)))
        return types.SimpleNamespace(avg_price=101.0, filled_size=size, total_fee=0.0)


def _bridge(base=12.0):
    b = object.__new__(Bridge)               # skip __init__
    b.executor = FakeExec(base)
    b.cfg = types.SimpleNamespace(
        scaleout=types.SimpleNamespace(ratios=[0.5, 0.25, 0.25]),
        exit_model="pro_v3", initial_collateral_usdc=2000.0)
    b.orig_base = {"SOL": base}
    b.realized = {"SOL": 0.0}
    b.legs = {"SOL": []}
    b.tp_seen = {"SOL": set()}
    b.scale = {}
    b.trade_ids = {"SOL": 1}
    b.db = types.SimpleNamespace(update_trade_close=lambda *a, **k: None)
    return b


async def _run(seq, base=12.0):
    b = _bridge(base)
    # no-op the Telegram close notification
    async def _noop(*a, **k):
        return None
    main_mod.notify.notify_close = _noop
    for action in seq:
        await b._handle_exit_action("SOL", action)
    return b


def test_full_ladder_50_25_25():
    b = asyncio.run(_run(["tp1", "tp2", "tp3"]))
    assert b.executor.closed == [("tp1", 6.0), ("tp2", 3.0), ("tp3", 3.0)]
    assert "SOL" not in b.executor.positions          # fully closed
    assert "SOL" not in b.orig_base                    # finalized + cleaned


def test_tp1_then_sl_flattens():
    b = asyncio.run(_run(["tp1", "sl"]))
    assert b.executor.closed == [("tp1", 6.0), ("sl", 6.0)]
    assert "SOL" not in b.executor.positions


def test_duplicate_tp1_ignored():
    b = asyncio.run(_run(["tp1", "tp1"]))
    assert b.executor.closed == [("tp1", 6.0)]          # second tp1 ignored
    assert b.executor.positions["SOL"].base_amount == 6.0


def test_sl_only_closes_all():
    b = asyncio.run(_run(["sl"]))
    assert b.executor.closed == [("sl", 12.0)]
    assert "SOL" not in b.executor.positions


def test_exit_without_position_is_noop():
    b = _bridge()
    del b.executor.positions["SOL"]
    asyncio.run(b._handle_exit_action("SOL", "tp1"))
    assert b.executor.closed == []


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
