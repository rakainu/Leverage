import pytest


@pytest.fixture
def sol_instrument():
    """BloFin SOL-USDT instrument metadata (real demo values as of 2026-04)."""
    return {
        "instId": "SOL-USDT",
        "contractValue": 1.0,   # 1 unit = 1 SOL
        "minSize": 0.01,         # 0.01 SOL minimum
        "lotSize": 0.01,         # 0.01 SOL increments
        "tickSize": 0.01,
    }
