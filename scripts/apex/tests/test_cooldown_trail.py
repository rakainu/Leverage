"""3 consecutive losing trail closes arm the cooldown (basket-wide, auto-resume)."""
import inspect
import types

from apex_bridge.config import CooldownConfig
from apex_bridge.main import Bridge


def _make_bridge(consec=3, minutes=60):
    b = object.__new__(Bridge)               # skip __init__ (needs a full cfg)
    b.cfg = types.SimpleNamespace(
        cooldown=CooldownConfig(enabled=True, consec_losses=consec, minutes=minutes),
        notify=types.SimpleNamespace(close=False),
    )
    b._cd_consec = 0
    b._cd_until = 0.0
    b._cd_armed = False
    return b


def test_three_losses_arm_cooldown():
    b = _make_bridge()
    assert not b._cooldown_active()
    b._register_close("sl", -12.0)
    b._register_close("sl_be", -3.0)
    assert not b._cooldown_active()          # 2 losses, not yet
    b._register_close("trail_sl", -8.0)
    assert b._cooldown_active()              # 3rd loss arms it


def test_a_win_resets_the_streak():
    b = _make_bridge()
    b._register_close("sl", -12.0)
    b._register_close("trail_sl", +25.0)     # win resets
    b._register_close("sl", -5.0)
    b._register_close("sl", -5.0)
    assert not b._cooldown_active()          # only 2 in a row after the win


def test_trail_close_path_feeds_the_breaker():
    """The trail close block in position_check_loop must call _register_close."""
    src = inspect.getsource(Bridge.position_check_loop)
    assert "_register_close(decision.reason" in src, \
        "trail close path must feed the cooldown breaker"
