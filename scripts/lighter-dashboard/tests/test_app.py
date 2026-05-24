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
    assert "Lighter" in r.text
    assert "hx-get" in r.text          # HTMX wiring present


def test_kpis_partial(fixture_db):
    app = create_app(_cfg(fixture_db), marks=_StubMarks())
    client = TestClient(app)
    r = client.get("/panel/kpis")
    assert r.status_code == 200
    assert "Equity" in r.text


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
