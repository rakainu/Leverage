"""Tests for the WebhookPayload Pydantic model — tolerant parsing of TV fields."""
import pytest
from pydantic import ValidationError

from blofin_bridge.main import WebhookPayload


def _base(**over):
    base = {"secret": "s", "symbol": "SOL-USDT", "action": "buy"}
    base.update(over)
    return base


# ----------------------- required fields -----------------------

def test_minimal_payload_accepts_only_required_fields():
    """Backward compat: the original 3 fields must still parse fine."""
    p = WebhookPayload(**_base())
    assert p.secret == "s"
    assert p.symbol == "SOL-USDT"
    assert p.action == "buy"
    # All optional fields default to None
    assert p.price is None
    assert p.high is None
    assert p.low is None
    assert p.timeframe is None
    assert p.timestamp is None


# ----------------------- tolerant numeric parsing -----------------------

def test_payload_parses_string_price_as_float():
    """TV sends numbers as strings ("100.5") — we accept and cast."""
    p = WebhookPayload(**_base(price="100.5", high="101.2", low="99.8"))
    assert p.price == 100.5
    assert p.high == 101.2
    assert p.low == 99.8


def test_payload_accepts_numeric_price():
    p = WebhookPayload(**_base(price=100.5, high=101.2, low=99.8))
    assert p.price == 100.5


def test_payload_drops_unsubstituted_placeholder_silently():
    """If TV fails to substitute a placeholder (e.g. {{close}}), we null it out
    instead of crashing — the snapshot layer will fall back to market data."""
    p = WebhookPayload(**_base(price="{{close}}", high="{{high}}", low="{{low}}"))
    assert p.price is None
    assert p.high is None
    assert p.low is None


def test_payload_drops_empty_string_fields():
    p = WebhookPayload(**_base(price="", high=""))
    assert p.price is None
    assert p.high is None


def test_payload_rejects_totally_bogus_numeric_as_none():
    """Non-numeric, non-placeholder garbage falls back to None, not crash."""
    p = WebhookPayload(**_base(price="definitely not a number"))
    assert p.price is None


# ----------------------- timeframe + timestamp -----------------------

def test_payload_keeps_timeframe_as_string():
    p = WebhookPayload(**_base(timeframe="5"))
    assert p.timeframe == "5"


def test_payload_keeps_timestamp_as_string():
    p = WebhookPayload(**_base(timestamp="2026-04-16T12:00:00Z"))
    assert p.timestamp == "2026-04-16T12:00:00Z"


def test_payload_timestamp_placeholder_nulled():
    p = WebhookPayload(**_base(timestamp="{{timenow}}"))
    assert p.timestamp is None


def test_payload_timeframe_placeholder_nulled():
    p = WebhookPayload(**_base(timeframe="{{interval}}"))
    assert p.timeframe is None


# ----------------------- action strictness -----------------------

def test_payload_rejects_unknown_action():
    with pytest.raises(ValidationError):
        WebhookPayload(**_base(action="liquidate"))


def test_payload_accepts_all_router_actions():
    for a in ("buy", "sell", "sl", "reversal_buy", "reversal_sell"):
        p = WebhookPayload(**_base(action=a))
        assert p.action == a
