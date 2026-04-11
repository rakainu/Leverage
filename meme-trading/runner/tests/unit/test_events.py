"""BuyEvent dataclass serialization."""
from datetime import datetime, timezone

from runner.ingest.events import BuyEvent


def test_buy_event_fields():
    ev = BuyEvent(
        signature="sigABC",
        wallet_address="Wal1",
        token_mint="Mint1",
        sol_amount=0.5,
        token_amount=1234.5,
        price_sol=0.000405,
        block_time=datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc),
    )

    assert ev.signature == "sigABC"
    assert ev.sol_amount == 0.5
    assert ev.block_time.year == 2026


def test_buy_event_to_db_row_matches_schema_columns():
    ev = BuyEvent(
        signature="sigX",
        wallet_address="W",
        token_mint="M",
        sol_amount=0.25,
        token_amount=500,
        price_sol=0.0005,
        block_time=datetime(2026, 4, 11, 10, 5, 0, tzinfo=timezone.utc),
    )

    row = ev.to_db_row()
    # Must match buy_events schema insert order:
    # signature, wallet_address, token_mint, sol_amount,
    # token_amount, price_sol, block_time
    assert row[0] == "sigX"
    assert row[1] == "W"
    assert row[2] == "M"
    assert row[3] == 0.25
    assert row[4] == 500
    assert row[5] == 0.0005
    assert row[6] == "2026-04-11T10:05:00+00:00"
