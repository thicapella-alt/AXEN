"""
tests/api/test_roas_router.py

Tests for GET /roas/, GET /roas/campaigns, POST /roas/ingest.

DB injected via dependency override.  Campaign records are inserted via
POST /roas/ingest or directly with upsert_roas_campaign() before each test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.axen_main import app
from api.axen_deps import get_db
from axen_database import get_connection, migrate, upsert_roas_campaign


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

def _campaign(
    campaign_id="CMP-001",
    platform="mercadolivre",
    campaign_name="Campanha Corda",
    period_start="2026-05-01",
    period_end="2026-05-31",
    ad_spend=100.0,
    attributed_revenue=400.0,
    impressions=5000,
    clicks=200,
) -> dict:
    return {
        "platform":           platform,
        "campaign_id":        campaign_id,
        "campaign_name":      campaign_name,
        "period_start":       period_start,
        "period_end":         period_end,
        "ad_spend":           ad_spend,
        "attributed_revenue": attributed_revenue,
        "impressions":        impressions,
        "clicks":             clicks,
    }


# ── POST /roas/ingest ─────────────────────────────────────────────────────────

class TestIngestCampaigns:

    def test_returns_200(self, client):
        c, db = client
        assert c.post("/roas/ingest", json=[_campaign()]).status_code == 200

    def test_returns_inserted_count(self, client):
        c, db = client
        result = c.post("/roas/ingest", json=[_campaign("A"), _campaign("B")]).json()
        assert result == {"inserted": 2}

    def test_single_record(self, client):
        c, db = client
        result = c.post("/roas/ingest", json=[_campaign()]).json()
        assert result["inserted"] == 1

    def test_empty_list_returns_zero(self, client):
        c, db = client
        result = c.post("/roas/ingest", json=[]).json()
        assert result["inserted"] == 0

    def test_data_persists_to_db(self, client):
        c, db = client
        c.post("/roas/ingest", json=[_campaign("PERSIST")])
        row = db.execute(
            "SELECT * FROM roas_campaigns WHERE campaign_id=?", ("PERSIST",)
        ).fetchone()
        assert row is not None

    def test_roas_computed_automatically(self, client):
        """roas = attributed_revenue / ad_spend = 400 / 100 = 4.0."""
        c, db = client
        c.post("/roas/ingest", json=[_campaign("R1", ad_spend=100.0, attributed_revenue=400.0)])
        row = db.execute(
            "SELECT roas FROM roas_campaigns WHERE campaign_id=?", ("R1",)
        ).fetchone()
        assert row["roas"] == pytest.approx(4.0)

    def test_upsert_replaces_existing(self, client):
        """Second ingest with same campaign_id updates the record."""
        c, db = client
        c.post("/roas/ingest", json=[_campaign("UPS", ad_spend=100.0, attributed_revenue=200.0)])
        c.post("/roas/ingest", json=[_campaign("UPS", ad_spend=50.0, attributed_revenue=300.0)])
        row = db.execute(
            "SELECT ad_spend, attributed_revenue FROM roas_campaigns WHERE campaign_id=?",
            ("UPS",),
        ).fetchone()
        assert row["ad_spend"] == pytest.approx(50.0)
        assert row["attributed_revenue"] == pytest.approx(300.0)

    def test_non_list_body_rejected(self, client):
        c, db = client
        assert c.post("/roas/ingest", json={"campaign_id": "X"}).status_code == 422


# ── GET /roas/ ────────────────────────────────────────────────────────────────

class TestRoasSummary:

    def test_returns_200(self, client):
        c, db = client
        assert c.get("/roas/").status_code == 200

    def test_returns_dict(self, client):
        c, db = client
        assert isinstance(c.get("/roas/").json(), dict)

    def test_empty_db_returns_zero_campaign_count(self, client):
        """SQLite aggregate over empty table: campaign_count=0, best_campaign=None."""
        c, db = client
        result = c.get("/roas/").json()
        assert result["campaign_count"] == 0
        assert result["best_campaign"] is None

    def test_has_required_keys(self, client):
        c, db = client
        result = c.get("/roas/").json()
        for key in ("avg_roas", "total_spend", "total_revenue", "campaign_count", "best_campaign"):
            assert key in result

    def test_avg_roas_correct(self, client):
        """roas = 400/100 = 4.0; single campaign → avg_roas = 4.0."""
        c, db = client
        upsert_roas_campaign(db, _campaign(ad_spend=100.0, attributed_revenue=400.0))
        result = c.get("/roas/").json()
        assert result["avg_roas"] == pytest.approx(4.0)

    def test_total_spend_correct(self, client):
        c, db = client
        upsert_roas_campaign(db, _campaign("A", ad_spend=100.0, attributed_revenue=200.0))
        upsert_roas_campaign(db, _campaign("B", ad_spend=50.0,  attributed_revenue=150.0))
        result = c.get("/roas/").json()
        assert result["total_spend"] == pytest.approx(150.0)

    def test_total_revenue_correct(self, client):
        c, db = client
        upsert_roas_campaign(db, _campaign("A", ad_spend=100.0, attributed_revenue=200.0))
        upsert_roas_campaign(db, _campaign("B", ad_spend=50.0,  attributed_revenue=150.0))
        result = c.get("/roas/").json()
        assert result["total_revenue"] == pytest.approx(350.0)

    def test_campaign_count_correct(self, client):
        c, db = client
        upsert_roas_campaign(db, _campaign("A"))
        upsert_roas_campaign(db, _campaign("B"))
        result = c.get("/roas/").json()
        assert result["campaign_count"] == 2

    def test_best_campaign_is_dict_when_data_exists(self, client):
        c, db = client
        upsert_roas_campaign(db, _campaign())
        result = c.get("/roas/").json()
        assert isinstance(result["best_campaign"], dict)

    def test_best_campaign_has_name_and_roas(self, client):
        c, db = client
        upsert_roas_campaign(db, _campaign(campaign_name="Top Camp", ad_spend=100.0, attributed_revenue=500.0))
        bc = c.get("/roas/").json()["best_campaign"]
        assert "campaign_name" in bc
        assert "roas" in bc

    def test_best_campaign_is_highest_roas(self, client):
        c, db = client
        upsert_roas_campaign(db, _campaign("A", campaign_name="Low",  ad_spend=100.0, attributed_revenue=200.0))
        upsert_roas_campaign(db, _campaign("B", campaign_name="High", ad_spend=100.0, attributed_revenue=800.0))
        bc = c.get("/roas/").json()["best_campaign"]
        assert bc["campaign_name"] == "High"

    def test_days_param_accepted(self, client):
        c, db = client
        assert c.get("/roas/?days=7").status_code == 200

    def test_days_below_minimum_rejected(self, client):
        c, db = client
        assert c.get("/roas/?days=0").status_code == 422

    def test_days_above_maximum_rejected(self, client):
        c, db = client
        assert c.get("/roas/?days=366").status_code == 422


# ── GET /roas/campaigns ───────────────────────────────────────────────────────

class TestListCampaigns:

    def test_returns_200(self, client):
        c, db = client
        assert c.get("/roas/campaigns").status_code == 200

    def test_returns_list(self, client):
        c, db = client
        assert isinstance(c.get("/roas/campaigns").json(), list)

    def test_empty_when_no_records(self, client):
        c, db = client
        assert c.get("/roas/campaigns").json() == []

    def test_returns_one_per_campaign(self, client):
        c, db = client
        upsert_roas_campaign(db, _campaign("A"))
        upsert_roas_campaign(db, _campaign("B"))
        result = c.get("/roas/campaigns").json()
        assert len(result) == 2

    def test_entry_has_campaign_id(self, client):
        c, db = client
        upsert_roas_campaign(db, _campaign("CMP-XYZ"))
        entry = c.get("/roas/campaigns").json()[0]
        assert entry["campaign_id"] == "CMP-XYZ"

    def test_entry_has_roas(self, client):
        c, db = client
        upsert_roas_campaign(db, _campaign(ad_spend=100.0, attributed_revenue=300.0))
        entry = c.get("/roas/campaigns").json()[0]
        assert entry["roas"] == pytest.approx(3.0)

    def test_entry_has_platform(self, client):
        c, db = client
        upsert_roas_campaign(db, _campaign(platform="shopee"))
        entry = c.get("/roas/campaigns").json()[0]
        assert entry["platform"] == "shopee"

    def test_days_param_filters_records(self, client):
        """Campaign with period_start far in the past is excluded when days=1."""
        c, db = client
        upsert_roas_campaign(db, _campaign("OLD", period_start="2020-01-01", period_end="2020-01-31"))
        result = c.get("/roas/campaigns?days=1").json()
        assert result == []

    def test_days_param_accepted(self, client):
        c, db = client
        assert c.get("/roas/campaigns?days=90").status_code == 200

    def test_days_below_minimum_rejected(self, client):
        c, db = client
        assert c.get("/roas/campaigns?days=0").status_code == 422

    def test_days_above_maximum_rejected(self, client):
        c, db = client
        assert c.get("/roas/campaigns?days=366").status_code == 422
