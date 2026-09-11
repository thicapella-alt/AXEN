"""
tests/api/test_prices_router.py

Tests for GET /prices/ and GET /prices/{material}.

DB is injected via dependency override so tests run fully in-memory.
Products are inserted with ingest_scrape_run() before each test.
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

def _product(material="corda", price=80.0, store="Key Design") -> Product:
    return Product(
        store=store,
        name=f"Pulseira {material} {store}",
        price=price,
        material=material,
        url=f"https://{store.lower().replace(' ', '')}.com/p1",
    )


# ── GET /prices/ ──────────────────────────────────────────────────────────────

class TestListPrices:

    def test_returns_200(self, client):
        c, db = client
        assert c.get("/prices/").status_code == 200

    def test_returns_empty_list_when_no_data(self, client):
        c, db = client
        assert c.get("/prices/").json() == []

    def test_returns_list(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("corda", 80.0)])
        result = c.get("/prices/").json()
        assert isinstance(result, list)

    def test_one_entry_per_material(self, client):
        c, db = client
        ingest_scrape_run(db, [
            _product("corda", 80.0),
            _product("metal", 140.0),
        ])
        result = c.get("/prices/").json()
        assert len(result) == 2

    def test_entry_has_required_keys(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("corda", 80.0)])
        entry = c.get("/prices/").json()[0]
        for key in ("material", "market_min", "axen_ref", "gap_pct"):
            assert key in entry

    def test_material_correct(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("corda", 80.0)])
        entry = c.get("/prices/").json()[0]
        assert entry["material"] == "corda"

    def test_market_min_is_cheapest(self, client):
        c, db = client
        ingest_scrape_run(db, [
            _product("corda", 80.0, "Key Design"),
            _product("corda", 60.0, "Beroc"),
            _product("corda", 90.0, "Other"),
        ])
        entry = c.get("/prices/").json()[0]
        assert entry["market_min"] == pytest.approx(60.0)

    def test_axen_ref_matches_known_price(self, client):
        """AXEN_PRICES["corda"] == 125.0."""
        c, db = client
        ingest_scrape_run(db, [_product("corda", 80.0)])
        entry = c.get("/prices/").json()[0]
        assert entry["axen_ref"] == pytest.approx(125.0)

    def test_gap_pct_correct(self, client):
        """gap = (125 - 80) / 80 * 100 = 56.25."""
        c, db = client
        ingest_scrape_run(db, [_product("corda", 80.0)])
        entry = c.get("/prices/").json()[0]
        assert entry["gap_pct"] == pytest.approx(56.25, rel=0.01)

    def test_axen_ref_none_for_unknown_material(self, client):
        """Materials not in AXEN_PRICES → axen_ref=None, gap_pct=None."""
        c, db = client
        ingest_scrape_run(db, [_product("madeira", 30.0)])
        entry = c.get("/prices/").json()[0]
        assert entry["axen_ref"] is None
        assert entry["gap_pct"] is None

    def test_sorted_by_material(self, client):
        c, db = client
        ingest_scrape_run(db, [
            _product("metal", 140.0),
            _product("corda", 80.0),
            _product("couro", 150.0),
        ])
        materials = [e["material"] for e in c.get("/prices/").json()]
        assert materials == sorted(materials)

    def test_multiple_products_same_material_deduped(self, client):
        c, db = client
        ingest_scrape_run(db, [
            _product("corda", 80.0, "Key Design"),
            _product("corda", 70.0, "Beroc"),
        ])
        result = c.get("/prices/").json()
        # Only one entry for "corda"
        corda_entries = [e for e in result if e["material"] == "corda"]
        assert len(corda_entries) == 1


# ── GET /prices/{material} ────────────────────────────────────────────────────

class TestGetPriceByMaterial:

    def test_returns_200_when_data_exists(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("corda", 80.0)])
        assert c.get("/prices/corda").status_code == 200

    def test_returns_404_when_no_data(self, client):
        c, db = client
        assert c.get("/prices/corda").status_code == 404

    def test_returns_single_dict_not_list(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("corda", 80.0)])
        result = c.get("/prices/corda").json()
        assert isinstance(result, dict)

    def test_material_matches_path_param(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("metal", 140.0)])
        entry = c.get("/prices/metal").json()
        assert entry["material"] == "metal"

    def test_market_min_correct_for_material(self, client):
        c, db = client
        ingest_scrape_run(db, [_product("couro", 150.0)])
        entry = c.get("/prices/couro").json()
        assert entry["market_min"] == pytest.approx(150.0)

    def test_axen_ref_correct_for_metal(self, client):
        """AXEN_PRICES["metal"] == 215.0."""
        c, db = client
        ingest_scrape_run(db, [_product("metal", 140.0)])
        entry = c.get("/prices/metal").json()
        assert entry["axen_ref"] == pytest.approx(215.0)

    def test_404_detail_mentions_material(self, client):
        c, db = client
        resp = c.get("/prices/doesnotexist")
        assert "doesnotexist" in resp.json()["detail"]

    def test_other_material_not_affected(self, client):
        """Inserting corda data should not populate metal."""
        c, db = client
        ingest_scrape_run(db, [_product("corda", 80.0)])
        assert c.get("/prices/metal").status_code == 404
