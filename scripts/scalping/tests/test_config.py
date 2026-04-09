import pytest
import yaml

from blofin_bridge.config import load_config


def _base_defaults(**overrides):
    d = {
        "margin_usdt": 100, "leverage": 30, "margin_mode": "isolated",
        "position_mode": "net", "sl_policy": "p2_step_stop",
        "sl_loss_usdt": 20, "trail_activate_usdt": 30,
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
    assert cfg.defaults.sl_loss_usdt == 20
    assert cfg.defaults.trail_activate_usdt == 30
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
