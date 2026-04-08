import pytest
from blofin_bridge.sizing import contracts_for_margin, SizingError


def test_basic_margin_at_10x(sol_instrument):
    # $100 margin * 10x = $1000 notional
    # at $80/SOL = 12.5 SOL = 12 contracts (rounded down to lot)
    size = contracts_for_margin(
        margin_usdt=100,
        leverage=10,
        last_price=80.0,
        instrument=sol_instrument,
    )
    assert size == 12


def test_rounds_down_to_lot(sol_instrument):
    # margin that produces fractional contracts must floor
    size = contracts_for_margin(
        margin_usdt=100, leverage=10, last_price=83.45,
        instrument=sol_instrument,
    )
    # 1000 / 83.45 = 11.98 -> 11
    assert size == 11


def test_below_min_size_raises(sol_instrument):
    # $5 margin * 10x / $80 = 0.625 SOL, below minSize 1.0
    with pytest.raises(SizingError, match="below minSize"):
        contracts_for_margin(
            margin_usdt=5, leverage=10, last_price=80.0,
            instrument=sol_instrument,
        )


def test_zero_leverage_raises(sol_instrument):
    with pytest.raises(SizingError, match="leverage must be positive"):
        contracts_for_margin(
            margin_usdt=100, leverage=0, last_price=80.0,
            instrument=sol_instrument,
        )


def test_partial_close_rounds_down(sol_instrument):
    from blofin_bridge.sizing import close_fraction_to_contracts
    # 40% of 12 contracts = 4.8 -> 4
    assert close_fraction_to_contracts(12, 0.40, sol_instrument) == 4


def test_partial_close_returns_zero_if_below_lot(sol_instrument):
    from blofin_bridge.sizing import close_fraction_to_contracts
    # 10% of 2 contracts = 0.2 -> 0
    assert close_fraction_to_contracts(2, 0.10, sol_instrument) == 0
