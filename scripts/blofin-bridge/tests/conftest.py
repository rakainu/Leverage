import pytest


@pytest.fixture
def sol_instrument():
    """BloFin SOL-USDT instrument metadata (sampled)."""
    return {
        "instId": "SOL-USDT",
        "contractValue": 1.0,   # 1 contract = 1 SOL
        "minSize": 1.0,          # min 1 contract
        "lotSize": 1.0,          # increments of 1 contract
        "tickSize": 0.001,
    }
