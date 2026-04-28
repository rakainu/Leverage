"""Tests for the /api/audit-and-notify endpoint.

Covers: bearer auth, format function, no-trades-in-window edge case.
End-to-end smoke (real DB + fake telegram) is exercised against the live VPS
after deploy via curl, not here.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from dashboard.audit import (
    AuditRequest, BaselineCounts, _arrow, _format_message,
    register_audit_routes,
)


def test_arrow_directionality():
    # net_sol: post less negative than pre → ✅ (more_is_better=True)
    assert _arrow(-0.1, -1.0, more_is_better=True) == "✅"
    assert _arrow(-2.0, -1.0, more_is_better=True) == "❌"
    assert _arrow(-1.0, -1.0, more_is_better=True) == "→"


def test_format_message_no_trades():
    baseline = BaselineCounts(n=106, wins=25, net_sol=-1.043, avg_pnl_pct=-13.76, wr_pct=23.6)
    post = {
        "headline": {"n": 0, "wins": 0, "net_sol": 0.0, "avg_pnl_pct": 0.0, "wr_pct": 0.0},
        "close_reasons": [], "speed_buckets": [], "toxic_hours": {"n": 0, "wins": 0, "sum_pnl": 0.0},
        "source_presence": [], "stop_slippage": {"n": 0, "avg_close_pnl": 0.0, "worst": 0.0, "avg_hwm": 0.0},
    }
    msg = _format_message("Pre-redeploy 22-day", "2026-04-28T00:55:00", post, baseline)
    assert "No closed positions" in msg
    assert "SMC POST-REDEPLOY AUDIT" in msg


def test_format_message_with_data_renders_arrows():
    baseline = BaselineCounts(n=106, wins=25, net_sol=-1.043, avg_pnl_pct=-13.76, wr_pct=23.6)
    post = {
        "headline": {"n": 30, "wins": 12, "net_sol": 0.5, "avg_pnl_pct": 5.0, "wr_pct": 40.0},
        "close_reasons": [{"close_reason": "stop_loss", "n": 10, "avg_pnl": -28.0, "sum_pnl": -0.5}],
        "speed_buckets": [{"bucket": "c_5to15m", "n": 30, "avg_pnl": 5.0, "sum_pnl": 0.5}],
        "toxic_hours": {"n": 0, "wins": 0, "sum_pnl": 0.0},
        "source_presence": [{"source": "gmgn-apify", "appearances": 90, "in_winners": 36, "avg_pnl_when_present": 5.0}],
        "stop_slippage": {"n": 10, "avg_close_pnl": -28.0, "worst": -50.0, "avg_hwm": 0.5},
    }
    msg = _format_message("Pre-redeploy 22-day", "2026-04-28T00:55:00", post, baseline)
    assert "✅" in msg  # net SOL improved
    assert "block working" in msg
    assert "Nansen presence: 0" in msg


def test_endpoint_rejects_missing_bearer(tmp_path, monkeypatch):
    monkeypatch.setenv("SMC_DB_PATH", str(tmp_path / "x.db"))
    import db.database as dbmod
    dbmod._connection = None

    settings = Settings()
    settings.audit_token = "test-token-abc"

    from fastapi import FastAPI
    app = FastAPI()
    register_audit_routes(app, db=None, settings=settings)
    client = TestClient(app)

    resp = client.post("/api/audit-and-notify", json={
        "since_utc": "2026-04-28T00:00:00",
        "baseline_label": "x",
        "baseline": {"n": 1, "wins": 0, "net_sol": 0, "avg_pnl_pct": 0, "wr_pct": 0},
    })
    assert resp.status_code == 401
    assert "missing bearer" in resp.json()["detail"]


def test_endpoint_rejects_wrong_bearer(tmp_path, monkeypatch):
    monkeypatch.setenv("SMC_DB_PATH", str(tmp_path / "x.db"))
    import db.database as dbmod
    dbmod._connection = None

    settings = Settings()
    settings.audit_token = "test-token-abc"

    from fastapi import FastAPI
    app = FastAPI()
    register_audit_routes(app, db=None, settings=settings)
    client = TestClient(app)

    resp = client.post(
        "/api/audit-and-notify",
        headers={"Authorization": "Bearer wrong-token"},
        json={
            "since_utc": "2026-04-28T00:00:00",
            "baseline_label": "x",
            "baseline": {"n": 1, "wins": 0, "net_sol": 0, "avg_pnl_pct": 0, "wr_pct": 0},
        },
    )
    assert resp.status_code == 401
    assert "invalid bearer" in resp.json()["detail"]


def test_endpoint_503_when_audit_token_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("SMC_DB_PATH", str(tmp_path / "x.db"))
    import db.database as dbmod
    dbmod._connection = None

    settings = Settings()
    settings.audit_token = ""

    from fastapi import FastAPI
    app = FastAPI()
    register_audit_routes(app, db=None, settings=settings)
    client = TestClient(app)

    resp = client.post(
        "/api/audit-and-notify",
        headers={"Authorization": "Bearer anything"},
        json={
            "since_utc": "2026-04-28T00:00:00",
            "baseline_label": "x",
            "baseline": {"n": 1, "wins": 0, "net_sol": 0, "avg_pnl_pct": 0, "wr_pct": 0},
        },
    )
    assert resp.status_code == 503
