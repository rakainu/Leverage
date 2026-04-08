import pytest
from blofin_bridge.sizing import (
    contracts_for_margin,
    close_fraction_to_contracts,
    SizingError,
)


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


# === Multi-instrument tests: contractValue != 1.0 ===

def test_zec_size_returns_base_units_not_contracts(zec_instrument):
    """$100 margin x 10x at $324 ZEC should return ~3 ZEC (base units)
    not ~30 (contracts). Each contract = 0.1 ZEC.
    """
    size = contracts_for_margin(
        margin_usdt=100, leverage=10, last_price=324.0,
        instrument=zec_instrument,
    )
    # 1000 / 324 = 3.0864 ZEC
    # = 30.864 contracts (cv=0.1)
    # floor to lot 1.0 = 30 contracts
    # = 30 * 0.1 = 3.0 ZEC base units
    assert size == 3.0


def test_zec_partial_close_returns_base_units(zec_instrument):
    """40% of 3.0 ZEC = 1.2 ZEC = 12 contracts (whole-lot) = 1.2 ZEC base."""
    closed = close_fraction_to_contracts(3.0, 0.40, zec_instrument)
    assert closed == 1.2


def test_zec_partial_close_with_lot_rounding(zec_instrument):
    """30% of 3.0 ZEC = 0.9 ZEC = 9 contracts -> 0.9 ZEC base. Whole lot."""
    closed = close_fraction_to_contracts(3.0, 0.30, zec_instrument)
    assert closed == 0.9


def test_zec_partial_close_below_lot_rounds_to_zero(zec_instrument):
    """5% of 0.5 ZEC = 0.025 ZEC = 0.25 contracts -> floors to 0 (below 1 lot)."""
    closed = close_fraction_to_contracts(0.5, 0.05, zec_instrument)
    assert closed == 0.0


def test_zec_below_min_raises(zec_instrument):
    """$10 margin × 10x at $324 ZEC = 0.31 ZEC = 3.1 contracts. Above min,
    should pass. Use $1 margin to actually trip the floor."""
    with pytest.raises(SizingError):
        contracts_for_margin(
            margin_usdt=1, leverage=10, last_price=324.0,
            instrument=zec_instrument,
        )


def test_sol_unchanged_after_refactor(sol_instrument):
    """Sanity: SOL still gives same answer as v1.1 (contractValue=1.0
    makes the conversion a no-op)."""
    # $100 margin × 10x at $80 = 12.5 SOL = 1250 contracts (cv=1.0... wait
    # cv=1.0 so contracts = base). 1250/0.01 lot = 1250 lots, no rounding.
    # 12.50 contracts × 1.0 = 12.50 ZEC base. Wait SOL.
    # Just verify: 100/10 = $1000 / $80 = 12.5 SOL.
    size = contracts_for_margin(
        margin_usdt=100, leverage=10, last_price=80.0,
        instrument=sol_instrument,
    )
    assert size == 12.5
