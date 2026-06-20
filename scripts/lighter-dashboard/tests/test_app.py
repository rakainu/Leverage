from fastapi.testclient import TestClient

from lighter_dashboard.app import create_app
from lighter_dashboard.config import DashboardConfig


def _cfg(db_path):
    return DashboardConfig(
        db_path=db_path, lighter_host="x", initial_collateral_usdc=2000,
        symbols={"ZEC": 90, "SOL": 2}, live_ms=3000, static_ms=15000,
        mark_cache_ttl_s=2.0,
    )


class _StubMarks:
    async def get_mid(self, name):
        return 90.0
    async def close(self):
        pass


def test_index_renders(fixture_db):
    app = create_app(_cfg(fixture_db), marks=_StubMarks())
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Edge health" in r.text     # the new edge-tracking hero
    assert "/api/state" in r.text      # client polls the state endpoint


def test_api_state(fixture_db):
    app = create_app(_cfg(fixture_db), marks=_StubMarks())
    client = TestClient(app)
    r = client.get("/api/state")
    assert r.status_code == 200
    s = r.json()
    # the new state carries the edge-health + side-health + protections blocks
    for key in ("edge", "stat", "sides", "protections", "per_coin", "fillq"):
        assert key in s
    assert s["edge"]["wr_bt"] == 88.0
    assert isinstance(s["sides"], list)


def test_kpis_partial(fixture_db):
    app = create_app(_cfg(fixture_db), marks=_StubMarks())
    client = TestClient(app)
    r = client.get("/panel/kpis")
    assert r.status_code == 200
    assert "Equity" in r.text


def test_kpis_window_param_persists(fixture_db):
    app = create_app(_cfg(fixture_db), marks=_StubMarks())
    client = TestClient(app)
    r = client.get("/panel/kpis?window=week")
    assert r.status_code == 200
    # self-poll URL carries the chosen window forward, and the W button is active
    assert "/panel/kpis?window=week" in r.text


def test_kpis_bad_window_falls_back_to_day(fixture_db):
    app = create_app(_cfg(fixture_db), marks=_StubMarks())
    client = TestClient(app)
    r = client.get("/panel/kpis?window=bogus")
    assert r.status_code == 200
    assert "/panel/kpis?window=day" in r.text


def test_positions_partial_shows_open(fixture_db):
    app = create_app(_cfg(fixture_db), marks=_StubMarks())
    client = TestClient(app)
    r = client.get("/panel/positions")
    assert r.status_code == 200
    assert "SOL" in r.text             # the one open trade


import pytest


@pytest.mark.parametrize("path", [
    "/panel/closed", "/panel/exits", "/panel/symbols",
    "/panel/signals", "/panel/equity",
])
def test_all_panels_render(fixture_db, path):
    app = create_app(_cfg(fixture_db), marks=_StubMarks())
    client = TestClient(app)
    r = client.get(path)
    assert r.status_code == 200
