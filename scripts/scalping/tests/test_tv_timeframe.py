"""Tests for normalize_tv_timeframe — maps TradingView's {{interval}} values
to ccxt timeframe strings that BloFin accepts.

TradingView placeholder reference (per TV docs):
  minutes <60 → bare digits      "1","3","5","15","30","45"
  hours          → minutes*N      "60","120","180","240"
  daily          → "D" or "1D"
  weekly         → "W" or "1W"
  monthly        → "M" or "1M"
  seconds        → "S5","S15",... (we don't trade these, but must not crash)

ccxt / BloFin accept "1m","3m","5m","15m","30m","1h","2h","4h","1d","1w","1M".
"""
import pytest

from blofin_bridge.main import normalize_tv_timeframe


# ---------- minute bars (bare digits < 60) ----------

@pytest.mark.parametrize("raw,expected", [
    ("1", "1m"),
    ("3", "3m"),
    ("5", "5m"),
    ("15", "15m"),
    ("30", "30m"),
    ("45", "45m"),
])
def test_bare_minute_digits_normalized_to_m(raw, expected):
    assert normalize_tv_timeframe(raw) == expected


# ---------- hour bars (minutes-as-multiple-of-60) ----------

@pytest.mark.parametrize("raw,expected", [
    ("60", "1h"),
    ("120", "2h"),
    ("180", "3h"),
    ("240", "4h"),
])
def test_hour_minute_multiples_normalized_to_h(raw, expected):
    assert normalize_tv_timeframe(raw) == expected


# ---------- daily / weekly / monthly ----------

@pytest.mark.parametrize("raw,expected", [
    ("D", "1d"),
    ("1D", "1d"),
    ("W", "1w"),
    ("1W", "1w"),
    ("M", "1M"),
    ("1M", "1M"),
])
def test_daily_weekly_monthly_codes(raw, expected):
    assert normalize_tv_timeframe(raw) == expected


# ---------- already-ccxt pass-through ----------

@pytest.mark.parametrize("value", ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"])
def test_already_ccxt_format_passes_through(value):
    assert normalize_tv_timeframe(value) == value


# ---------- null / empty / junk ----------

def test_none_returns_none():
    assert normalize_tv_timeframe(None) is None


def test_empty_string_returns_none():
    assert normalize_tv_timeframe("") is None
    assert normalize_tv_timeframe("   ") is None


def test_unsubstituted_placeholder_returns_none():
    assert normalize_tv_timeframe("{{interval}}") is None


def test_unrecognized_junk_returns_none():
    assert normalize_tv_timeframe("banana") is None
    assert normalize_tv_timeframe("5min") is None  # not ccxt shape


# ---------- case + whitespace tolerance ----------

def test_whitespace_trimmed():
    assert normalize_tv_timeframe("  5  ") == "5m"


def test_lowercase_daily_weekly():
    # TV sends upper but users paste lower — tolerate
    assert normalize_tv_timeframe("d") == "1d"
    assert normalize_tv_timeframe("w") == "1w"


# ---------- seconds shouldn't crash (we don't support, just null) ----------

def test_seconds_not_supported_returns_none():
    assert normalize_tv_timeframe("S5") is None
    assert normalize_tv_timeframe("S30") is None
