"""Wallet registry loads shared wallets.json, filters active."""
from pathlib import Path

import pytest

from runner.cluster.wallet_registry import WalletRegistry

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "wallets_sample.json"


def test_loads_active_wallets_only():
    reg = WalletRegistry(FIX)
    reg.load()

    active = reg.active_addresses()
    assert "WalletA11111111111111111111111111111111111" in active
    assert "WalletB22222222222222222222222222222222222" in active
    assert "WalletC44444444444444444444444444444444444" in active
    assert "WalletInactive3333333333333333333333333333" not in active
    assert len(active) == 3


def test_get_wallet_info():
    reg = WalletRegistry(FIX)
    reg.load()

    info = reg.get("WalletA11111111111111111111111111111111111")
    assert info["source"] == "nansen"
    assert info["name"] == "smart-money-1"
    assert info["active"] is True


def test_unknown_wallet_returns_none():
    reg = WalletRegistry(FIX)
    reg.load()

    assert reg.get("unknown") is None


def test_active_count():
    reg = WalletRegistry(FIX)
    reg.load()
    assert reg.active_count() == 3


def test_missing_file_raises(tmp_path: Path):
    reg = WalletRegistry(tmp_path / "nope.json")
    with pytest.raises(FileNotFoundError):
        reg.load()


def test_reload_picks_up_changes(tmp_path: Path):
    p = tmp_path / "wallets.json"
    p.write_text(
        '{"wallets":[{"address":"A","name":"a","source":"m","tags":[],"active":true,"added_at":"2026-01-01T00:00:00Z"}]}'
    )
    reg = WalletRegistry(p)
    reg.load()
    assert reg.active_count() == 1

    p.write_text(
        '{"wallets":['
        '{"address":"A","name":"a","source":"m","tags":[],"active":true,"added_at":"2026-01-01T00:00:00Z"},'
        '{"address":"B","name":"b","source":"m","tags":[],"active":true,"added_at":"2026-01-01T00:00:00Z"}'
        ']}'
    )
    reg.load()
    assert reg.active_count() == 2
