import pytest


@pytest.fixture
def sol_instrument():
    """BloFin SOL-USDT instrument metadata (real demo values as of 2026-04).

    contractValue=1.0 means 1 contract == 1 SOL.
    """
    return {
        "instId": "SOL-USDT",
        "contractValue": 1.0,
        "minSize": 0.01,
        "lotSize": 0.01,
        "tickSize": 0.01,
    }


@pytest.fixture
def zec_instrument():
    """BloFin ZEC-USDT instrument metadata (real demo values as of 2026-04).

    contractValue=0.1 means 1 contract == 0.1 ZEC. lotSize=1.0 means orders
    must be in whole-contract increments. minSize=1.0 means at least 1
    contract = 0.1 ZEC. This exercises the base-units-vs-contracts conversion
    that SOL (contractValue=1.0) accidentally hides.
    """
    return {
        "instId": "ZEC-USDT",
        "contractValue": 0.1,
        "minSize": 1.0,
        "lotSize": 1.0,
        "tickSize": 0.01,
    }
