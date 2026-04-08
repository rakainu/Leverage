import pytest
from blofin_bridge.sizing import contracts_for_margin, SizingError


def test_basic_margin_at_10x(sol_instrument):
    # $100 margin * 10x = $1000 notional at $80/SOL = 12.5 SOL
    # lotSize 0.01 -> exactly 12.50
    size = contracts_for_margin(
        margin_usdt=100, leverage=10, last_price=80.0,
        instrument=sol_instrument,
    )
    assert size == 12.50


def test_rounds_down_to_lot(sol_instrument):
    # 1000 / 83.45 = 11.98322... -> floored to 11.98 at lot 0.01
    size = contracts_for_margin(
        margin_usdt=100, leverage=10, last_price=83.45,
        instrument=sol_instrument,
    )
    assert size == 11.98


def test_below_min_size_raises(sol_instrument):
    # $0.05 margin * 10x / $80 = 0.00625 SOL, below minSize 0.01
    with pytest.raises(SizingError, match="below minSize"):
        contracts_for_margin(
            margin_usdt=0.05, leverage=10, last_price=80.0,
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
    # 40% of 12.50 SOL = 5.00
    assert close_fraction_to_contracts(12.50, 0.40, sol_instrument) == 5.00


def test_partial_close_tiny_fraction_rounds_to_zero(sol_instrument):
    from blofin_bridge.sizing import close_fraction_to_contracts
    # 1% of 0.5 SOL = 0.005 -> below lot 0.01 -> 0.0
    assert close_fraction_to_contracts(0.5, 0.01, sol_instrument) == 0.0


def test_odd_price_keeps_precision(sol_instrument):
    # 1000 / 77.77 = 12.8583... -> 12.85 at lot 0.01
    size = contracts_for_margin(
        margin_usdt=100, leverage=10, last_price=77.77,
        instrument=sol_instrument,
    )
    assert size == 12.85
