"""Tests for the V3.2 SignalEngine runtime.

SignalEngine replaces the TradingView webhook as the entry-signal source. Each
scan it fetches recent 5m bars, drops the still-forming candle, runs the proven
HA-V3 signal on the last *closed* bar, and — on a fresh cross — queues a pending
signal (exactly what `router.dispatch` did for a webhook). The existing poller
then handles the EMA-retest, gates, and trailing exit unchanged.
"""
from __future__ import annotations
import csv
from pathlib import Path

import pytest

from blofin_bridge.signals import SignalParams
from blofin_bridge.signal_engine import SignalEngine
from blofin_bridge.state import Store

FIXTURE = Path(__file__).parent / "fixtures" / "signal_golden_fixture.csv"


def _rows():
    with FIXTURE.open() as f:
        return list(csv.DictReader(f))


def _ohlcv(rows):
    """Fixture rows -> ccxt OHLCV [ts, o, h, l, c, v] at 5m spacing."""
    out, ts = [], 1_600_000_000_000
    for r in rows:
        out.append([ts, float(r["Open"]), float(r["High"]),
                    float(r["Low"]), float(r["Close"]), 0.0])
        ts += 300_000
    return out


def _first_index(rows, col, start=200):
    return next(k for k in range(start, len(rows)) if rows[k][col] == "True")


def _first_flat_index(rows, start=200):
    return next(k for k in range(start, len(rows))
                if rows[k]["buy_sig"] == "False" and rows[k]["sell_sig"] == "False")


class FakeBlofin:
    """Returns the last `limit` bars of a preset OHLCV series per symbol."""
    def __init__(self, series: dict[str, list[list[float]]]):
        self.series = series
        self.calls = 0

    def fetch_recent_ohlcv(self, inst_id, *, timeframe="5m", limit=20):
        self.calls += 1
        return self.series[inst_id][-limit:]


class FakeGate:
    def __init__(self, paused: set[str]):
        self._paused = paused

    def is_paused(self, symbol: str) -> bool:
        return symbol in self._paused


def _engine(store, blofin, gate=None):
    return SignalEngine(
        store=store, blofin=blofin, symbols=["ZEC-USDT"],
        params=SignalParams(), lookback_bars=5000, timeout_minutes=30,
        gate=gate,
    )


def test_scan_queues_pending_on_buy_cross(tmp_path):
    rows = _rows()
    i = _first_index(rows, "buy_sig")
    series = _ohlcv(rows[: i + 1])
    series.append([series[-1][0] + 300_000, 999, 999, 999, 999, 0.0])  # forming bar
    store = Store(tmp_path / "t.db")
    eng = _engine(store, FakeBlofin({"ZEC-USDT": series}))

    created = eng.scan_once()

    assert len(created) == 1
    pend = store.list_pending_signals()
    assert len(pend) == 1
    assert pend[0]["action"] == "buy"
    assert pend[0]["symbol"] == "ZEC-USDT"


def test_scan_queues_sell_on_sell_cross(tmp_path):
    rows = _rows()
    i = _first_index(rows, "sell_sig")
    series = _ohlcv(rows[: i + 1])
    series.append([series[-1][0] + 300_000, 999, 999, 999, 999, 0.0])
    store = Store(tmp_path / "t.db")
    eng = _engine(store, FakeBlofin({"ZEC-USDT": series}))

    eng.scan_once()

    pend = store.list_pending_signals()
    assert len(pend) == 1 and pend[0]["action"] == "sell"


def test_drops_unfinished_candle(tmp_path):
    """The last (forming) bar must be ignored: signal comes from the last
    CLOSED bar, even if the forming bar would look different."""
    rows = _rows()
    i = _first_index(rows, "buy_sig")
    series = _ohlcv(rows[: i + 1])               # last CLOSED bar = a buy cross
    # Append a forming bar that is NOT a cross; engine must still fire buy.
    series.append([series[-1][0] + 300_000,
                   rows[i]["Close"], rows[i]["Close"],
                   rows[i]["Close"], rows[i]["Close"], 0.0])
    store = Store(tmp_path / "t.db")
    eng = _engine(store, FakeBlofin({"ZEC-USDT": series}))

    eng.scan_once()

    assert store.list_pending_signals()[0]["action"] == "buy"


def test_dedup_same_bar_fires_once(tmp_path):
    rows = _rows()
    i = _first_index(rows, "buy_sig")
    series = _ohlcv(rows[: i + 1])
    series.append([series[-1][0] + 300_000, 999, 999, 999, 999, 0.0])
    store = Store(tmp_path / "t.db")
    eng = _engine(store, FakeBlofin({"ZEC-USDT": series}))

    first = eng.scan_once()
    second = eng.scan_once()

    assert len(first) == 1 and second == []
    assert len(store.list_pending_signals()) == 1


def test_flat_bar_creates_no_signal(tmp_path):
    rows = _rows()
    i = _first_flat_index(rows)
    series = _ohlcv(rows[: i + 1])
    series.append([series[-1][0] + 300_000, 999, 999, 999, 999, 0.0])
    store = Store(tmp_path / "t.db")
    eng = _engine(store, FakeBlofin({"ZEC-USDT": series}))

    created = eng.scan_once()

    assert created == []
    assert store.list_pending_signals() == []


def test_paused_symbol_skipped(tmp_path):
    rows = _rows()
    i = _first_index(rows, "buy_sig")
    series = _ohlcv(rows[: i + 1])
    series.append([series[-1][0] + 300_000, 999, 999, 999, 999, 0.0])
    store = Store(tmp_path / "t.db")
    eng = _engine(store, FakeBlofin({"ZEC-USDT": series}),
                  gate=FakeGate({"ZEC-USDT"}))

    created = eng.scan_once()

    assert created == []
    assert store.list_pending_signals() == []


def test_min_adx_gate_suppresses_weak_trend(tmp_path):
    """With min_adx set above the bar's ADX, a cross is suppressed; with it
    below, the same cross fires. (Risk-adjusted 'quality gate', default off.)"""
    rows = _rows()
    i = _first_index(rows, "buy_sig")
    adx_at_bar = float(rows[i]["adx"])
    series = _ohlcv(rows[: i + 1])
    series.append([series[-1][0] + 300_000, 999, 999, 999, 999, 0.0])

    # Gate above the bar's ADX -> suppressed.
    store_hi = Store(tmp_path / "hi.db")
    eng_hi = SignalEngine(store=store_hi, blofin=FakeBlofin({"ZEC-USDT": series}),
                          symbols=["ZEC-USDT"], params=SignalParams(),
                          lookback_bars=5000, timeout_minutes=30,
                          min_adx=adx_at_bar + 5)
    assert eng_hi.scan_once() == []

    # Gate below the bar's ADX -> fires.
    store_lo = Store(tmp_path / "lo.db")
    eng_lo = SignalEngine(store=store_lo, blofin=FakeBlofin({"ZEC-USDT": series}),
                          symbols=["ZEC-USDT"], params=SignalParams(),
                          lookback_bars=5000, timeout_minutes=30,
                          min_adx=max(0.0, adx_at_bar - 5))
    assert len(eng_lo.scan_once()) == 1


def test_new_closed_bar_rescans(tmp_path):
    """When a new bar closes (last_ts changes), the engine scans again."""
    rows = _rows()
    i = _first_index(rows, "buy_sig")
    j = _first_index(rows, "sell_sig", start=i + 1)

    store = Store(tmp_path / "t.db")
    s1 = _ohlcv(rows[: i + 1]) + [[0, 9, 9, 9, 9, 0.0]]
    s1[-1][0] = s1[-2][0] + 300_000
    blofin = FakeBlofin({"ZEC-USDT": s1})
    eng = _engine(store, blofin)
    eng.scan_once()
    assert store.list_pending_signals()[0]["action"] == "buy"

    # New closed bars arrive up to a sell cross.
    s2 = _ohlcv(rows[: j + 1]) + [[0, 9, 9, 9, 9, 0.0]]
    s2[-1][0] = s2[-2][0] + 300_000
    blofin.series["ZEC-USDT"] = s2
    eng.scan_once()
    pend = store.list_pending_signals()
    assert len(pend) == 1 and pend[0]["action"] == "sell"  # buy was cancelled
