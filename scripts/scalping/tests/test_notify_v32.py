"""V3.2 'Fin' Telegram notifications — minimal + branded 'V3'.

Rich's spec: signed from V3, less info — only entry (with SL) on open, and exit
with ±P&L on close. No signal/pending/trail/breakeven chatter.
"""
import importlib.util
from pathlib import Path

import pytest

_path = Path(__file__).parents[1] / "v3.2-deploy" / "notify.py"
_spec = importlib.util.spec_from_file_location("v32_notify", _path)
notify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notify)


def test_entry_is_slim_branded_v3():
    msg = notify.format_entry({
        "side": "long", "symbol": "ZEC-USDT",
        "entry_price": 505.28, "sl_trigger": 499.72, "sl_loss_usdt": 82.5,
    })
    assert "V3" in msg
    assert "ZEC" in msg and "LONG" in msg
    assert "505.28" in msg          # entry
    assert "499.72" in msg          # SL
    # slim: no TP-ceiling, trail, or signal noise
    assert "TP Ceiling" not in msg
    assert "Trail" not in msg and "trail" not in msg
    assert "Signal" not in msg


def test_exit_win_shows_plus_pnl_branded():
    msg = notify.format_exit("ZEC-USDT", "long", 44.2, "trail_sl")
    assert "V3" in msg and "ZEC" in msg
    assert "+$44.20" in msg


def test_exit_loss_shows_minus_pnl():
    msg = notify.format_exit("SOL-USDT", "short", -82.5, "sl")
    assert "V3" in msg and "SOL" in msg
    assert "82.50" in msg
    assert ("-$" in msg) or ("−$" in msg)


def test_exit_is_minimal_no_chatter():
    msg = notify.format_exit("BTC-USDT", "long", 12.0, "trail_sl")
    assert "Signal" not in msg and "TP Ceiling" not in msg
    assert msg.count("\n") <= 1   # one-liner-ish, "less info"
