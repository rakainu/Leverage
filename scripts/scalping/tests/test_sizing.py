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

def test_zec_size_returns_contracts(zec_instrument):
    """$100 margin × 10x at $324 ZEC = 3.084 ZEC = 30.84 contracts (cv=0.1)
    floored to lot 1.0 = 30 contracts. ccxt's amount field for BloFin maps
    directly to BloFin's size in contracts.
    """
    size = contracts_for_margin(
        margin_usdt=100, leverage=10, last_price=324.0,
        instrument=zec_instrument,
    )
    assert size == 30   # 30 contracts == 3 ZEC base


def test_zec_partial_close_in_contracts(zec_instrument):
    """40% of 30 contracts = 12 contracts (whole lot)."""
    closed = close_fraction_to_contracts(30, 0.40, zec_instrument)
    assert closed == 12


def test_zec_partial_close_with_lot_rounding(zec_instrument):
    """30% of 30 contracts = 9 contracts (whole lot)."""
    closed = close_fraction_to_contracts(30, 0.30, zec_instrument)
    assert closed == 9


def test_zec_partial_close_below_lot_rounds_to_zero(zec_instrument):
    """5% of 5 contracts = 0.25 contracts -> floors to 0 (below 1 lot)."""
    closed = close_fraction_to_contracts(5, 0.05, zec_instrument)
    assert closed == 0.0


def test_zec_below_min_raises(zec_instrument):
    """$1 margin × 10x at $324 = 0.0308 ZEC = 0.308 contracts -> below
    minSize of 1 contract, should raise."""
    with pytest.raises(SizingError):
        contracts_for_margin(
            margin_usdt=1, leverage=10, last_price=324.0,
            instrument=zec_instrument,
        )


def test_sol_returns_contracts_same_as_base(sol_instrument):
    """SOL contractValue=1.0 means contracts == base SOL count by coincidence."""
    size = contracts_for_margin(
        margin_usdt=100, leverage=10, last_price=80.0,
        instrument=sol_instrument,
    )
    assert size == 12.5   # 12.5 contracts == 12.5 SOL
