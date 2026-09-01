"""
tests/api/test_competitors_router.py

Tests for GET /competitors/ and GET /competitors/history.

DB is injected via dependency override so tests run fully in-memory.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from api.axen_main import app
from api.axen_deps import get_db
from axen_database import get_connection, ingest_scrape_run, migrate


# ── Product stub ──────────────────────────────────────────────────────────────

@dataclass
class Product:
    store: str
    name: str
    price: float
    material: str
    url: str = ""
    category_url: str = ""
    context: str = ""
    delivery_info: str = ""


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db_conn():
    conn = get_connection(":memory:")
    migrate(conn)
    yield conn
    conn.close()


@pytest.fixture
def client(db_conn):
    def _override():
        yield db_conn

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c, db_conn
    app.dependency_overrides.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _product(
    material="corda",
    price=80.0,
    store="Key Design",
    url="https://keydesign.com.br/p1",
) -> Product:
    return Product(
        store=store,
        name=f"Pulseira {material} {store}",
        price=price,
        material=material,
        url=url,
        delivery_info="Frete Grátis",
    )


# ── GET /competitors/ ─────────────────────────────────────────────────────────

class TestListCompetitors:

    def test_returns_200(self, client):
        c, db = client
        assert c.get("/competitors/").status_code == 200

    def test_returns_empty_list_when_no_data(self, client):
        c, db = client
        assert c.get("/competitors/").json() == []

    def test_returns_list(self, client):
        c, db = client
        ingest_scrape_run(db, [_product()])
        assert isinstance(c.get("/competitors/").json(), list)

    def test_returns_one_product_per_scraped_item(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("corda"), _product("metal")])
        assert len(c.get("/competitors/").json()) == 2

    def test_entry_has_required_keys(self, client):
        c, db = client
        ingest_scrape_run(db, [_product()])
        entry = c.get("/competitors/").json()[0]
        for key in ("store", "name", "price", "material", "url", "delivery_info", "discount_pct", "scraped_at"):
            assert key in entry

    def test_store_correct(self, client):
        c, db = client
        ingest_scrape_run(db, [_product(store="Key Design")])
        entry = c.get("/competitors/").json()[0]
        assert entry["store"] == "Key Design"

    def test_price_is_float(self, client):
        c, db = client
        ingest_scrape_run(db, [_product(price=89.90)])
        entry = c.get("/competitors/").json()[0]
        assert isinstance(entry["price"], float)
        assert entry["price"] == pytest.approx(89.90)

    def test_material_correct(self, client):
        c, db = client
        ingest_scrape_run(db, [_product(material="couro")])
        entry = c.get("/competitors/").json()[0]
        assert entry["material"] == "couro"

    def test_url_correct(self, client):
        c, db = client
        ingest_scrape_run(db, [_product(url="https://example.com/p99")])
        entry = c.get("/competitors/").json()[0]
        assert entry["url"] == "https://example.com/p99"

    def test_filter_by_material(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("corda"), _product("metal")])
        result = c.get("/competitors/?material=corda").json()
        assert all(e["material"] == "corda" for e in result)
        assert len(result) == 1

    def test_filter_by_store(self, client):
        c, db = client
        ingest_scrape_run(db, [
            _product(store="Key Design"),
            _product(store="Beroc"),
        ])
        result = c.get("/competitors/?store=Beroc").json()
        assert all(e["store"] == "Beroc" for e in result)
        assert len(result) == 1

    def test_filter_by_material_and_store(self, client):
        c, db = client
        ingest_scrape_run(db, [
            _product("corda", store="Key Design"),
            _product("corda", store="Beroc"),
            _product("metal", store="Key Design"),
        ])
        result = c.get("/competitors/?material=corda&store=Beroc").json()
        assert len(result) == 1
        assert result[0]["material"] == "corda"
        assert result[0]["store"] == "Beroc"

    def test_only_latest_run_returned(self, client):
        """Second run replaces first — endpoint always shows latest."""
        c, db = client
        ingest_scrape_run(db, [_product("corda", price=80.0)])
        ingest_scrape_run(db, [_product("corda", price=70.0)])
        result = c.get("/competitors/").json()
        assert len(result) == 1
        assert result[0]["price"] == pytest.approx(70.0)

    def test_scraped_at_is_string(self, client):
        c, db = client
        ingest_scrape_run(db, [_product()])
        entry = c.get("/competitors/").json()[0]
        assert isinstance(entry["scraped_at"], str)


# ── GET /competitors/history ──────────────────────────────────────────────────

class TestPriceHistory:

    def test_returns_200(self, client):
        c, db = client
        assert c.get("/competitors/history").status_code == 200

    def test_returns_empty_when_no_changes(self, client):
        c, db = client
        ingest_scrape_run(db, [_product(price=80.0)])
        # Only one run → no price_changes
        assert c.get("/competitors/history").json() == []

    def test_returns_change_after_two_runs(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("corda", price=80.0)])
        ingest_scrape_run(db, [_product("corda", price=60.0)])
        result = c.get("/competitors/history").json()
        assert len(result) >= 1

    def test_entry_has_required_keys(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("corda", price=80.0)])
        ingest_scrape_run(db, [_product("corda", price=60.0)])
        entry = c.get("/competitors/history").json()[0]
        for key in ("store", "material", "price_before", "price_after", "change_pct", "changed_at"):
            assert key in entry

    def test_price_before_is_float(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("corda", price=80.0)])
        ingest_scrape_run(db, [_product("corda", price=60.0)])
        entry = c.get("/competitors/history").json()[0]
        assert isinstance(entry["price_before"], float)

    def test_price_after_is_float(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("corda", price=80.0)])
        ingest_scrape_run(db, [_product("corda", price=60.0)])
        entry = c.get("/competitors/history").json()[0]
        assert isinstance(entry["price_after"], float)

    def test_filter_by_material(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("corda", 80.0), _product("metal", 200.0)])
        ingest_scrape_run(db, [_product("corda", 60.0), _product("metal", 180.0)])
        result = c.get("/competitors/history?material=corda").json()
        assert all(e["material"] == "corda" for e in result)

    def test_days_param_accepted(self, client):
        c, db = client
        resp = c.get("/competitors/history?days=7")
        assert resp.status_code == 200

    def test_days_below_minimum_rejected(self, client):
        """days=0 is below the minimum (ge=1) — FastAPI should return 422."""
        c, db = client
        assert c.get("/competitors/history?days=0").status_code == 422

    def test_days_above_maximum_rejected(self, client):
        """days=366 is above le=365 — FastAPI should return 422."""
        c, db = client
        assert c.get("/competitors/history?days=366").status_code == 422
