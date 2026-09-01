"""
tests/api/test_recommendations_router.py

Tests for GET /recommendations/, GET /recommendations/{id},
POST /recommendations/{id}/dismiss, POST /recommendations/{id}/apply.

DB injected via dependency override; recommendations inserted with
save_recommendation() before each test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.axen_main import app
from api.axen_deps import get_db
from axen_database import get_connection, migrate, save_recommendation


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

def _rec(
    db,
    type_="price_suggestion",
    priority="high",
    title="Test rec",
    body="body text",
    material="corda",
) -> int:
    return save_recommendation(
        db,
        type_=type_,
        priority=priority,
        title=title,
        body=body,
        material=material,
    )


# ── GET /recommendations/ ─────────────────────────────────────────────────────

class TestListRecommendations:

    def test_returns_200(self, client):
        c, db = client
        assert c.get("/recommendations/").status_code == 200

    def test_returns_empty_when_no_records(self, client):
        c, db = client
        assert c.get("/recommendations/").json() == []

    def test_returns_list(self, client):
        c, db = client
        _rec(db)
        assert isinstance(c.get("/recommendations/").json(), list)

    def test_returns_one_per_inserted_rec(self, client):
        c, db = client
        _rec(db, title="Rec A")
        _rec(db, title="Rec B")
        assert len(c.get("/recommendations/").json()) == 2

    def test_entry_has_id(self, client):
        c, db = client
        _rec(db)
        entry = c.get("/recommendations/").json()[0]
        assert "id" in entry

    def test_entry_has_type(self, client):
        c, db = client
        _rec(db, type_="price_suggestion")
        entry = c.get("/recommendations/").json()[0]
        assert entry["type"] == "price_suggestion"

    def test_entry_has_priority(self, client):
        c, db = client
        _rec(db, priority="high")
        entry = c.get("/recommendations/").json()[0]
        assert entry["priority"] == "high"

    def test_filter_by_type(self, client):
        c, db = client
        _rec(db, type_="price_suggestion")
        _rec(db, type_="promotion_alert")
        result = c.get("/recommendations/?type=price_suggestion").json()
        assert all(r["type"] == "price_suggestion" for r in result)
        assert len(result) == 1

    def test_filter_by_priority(self, client):
        c, db = client
        _rec(db, priority="high")
        _rec(db, priority="medium")
        result = c.get("/recommendations/?priority=high").json()
        assert all(r["priority"] == "high" for r in result)
        assert len(result) == 1

    def test_active_only_true_excludes_dismissed(self, client):
        c, db = client
        from axen_database import dismiss_recommendation
        rec_id = _rec(db)
        dismiss_recommendation(db, rec_id)
        result = c.get("/recommendations/?active_only=true").json()
        assert result == []

    def test_active_only_false_includes_dismissed(self, client):
        c, db = client
        from axen_database import dismiss_recommendation
        rec_id = _rec(db)
        dismiss_recommendation(db, rec_id)
        result = c.get("/recommendations/?active_only=false").json()
        assert len(result) == 1

    def test_limit_param_respected(self, client):
        c, db = client
        for i in range(5):
            _rec(db, title=f"Rec {i}")
        result = c.get("/recommendations/?limit=2").json()
        assert len(result) == 2

    def test_limit_below_minimum_rejected(self, client):
        c, db = client
        assert c.get("/recommendations/?limit=0").status_code == 422

    def test_active_only_defaults_to_true(self, client):
        """Default active_only=true: dismissed records are excluded."""
        c, db = client
        from axen_database import dismiss_recommendation
        rec_id = _rec(db)
        dismiss_recommendation(db, rec_id)
        # No query param — should use default active_only=True
        assert c.get("/recommendations/").json() == []

    def test_high_priority_comes_before_medium(self, client):
        c, db = client
        _rec(db, priority="medium", title="Medium rec")
        _rec(db, priority="high",   title="High rec")
        result = c.get("/recommendations/").json()
        assert result[0]["priority"] == "high"
        assert result[1]["priority"] == "medium"


# ── GET /recommendations/{rec_id} ─────────────────────────────────────────────

class TestGetRecommendation:

    def test_returns_200_when_found(self, client):
        c, db = client
        rec_id = _rec(db)
        assert c.get(f"/recommendations/{rec_id}").status_code == 200

    def test_returns_404_when_not_found(self, client):
        c, db = client
        assert c.get("/recommendations/9999").status_code == 404

    def test_returns_dict(self, client):
        c, db = client
        rec_id = _rec(db)
        result = c.get(f"/recommendations/{rec_id}").json()
        assert isinstance(result, dict)

    def test_id_matches(self, client):
        c, db = client
        rec_id = _rec(db)
        result = c.get(f"/recommendations/{rec_id}").json()
        assert result["id"] == rec_id

    def test_type_correct(self, client):
        c, db = client
        rec_id = _rec(db, type_="promotion_alert")
        result = c.get(f"/recommendations/{rec_id}").json()
        assert result["type"] == "promotion_alert"

    def test_404_detail_mentions_id(self, client):
        c, db = client
        resp = c.get("/recommendations/42")
        assert "42" in resp.json()["detail"]


# ── POST /recommendations/{rec_id}/dismiss ────────────────────────────────────

class TestDismiss:

    def test_returns_200_on_success(self, client):
        c, db = client
        rec_id = _rec(db)
        assert c.post(f"/recommendations/{rec_id}/dismiss").status_code == 200

    def test_returns_ok_true(self, client):
        c, db = client
        rec_id = _rec(db)
        result = c.post(f"/recommendations/{rec_id}/dismiss").json()
        assert result == {"ok": True}

    def test_returns_404_for_unknown_id(self, client):
        c, db = client
        assert c.post("/recommendations/9999/dismiss").status_code == 404

    def test_dismissed_rec_excluded_from_active_list(self, client):
        c, db = client
        rec_id = _rec(db)
        c.post(f"/recommendations/{rec_id}/dismiss")
        result = c.get("/recommendations/").json()
        assert result == []

    def test_dismiss_is_idempotent(self, client):
        """Dismissing an already-dismissed rec still returns 200."""
        c, db = client
        rec_id = _rec(db)
        c.post(f"/recommendations/{rec_id}/dismiss")
        resp = c.post(f"/recommendations/{rec_id}/dismiss")
        assert resp.status_code == 200


# ── POST /recommendations/{rec_id}/apply ─────────────────────────────────────

class TestApply:

    def test_returns_200_on_success(self, client):
        c, db = client
        rec_id = _rec(db)
        assert c.post(f"/recommendations/{rec_id}/apply").status_code == 200

    def test_returns_ok_true(self, client):
        c, db = client
        rec_id = _rec(db)
        result = c.post(f"/recommendations/{rec_id}/apply").json()
        assert result == {"ok": True}

    def test_returns_404_for_unknown_id(self, client):
        c, db = client
        assert c.post("/recommendations/9999/apply").status_code == 404

    def test_applied_rec_excluded_from_active_list(self, client):
        c, db = client
        rec_id = _rec(db)
        c.post(f"/recommendations/{rec_id}/apply")
        result = c.get("/recommendations/").json()
        assert result == []

    def test_apply_is_idempotent(self, client):
        """Applying an already-applied rec still returns 200."""
        c, db = client
        rec_id = _rec(db)
        c.post(f"/recommendations/{rec_id}/apply")
        resp = c.post(f"/recommendations/{rec_id}/apply")
        assert resp.status_code == 200
