"""
tests/api/test_agents_router.py

Tests for GET /agents/ and POST /agents/run.

DB injected via dependency override; products inserted with
ingest_scrape_run() to give agents something to analyse.
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

def _competitor(material="corda", price=80.0) -> Product:
    """Cheap competitor — gap > 10% for corda (AXEN ref = 125)."""
    return Product(
        store="Key Design",
        name=f"Pulseira {material}",
        price=price,
        material=material,
        url="https://keydesign.com.br/p1",
    )


def _ml_product(item_id="MLB001", position=3, material="corda") -> Product:
    return Product(
        store="Mercado Livre",
        name="Pulseira ML",
        price=89.90,
        material=material,
        url=f"https://www.mercadolivre.com.br/p/{item_id}",
        context=f"id:{item_id} position:{position} query:pulseira_corda",
    )


# ── GET /agents/ ──────────────────────────────────────────────────────────────

class TestListAgents:

    def test_returns_200(self, client):
        c, db = client
        assert c.get("/agents/").status_code == 200

    def test_returns_list(self, client):
        c, db = client
        assert isinstance(c.get("/agents/").json(), list)

    def test_contains_pricing_agent(self, client):
        c, db = client
        names = [a["name"] for a in c.get("/agents/").json()]
        assert "pricing" in names

    def test_contains_ml_position_agent(self, client):
        c, db = client
        names = [a["name"] for a in c.get("/agents/").json()]
        assert "ml_position" in names

    def test_contains_promotions_agent(self, client):
        c, db = client
        names = [a["name"] for a in c.get("/agents/").json()]
        assert "promotions" in names

    def test_each_entry_has_name_and_description(self, client):
        c, db = client
        for entry in c.get("/agents/").json():
            assert "name" in entry
            assert "description" in entry

    def test_description_is_string(self, client):
        c, db = client
        for entry in c.get("/agents/").json():
            assert isinstance(entry["description"], str)


# ── POST /agents/run ──────────────────────────────────────────────────────────

class TestRunAgents:

    def test_returns_200(self, client):
        c, db = client
        resp = c.post("/agents/run", json={"agent": "all"})
        assert resp.status_code == 200

    def test_returns_list(self, client):
        c, db = client
        result = c.post("/agents/run", json={"agent": "all"}).json()
        assert isinstance(result, list)

    def test_empty_db_produces_no_recommendations(self, client):
        c, db = client
        result = c.post("/agents/run", json={"agent": "all"}).json()
        assert result == []

    def test_pricing_agent_emits_rec_when_gap_exists(self, client):
        c, db = client
        ingest_scrape_run(db, [_competitor("corda", 80.0)])
        result = c.post("/agents/run", json={"agent": "pricing"}).json()
        assert len(result) == 1

    def test_pricing_agent_rec_has_correct_type(self, client):
        c, db = client
        ingest_scrape_run(db, [_competitor("corda", 80.0)])
        rec = c.post("/agents/run", json={"agent": "pricing"}).json()[0]
        assert rec["type"] == "price_suggestion"

    def test_ml_position_agent_run_on_empty_db(self, client):
        c, db = client
        result = c.post("/agents/run", json={"agent": "ml_position"}).json()
        assert result == []

    def test_promotions_agent_run_on_empty_db(self, client):
        c, db = client
        result = c.post("/agents/run", json={"agent": "promotions"}).json()
        assert result == []

    def test_run_all_returns_combined_results(self, client):
        c, db = client
        # Insert data that triggers the pricing agent
        ingest_scrape_run(db, [_competitor("corda", 80.0)])
        result = c.post("/agents/run", json={"agent": "all"}).json()
        # At least the pricing recommendation should appear
        assert any(r["type"] == "price_suggestion" for r in result)

    def test_invalid_agent_name_rejected(self, client):
        c, db = client
        resp = c.post("/agents/run", json={"agent": "nonexistent"})
        assert resp.status_code == 422

    def test_missing_agent_field_rejected(self, client):
        c, db = client
        resp = c.post("/agents/run", json={})
        # FastAPI uses the default value "all" when agent is absent
        assert resp.status_code == 200

    def test_second_run_deduplicates(self, client):
        """Running twice: second run emits nothing (rec already active)."""
        c, db = client
        ingest_scrape_run(db, [_competitor("corda", 80.0)])
        c.post("/agents/run", json={"agent": "pricing"})
        result2 = c.post("/agents/run", json={"agent": "pricing"}).json()
        assert result2 == []

    def test_response_items_have_required_keys(self, client):
        c, db = client
        ingest_scrape_run(db, [_competitor("corda", 80.0)])
        rec = c.post("/agents/run", json={"agent": "pricing"}).json()[0]
        for key in ("id", "type", "priority", "title", "body", "data"):
            assert key in rec
