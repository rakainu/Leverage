from unittest.mock import MagicMock

import pytest

from blofin_bridge.reconcile import reconcile, ReconcileReport
from blofin_bridge.state import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "rec.db")


def test_clean_state_returns_no_drift(store):
    blofin = MagicMock()
    blofin.fetch_positions.return_value = []
    rep = reconcile(store=store, blofin=blofin)
    assert rep.frozen_symbols == []
    assert rep.drift_count == 0


def test_sqlite_has_position_blofin_doesnt(store):
    store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    blofin = MagicMock()
    blofin.fetch_positions.return_value = []    # BloFin is flat
    rep = reconcile(store=store, blofin=blofin)
    assert "SOL-USDT" in rep.frozen_symbols
    assert rep.drift_count == 1


def test_blofin_has_position_sqlite_doesnt(store):
    blofin = MagicMock()
    blofin.fetch_positions.return_value = [{
        "symbol": "SOL/USDT:USDT",
        "info": {"instId": "SOL-USDT"},
        "contracts": 12,
        "side": "long",
    }]
    rep = reconcile(store=store, blofin=blofin)
    assert "SOL-USDT" in rep.frozen_symbols
