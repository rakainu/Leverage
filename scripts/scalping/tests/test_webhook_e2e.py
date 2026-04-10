from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(tmp_path, monkeypatch):
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(
        "defaults:\n"
        "  margin_usdt: 100\n  leverage: 30\n  margin_mode: isolated\n"
        "  position_mode: net\n  sl_policy: p2_step_stop\n"
        "  sl_loss_usdt: 15\n  breakeven_usdt: 15\n"
        "  trail_activate_usdt: 25\n  trail_start_usdt: 30\n"
        "  trail_distance_usdt: 10\n  tp_limit_margin_pct: 2.0\n"
        "symbols:\n"
        "  SOL-USDT:\n"
        "    enabled: true\n    margin_usdt: 100\n    leverage: 30\n"
        "    margin_mode: isolated\n    sl_policy: p2_step_stop\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BLOFIN_DEMO_API_KEY", "k")
    monkeypatch.setenv("BLOFIN_DEMO_API_SECRET", "s")
    monkeypatch.setenv("BLOFIN_DEMO_PASSPHRASE", "p")
    monkeypatch.setenv("BRIDGE_SECRET", "topsecret" * 3)
    monkeypatch.setenv("BLOFIN_ENV", "demo")
    monkeypatch.setenv("BLOFIN_BRIDGE_CONFIG", str(yaml_path))
    monkeypatch.setenv("BLOFIN_BRIDGE_DB", str(tmp_path / "bridge.db"))

    from blofin_bridge import main as main_mod
    mock_blofin = MagicMock()
    mock_blofin.get_instrument.return_value = {
        "instId": "SOL-USDT", "contractValue": 1.0, "minSize": 1.0,
        "lotSize": 1.0, "tickSize": 0.001,
    }
    mock_blofin.fetch_last_price.return_value = 80.0
    mock_blofin.place_market_entry.return_value = {
        "orderId": "ord-1", "fill_price": 80.12, "filled": 12,
    }
    mock_blofin.place_limit_reduce_only.return_value = "tp-ceiling-id"
    mock_blofin.fetch_positions.return_value = []
    monkeypatch.setattr(main_mod, "_build_blofin_client", lambda _: mock_blofin)
    return main_mod.create_app()


def test_webhook_rejects_wrong_secret(app):
    client = TestClient(app)
    r = client.post("/webhook/pro-v3", json={
        "secret": "wrong", "symbol": "SOL-USDT", "action": "buy",
    })
    assert r.status_code == 401


def test_webhook_buy_opens_position(app):
    client = TestClient(app)
    r = client.post("/webhook/pro-v3", json={
        "secret": "topsecret" * 3, "symbol": "SOL-USDT", "action": "buy",
        "source": "pro_v3",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is True


def test_webhook_unknown_action_400(app):
    client = TestClient(app)
    r = client.post("/webhook/pro-v3", json={
        "secret": "topsecret" * 3, "symbol": "SOL-USDT", "action": "tp1",
    })
    assert r.status_code == 422 or r.status_code == 400


def test_health_endpoint(app):
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_status_rejects_no_secret(app):
    client = TestClient(app)
    r = client.get("/status")
    assert r.status_code == 401


def test_status_returns_state(app):
    client = TestClient(app)
    r = client.get("/status", params={"secret": "topsecret" * 3})
    assert r.status_code == 200
    body = r.json()
    assert "open_positions" in body
    assert "recent_events" in body
