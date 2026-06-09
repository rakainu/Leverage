"""Regression test for the close-order size-precision bug (2026-06-09).

A WLD short hung open for days because close_position submitted the raw
pos.base_amount (11685.099999999999) and PaperClient's validate_order rejects
any base amount that carries more decimals than the market's size_decimals.
The fix routes every submitted base amount through round(value, size_decimals).

Run inside the container:
    docker exec scalper-bridge python -m pytest /app/tests/test_size_precision.py -q
or standalone:
    docker exec scalper-bridge python /app/tests/test_size_precision.py
"""
from lighter.paper_client.matching import _fits_decimals


def quantize(value: float, size_decimals: int) -> float:
    """Mirror of Executor.quantize_size (without the MarketConfig lookup)."""
    return round(value, size_decimals)


def test_raw_dust_fails_validator():
    # The exact value that stranded WLD short trade #56.
    assert _fits_decimals(11685.099999999999, 1) is False


def test_quantized_dust_passes_validator():
    assert _fits_decimals(quantize(11685.099999999999, 1), 1) is True


def test_quantize_round_trips_across_decimals():
    # Float dust at every precision class we trade (sd=1..5) must survive.
    cases = [
        (11685.099999999999, 1),
        (3612.6800000000003, 1),   # NEAR-like, sd=1
        (1234.5670000000002, 3),   # SOL-like, sd=3
        (98.76500000000001, 2),    # HYPE-like, sd=2
        (0.123450000000001, 5),
    ]
    for value, sd in cases:
        q = quantize(value, sd)
        assert _fits_decimals(q, sd), f"quantize({value!r}, {sd}) -> {q!r} still fails"


if __name__ == "__main__":
    import sys
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
