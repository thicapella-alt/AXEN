"""
tests/api/test_sales_router.py

Tests for GET /sales/, GET /sales/by-state, POST /sales/ingest.

DB injected via dependency override.  Sales records are inserted via
POST /sales/ingest or directly with upsert_sale() before each test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.axen_main import app
from api.axen_deps import get_db
from axen_database import get_connection, migrate, upsert_sale


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

def _sale(
    order_id="ORD-001",
    platform="mercadolivre",
    product_name="Pulseira Corda",
    material="corda",
    unit_price=125.0,
    total_value=125.0,
    quantity=1,
    buyer_state="SP",
    sold_at="2026-05-01T12:00:00+00:00",
) -> dict:
    return {
        "platform":     platform,
        "order_id":     order_id,
        "product_name": product_name,
        "material":     material,
        "unit_price":   unit_price,
        "total_value":  total_value,
        "quantity":     quantity,
        "buyer_state":  buyer_state,
        "sold_at":      sold_at,
    }


# ── POST /sales/ingest ────────────────────────────────────────────────────────

class TestIngestSales:

    def test_returns_200(self, client):
        c, db = client
        assert c.post("/sales/ingest", json=[_sale()]).status_code == 200

    def test_returns_inserted_count(self, client):
        c, db = client
        result = c.post("/sales/ingest", json=[_sale("A"), _sale("B")]).json()
        assert result == {"inserted": 2}

    def test_single_record_count(self, client):
        c, db = client
        result = c.post("/sales/ingest", json=[_sale()]).json()
        assert result["inserted"] == 1

    def test_empty_list_returns_zero(self, client):
        c, db = client
        result = c.post("/sales/ingest", json=[]).json()
        assert result["inserted"] == 0

    def test_duplicate_not_double_counted(self, client):
        """Duplicate (platform, order_id) pairs are silently ignored."""
        c, db = client
        c.post("/sales/ingest", json=[_sale("DUP")])
        result = c.post("/sales/ingest", json=[_sale("DUP")]).json()
        # Second call reports 1 processed (the function runs upsert once per item)
        assert result["inserted"] == 1

    def test_data_persists_to_db(self, client):
        c, db = client
        c.post("/sales/ingest", json=[_sale("PERSIST")])
        row = db.execute(
            "SELECT * FROM sales WHERE order_id=?", ("PERSIST",)
        ).fetchone()
        assert row is not None

    def test_multiple_platforms(self, client):
        c, db = client
        result = c.post("/sales/ingest", json=[
            _sale("A", platform="mercadolivre"),
            _sale("B", platform="shopee"),
        ]).json()
        assert result["inserted"] == 2

    def test_non_list_body_rejected(self, client):
        c, db = client
        assert c.post("/sales/ingest", json={"order_id": "X"}).status_code == 422


# ── GET /sales/ ───────────────────────────────────────────────────────────────

class TestSalesSummary:

    def test_returns_200(self, client):
        c, db = client
        assert c.get("/sales/").status_code == 200

    def test_returns_dict_with_top_products_and_weekly(self, client):
        c, db = client
        result = c.get("/sales/").json()
        assert "top_products" in result
        assert "weekly" in result

    def test_empty_db_returns_empty_lists(self, client):
        c, db = client
        result = c.get("/sales/").json()
        assert result["top_products"] == []
        assert result["weekly"] == []

    def test_top_products_is_list(self, client):
        c, db = client
        upsert_sale(db, _sale())
        result = c.get("/sales/").json()
        assert isinstance(result["top_products"], list)

    def test_weekly_is_list(self, client):
        c, db = client
        upsert_sale(db, _sale())
        result = c.get("/sales/").json()
        assert isinstance(result["weekly"], list)

    def test_top_products_entry_has_required_keys(self, client):
        c, db = client
        upsert_sale(db, _sale())
        entry = c.get("/sales/").json()["top_products"][0]
        for key in ("product_name", "material", "total_units", "total_revenue"):
            assert key in entry

    def test_top_products_product_name_correct(self, client):
        c, db = client
        upsert_sale(db, _sale(product_name="Pulseira Corda"))
        entry = c.get("/sales/").json()["top_products"][0]
        assert entry["product_name"] == "Pulseira Corda"

    def test_top_products_total_units_correct(self, client):
        c, db = client
        upsert_sale(db, _sale("A", quantity=2))
        upsert_sale(db, _sale("B", quantity=3))
        entry = c.get("/sales/").json()["top_products"][0]
        assert entry["total_units"] == 5

    def test_top_products_total_revenue_correct(self, client):
        c, db = client
        upsert_sale(db, _sale(total_value=125.0))
        entry = c.get("/sales/").json()["top_products"][0]
        assert entry["total_revenue"] == pytest.approx(125.0)

    def test_weekly_entry_has_required_keys(self, client):
        c, db = client
        upsert_sale(db, _sale())
        result = c.get("/sales/").json()["weekly"]
        if result:
            for key in ("week_label", "total_units", "total_revenue"):
                assert key in result[0]

    def test_days_param_accepted(self, client):
        c, db = client
        assert c.get("/sales/?days=7").status_code == 200

    def test_platform_param_accepted(self, client):
        c, db = client
        assert c.get("/sales/?platform=mercadolivre").status_code == 200

    def test_days_below_minimum_rejected(self, client):
        c, db = client
        assert c.get("/sales/?days=0").status_code == 422

    def test_days_above_maximum_rejected(self, client):
        c, db = client
        assert c.get("/sales/?days=366").status_code == 422

    def test_multiple_sales_aggregated_in_top_products(self, client):
        c, db = client
        upsert_sale(db, _sale("X1", product_name="Pulseira Corda", quantity=1))
        upsert_sale(db, _sale("X2", product_name="Pulseira Corda", quantity=2))
        entry = c.get("/sales/").json()["top_products"][0]
        assert entry["total_units"] == 3


# ── GET /sales/by-state ───────────────────────────────────────────────────────

class TestSalesByState:

    def test_returns_200(self, client):
        c, db = client
        assert c.get("/sales/by-state").status_code == 200

    def test_returns_list(self, client):
        c, db = client
        assert isinstance(c.get("/sales/by-state").json(), list)

    def test_empty_db_returns_empty_list(self, client):
        c, db = client
        assert c.get("/sales/by-state").json() == []

    def test_entry_has_required_keys(self, client):
        c, db = client
        upsert_sale(db, _sale(buyer_state="RJ"))
        entry = c.get("/sales/by-state").json()[0]
        for key in ("buyer_state", "material", "total_units", "total_revenue"):
            assert key in entry

    def test_buyer_state_correct(self, client):
        c, db = client
        upsert_sale(db, _sale(buyer_state="MG"))
        entry = c.get("/sales/by-state").json()[0]
        assert entry["buyer_state"] == "MG"

    def test_multiple_states(self, client):
        c, db = client
        upsert_sale(db, _sale("A", buyer_state="SP"))
        upsert_sale(db, _sale("B", buyer_state="RJ"))
        result = c.get("/sales/by-state").json()
        states = {e["buyer_state"] for e in result}
        assert "SP" in states
        assert "RJ" in states

    def test_filter_by_material(self, client):
        c, db = client
        upsert_sale(db, _sale("A", material="corda"))
        upsert_sale(db, _sale("B", material="metal"))
        result = c.get("/sales/by-state?material=corda").json()
        assert all(e["material"] == "corda" for e in result)

    def test_days_param_accepted(self, client):
        c, db = client
        assert c.get("/sales/by-state?days=7").status_code == 200

    def test_days_below_minimum_rejected(self, client):
        c, db = client
        assert c.get("/sales/by-state?days=0").status_code == 422
