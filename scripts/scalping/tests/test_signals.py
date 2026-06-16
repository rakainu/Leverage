"""Tests for the V3.2 self-generated HA signal module.

The headline test is `test_signal_series_matches_engine_golden`: the bridge's
pure-Python signal generator must reproduce the backtest engine's
`generate_v3_signals` output bar-for-bar on a 700-bar ZEC golden fixture. That
fixture was produced by the same engine that backtests +$19k / PF 2.80, so
matching it is what guarantees the live signal == the proven-profitable signal.

Recursive indicators (Heikin-Ashi, EMA, Wilder SMMA) need warm-up to converge,
so signal comparison starts at WARMUP bars into the fixture.
"""
from __future__ import annotations
import csv
from pathlib import Path

import pytest

from blofin_bridge import signals

FIXTURE = Path(__file__).parent / "fixtures" / "signal_golden_fixture.csv"
WARMUP = 200  # bars of fixture history before SMMA/EMA seeds wash out


def _load_fixture() -> list[dict]:
    with FIXTURE.open() as f:
        return list(csv.DictReader(f))


def _bars_from_fixture(rows) -> list[signals.Bar]:
    return [
        signals.Bar(
            open=float(r["Open"]), high=float(r["High"]),
            low=float(r["Low"]), close=float(r["Close"]),
        )
        for r in rows
    ]


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------
def test_heikin_ashi_seed_and_recursion():
    # ha_close = ohlc/4 ; ha_open[0] = (o+c)/2 ; ha_open[i] = (haO[i-1]+haC[i-1])/2
    bars = [signals.Bar(10, 12, 8, 11), signals.Bar(11, 13, 10, 12)]
    ha_open, ha_close = signals.heikin_ashi(bars)
    assert ha_close[0] == pytest.approx((10 + 12 + 8 + 11) / 4)
    assert ha_open[0] == pytest.approx((10 + 11) / 2)
    assert ha_open[1] == pytest.approx((ha_open[0] + ha_close[0]) / 2)


def test_ema_uses_sma_seed():
    # First EMA value (at index length-1) is the SMA of the first `length`.
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    ema = signals.ema(vals, 3)
    assert ema[2] == pytest.approx((1 + 2 + 3) / 3)        # SMA seed
    assert ema[1] is None and ema[0] is None               # warm-up
    mult = 2 / (3 + 1)
    assert ema[3] == pytest.approx((4 - 2.0) * mult + 2.0)  # 2.0 = seed


def test_smma_wilder_recursion():
    # SMMA seed = SMA of first `length`; then (prev*(L-1)+x)/L.
    vals = [2.0, 4.0, 6.0, 8.0]
    smma = signals.smma(vals, 2)
    assert smma[1] == pytest.approx((2 + 4) / 2)            # seed = 3.0
    assert smma[2] == pytest.approx((3.0 * 1 + 6.0) / 2)    # 4.5
    assert smma[3] == pytest.approx((4.5 * 1 + 8.0) / 2)    # 6.25


# --------------------------------------------------------------------------
# Headline parity test
# --------------------------------------------------------------------------
def test_signal_series_matches_engine_golden():
    rows = _load_fixture()
    bars = _bars_from_fixture(rows)
    series = signals.generate_signal_series(bars, signals.SignalParams())

    assert len(series) == len(bars)

    buy_mismatch, sell_mismatch = [], []
    for i in range(WARMUP, len(bars)):
        want_buy = rows[i]["buy_sig"] == "True"
        want_sell = rows[i]["sell_sig"] == "True"
        if series[i].buy != want_buy:
            buy_mismatch.append(i)
        if series[i].sell != want_sell:
            sell_mismatch.append(i)
        # ADX + body/ATR converge to the engine's values
        assert series[i].adx == pytest.approx(float(rows[i]["adx"]), abs=0.05)
        assert series[i].body_atr_ratio == pytest.approx(
            float(rows[i]["body_atr_ratio"]), abs=0.01)

    assert not buy_mismatch, f"buy signal mismatches at bars {buy_mismatch}"
    assert not sell_mismatch, f"sell signal mismatches at bars {sell_mismatch}"


def test_latest_signal_reports_engine_buys():
    """latest_signal over a prefix returns the same side the engine flagged."""
    rows = _load_fixture()
    bars = _bars_from_fixture(rows)
    checked = 0
    for i in range(WARMUP, len(bars)):
        if rows[i]["buy_sig"] == "True":
            sig = signals.latest_signal(bars[: i + 1], signals.SignalParams())
            assert sig.side == "buy", f"bar {i} should be buy"
            checked += 1
        elif rows[i]["sell_sig"] == "True":
            sig = signals.latest_signal(bars[: i + 1], signals.SignalParams())
            assert sig.side == "sell", f"bar {i} should be sell"
            checked += 1
    assert checked > 5  # fixture actually exercised the path


def test_latest_signal_none_when_no_cross():
    """A flat, no-cross prefix yields no signal."""
    rows = _load_fixture()
    bars = _bars_from_fixture(rows)
    # Find a bar the engine left flat and assert we agree.
    for i in range(WARMUP, len(bars)):
        if rows[i]["buy_sig"] == "False" and rows[i]["sell_sig"] == "False":
            sig = signals.latest_signal(bars[: i + 1], signals.SignalParams())
            assert sig.side is None
            return
    pytest.fail("fixture had no flat bar to test")
