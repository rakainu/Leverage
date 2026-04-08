import os
from pathlib import Path

import pytest
import yaml

from blofin_bridge.config import Settings, load_config, SymbolConfig


def test_load_config_from_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": {
            "margin_usdt": 50, "leverage": 5, "margin_mode": "isolated",
            "position_mode": "net", "safety_sl_pct": 0.04,
            "tp_split": [0.5, 0.3, 0.2], "sl_policy": "p2_step_stop",
        },
        "symbols": {
            "SOL-USDT": {"enabled": True, "margin_usdt": 50, "leverage": 5,
                         "margin_mode": "isolated", "sl_policy": "p2_step_stop"},
        },
    }))
    monkeypatch.setenv("BLOFIN_DEMO_API_KEY", "demo-k")
    monkeypatch.setenv("BLOFIN_DEMO_API_SECRET", "demo-s")
    monkeypatch.setenv("BLOFIN_DEMO_PASSPHRASE", "demo-p")
    monkeypatch.setenv("BLOFIN_LIVE_API_KEY", "live-k")
    monkeypatch.setenv("BLOFIN_LIVE_API_SECRET", "live-s")
    monkeypatch.setenv("BLOFIN_LIVE_PASSPHRASE", "live-p")
    monkeypatch.setenv("BRIDGE_SECRET", "x" * 20)
    monkeypatch.setenv("BLOFIN_ENV", "demo")

    cfg = load_config(yaml_path)

    assert cfg.blofin.env == "demo"
    assert cfg.blofin.api_key == "demo-k"        # demo keys selected
    assert cfg.defaults.margin_usdt == 50
    assert cfg.defaults.tp_split == [0.5, 0.3, 0.2]
    assert "SOL-USDT" in cfg.symbols
    assert cfg.symbols["SOL-USDT"].enabled is True


def test_missing_required_env_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text("defaults: {}\nsymbols: {}\n")
    # No env vars set — clear all bridge-relevant ones
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
        "defaults": {
            "margin_usdt": 100, "leverage": 10, "margin_mode": "isolated",
            "position_mode": "net", "safety_sl_pct": 0.05,
            "tp_split": [0.4, 0.3, 0.3], "sl_policy": "p2_step_stop",
        },
        "symbols": {},
    }))
    monkeypatch.setenv("BLOFIN_ENV", "live")
    # Only demo keys present; live are empty
    monkeypatch.setenv("BLOFIN_DEMO_API_KEY", "d")
    monkeypatch.setenv("BLOFIN_DEMO_API_SECRET", "d")
    monkeypatch.setenv("BLOFIN_DEMO_PASSPHRASE", "d")
    for k in ("BLOFIN_LIVE_API_KEY", "BLOFIN_LIVE_API_SECRET", "BLOFIN_LIVE_PASSPHRASE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BRIDGE_SECRET", "x" * 20)
    with pytest.raises(ValueError, match="BLOFIN_ENV=live requires"):
        load_config(yaml_path)


def test_tp_split_must_sum_to_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "defaults": {
            "margin_usdt": 100, "leverage": 10, "margin_mode": "isolated",
            "position_mode": "net", "safety_sl_pct": 0.05,
            "tp_split": [0.5, 0.3, 0.3],          # sums to 1.1 - invalid
            "sl_policy": "p2_step_stop",
        },
        "symbols": {},
    }))
    for k, v in [
        ("BLOFIN_DEMO_API_KEY", "k"), ("BLOFIN_DEMO_API_SECRET", "s"),
        ("BLOFIN_DEMO_PASSPHRASE", "p"), ("BRIDGE_SECRET", "x" * 20),
        ("BLOFIN_ENV", "demo"),
    ]:
        monkeypatch.setenv(k, v)

    with pytest.raises(ValueError, match="tp_split must sum to 1.0"):
        load_config(yaml_path)
