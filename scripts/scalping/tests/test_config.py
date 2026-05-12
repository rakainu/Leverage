import pytest
import yaml

from blofin_bridge.config import load_config


def _base_defaults(**overrides):
    d = {
        "margin_usdt": 100, "leverage": 30, "margin_mode": "isolated",
        "position_mode": "net", "sl_policy": "p2_step_stop",
        "sl_loss_usdt": 15, "breakeven_usdt": 15,
        "trail_activate_usdt": 25, "trail_start_usdt": 30,
        "trail_distance_usdt": 10, "tp_limit_margin_pct": 2.0,
        "poll_interval_seconds": 10,
    }
    d.update(overrides)
    return d


def _set_env(monkeypatch):
    monkeypatch.setenv("BLOFIN_DEMO_API_KEY", "demo-k")
    monkeypatch.setenv("BLOFIN_DEMO_API_SECRET", "demo-s")
    monkeypatch.setenv("BLOFIN_DEMO_PASSPHRASE", "demo-p")
    monkeypatch.setenv("BLOFIN_LIVE_API_KEY", "live-k")
    monkeypatch.setenv("BLOFIN_LIVE_API_SECRET", "live-s")
    monkeypatch.setenv("BLOFIN_LIVE_PASSPHRASE", "live-p")
    monkeypatch.setenv("BRIDGE_SECRET", "x" * 20)
    monkeypatch.setenv("BLOFIN_ENV", "demo")


def test_load_config_from_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": _base_defaults(),
        "symbols": {
            "SOL-USDT": {"enabled": True, "margin_usdt": 100, "leverage": 30,
                         "margin_mode": "isolated", "sl_policy": "p2_step_stop"},
        },
    }))
    _set_env(monkeypatch)

    cfg = load_config(yaml_path)
    assert cfg.blofin.env == "demo"
    assert cfg.defaults.sl_loss_usdt == 15
    assert cfg.defaults.trail_activate_usdt == 25
    assert cfg.defaults.trail_start_usdt == 30
    assert cfg.defaults.trail_distance_usdt == 10
    assert cfg.defaults.tp_limit_margin_pct == 2.0
    assert cfg.defaults.leverage == 30
    assert "SOL-USDT" in cfg.symbols


def test_missing_required_env_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text("defaults: {}\nsymbols: {}\n")
    for k in (
        "BLOFIN_DEMO_API_KEY", "BLOFIN_DEMO_API_SECRET", "BLOFIN_DEMO_PASSPHRASE",
        "BLOFIN_LIVE_API_KEY", "BLOFIN_LIVE_API_SECRET", "BLOFIN_LIVE_PASSPHRASE",
        "BRIDGE_SECRET", "BLOFIN_ENV",
    ):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(Exception):
        load_config(yaml_path)


def test_live_env_with_missing_live_keys_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": _base_defaults(),
        "symbols": {},
    }))
    monkeypatch.setenv("BLOFIN_ENV", "live")
    monkeypatch.setenv("BLOFIN_DEMO_API_KEY", "d")
    monkeypatch.setenv("BLOFIN_DEMO_API_SECRET", "d")
    monkeypatch.setenv("BLOFIN_DEMO_PASSPHRASE", "d")
    for k in ("BLOFIN_LIVE_API_KEY", "BLOFIN_LIVE_API_SECRET", "BLOFIN_LIVE_PASSPHRASE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BRIDGE_SECRET", "x" * 20)
    with pytest.raises(ValueError, match="BLOFIN_ENV=live requires"):
        load_config(yaml_path)


def test_sl_loss_usdt_must_be_positive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": _base_defaults(sl_loss_usdt=0),
        "symbols": {},
    }))
    _set_env(monkeypatch)
    with pytest.raises(Exception):
        load_config(yaml_path)


def test_trail_activate_must_be_positive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": _base_defaults(trail_activate_usdt=-5),
        "symbols": {},
    }))
    _set_env(monkeypatch)
    with pytest.raises(Exception):
        load_config(yaml_path)


def test_tp_limit_must_be_positive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": _base_defaults(tp_limit_margin_pct=0),
        "symbols": {},
    }))
    _set_env(monkeypatch)
    with pytest.raises(Exception):
        load_config(yaml_path)


def test_poll_interval_must_be_at_least_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": _base_defaults(poll_interval_seconds=0),
        "symbols": {},
    }))
    _set_env(monkeypatch)
    with pytest.raises(Exception):
        load_config(yaml_path)


# ---------- Per-symbol scaling ----------


def _v3_defaults():
    """V3-style defaults at $100 baseline for scaling tests."""
    return _base_defaults(
        sl_loss_usdt=13,
        breakeven_usdt=12,
        lock_profit_activate_usdt=18,
        lock_profit_usdt=15,
        trail_activate_usdt=30,
        trail_start_usdt=32,
        trail_distance_usdt=15,
        tp_limit_margin_pct=2.0,
    )


def test_symbol_at_baseline_margin_keeps_default_thresholds(tmp_path, monkeypatch):
    """A symbol with the same margin as defaults gets defaults verbatim."""
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": _v3_defaults(),
        "symbols": {
            "FOO-USDT": {
                "enabled": True, "margin_usdt": 100, "leverage": 30,
                "margin_mode": "isolated", "sl_policy": "p2_step_stop",
            },
        },
    }))
    _set_env(monkeypatch)

    cfg = load_config(yaml_path)
    foo = cfg.symbols["FOO-USDT"]
    assert foo.margin_usdt == 100
    assert foo.sl_loss_usdt == 13
    assert foo.breakeven_usdt == 12
    assert foo.lock_profit_activate_usdt == 18
    assert foo.lock_profit_usdt == 15
    assert foo.trail_activate_usdt == 30
    assert foo.trail_start_usdt == 32
    assert foo.trail_distance_usdt == 15


def test_symbol_with_2_5x_margin_scales_all_thresholds(tmp_path, monkeypatch):
    """ZEC at $250 margin should get every $-threshold × 2.5."""
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": _v3_defaults(),
        "symbols": {
            "ZEC-USDT": {
                "enabled": True, "margin_usdt": 250, "leverage": 30,
                "margin_mode": "isolated", "sl_policy": "p2_step_stop",
            },
        },
    }))
    _set_env(monkeypatch)

    cfg = load_config(yaml_path)
    zec = cfg.symbols["ZEC-USDT"]
    assert zec.margin_usdt == 250
    assert zec.sl_loss_usdt == pytest.approx(32.5)         # 13 × 2.5
    assert zec.breakeven_usdt == pytest.approx(30.0)       # 12 × 2.5
    assert zec.lock_profit_activate_usdt == pytest.approx(45.0)  # 18 × 2.5
    assert zec.lock_profit_usdt == pytest.approx(37.5)     # 15 × 2.5
    assert zec.trail_activate_usdt == pytest.approx(75.0)  # 30 × 2.5
    assert zec.trail_start_usdt == pytest.approx(80.0)     # 32 × 2.5
    assert zec.trail_distance_usdt == pytest.approx(37.5)  # 15 × 2.5


def test_symbol_with_0_3x_margin_scales_all_thresholds(tmp_path, monkeypatch):
    """SOL at $30 margin should get every $-threshold × 0.3."""
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": _v3_defaults(),
        "symbols": {
            "SOL-USDT": {
                "enabled": True, "margin_usdt": 30, "leverage": 30,
                "margin_mode": "isolated", "sl_policy": "p2_step_stop",
            },
        },
    }))
    _set_env(monkeypatch)

    cfg = load_config(yaml_path)
    sol = cfg.symbols["SOL-USDT"]
    assert sol.margin_usdt == 30
    assert sol.sl_loss_usdt == pytest.approx(3.9)          # 13 × 0.3
    assert sol.breakeven_usdt == pytest.approx(3.6)        # 12 × 0.3
    assert sol.lock_profit_activate_usdt == pytest.approx(5.4)
    assert sol.trail_activate_usdt == pytest.approx(9.0)
    assert sol.trail_distance_usdt == pytest.approx(4.5)


def test_explicit_per_symbol_override_beats_scaling(tmp_path, monkeypatch):
    """An explicit per-symbol threshold wins over the scaled value."""
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": _v3_defaults(),
        "symbols": {
            "BTC-USDT": {
                "enabled": True, "margin_usdt": 200, "leverage": 30,
                "margin_mode": "isolated", "sl_policy": "p2_step_stop",
                # Override: BTC needs a tighter SL than the 2× scaled value.
                "sl_loss_usdt": 15,
            },
        },
    }))
    _set_env(monkeypatch)

    cfg = load_config(yaml_path)
    btc = cfg.symbols["BTC-USDT"]
    # Explicit override wins
    assert btc.sl_loss_usdt == 15
    # Everything else still scales at 2× margin
    assert btc.breakeven_usdt == pytest.approx(24.0)        # 12 × 2
    assert btc.trail_distance_usdt == pytest.approx(30.0)   # 15 × 2


def test_multi_symbol_scaling_independent(tmp_path, monkeypatch):
    """Multiple symbols scale independently from the same defaults."""
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": _v3_defaults(),
        "symbols": {
            "ZEC-USDT": {
                "enabled": True, "margin_usdt": 250, "leverage": 30,
                "margin_mode": "isolated", "sl_policy": "p2_step_stop",
            },
            "SOL-USDT": {
                "enabled": True, "margin_usdt": 30, "leverage": 30,
                "margin_mode": "isolated", "sl_policy": "p2_step_stop",
            },
        },
    }))
    _set_env(monkeypatch)

    cfg = load_config(yaml_path)
    assert cfg.symbols["ZEC-USDT"].sl_loss_usdt == pytest.approx(32.5)
    assert cfg.symbols["SOL-USDT"].sl_loss_usdt == pytest.approx(3.9)
    # Defaults themselves unchanged.
    assert cfg.defaults.sl_loss_usdt == 13
