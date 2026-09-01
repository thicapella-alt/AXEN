"""
tests/api/test_integrations_router.py

Tests for GET /integrations/, GET /integrations/mercadolivre/sales,
GET /integrations/mercadolivre/ads.

Two override strategies are used:
  - get_ml_integration() is overridden to return a mock ML integration
    (enabled path) or None (disabled path).
  - No real HTTP calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.axen_main import app
from api.axen_deps import get_db, get_ml_integration
from axen_database import get_connection, migrate


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db_conn():
    conn = get_connection(":memory:")
    migrate(conn)
    yield conn
    conn.close()


@pytest.fixture
def client_disabled(db_conn):
    """Client where all integrations are disabled (get_ml_integration → None)."""
    def _override_db():
        yield db_conn

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_ml_integration] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def ml_mock():
    """Pre-built MagicMock standing in for MercadoLivreIntegration."""
    mock = MagicMock()
    mock.get_sales_report.return_value = []
    mock.get_ad_metrics.return_value = []
    return mock


@pytest.fixture
def client_ml_enabled(db_conn, ml_mock):
    """Client where ML integration is enabled (get_ml_integration → mock)."""
    def _override_db():
        yield db_conn

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_ml_integration] = lambda: ml_mock
    with TestClient(app) as c:
        yield c, ml_mock
    app.dependency_overrides.clear()


# ── GET /integrations/ ────────────────────────────────────────────────────────

class TestListIntegrations:

    def test_returns_200(self, client_disabled):
        assert client_disabled.get("/integrations/").status_code == 200

    def test_returns_list(self, client_disabled):
        assert isinstance(client_disabled.get("/integrations/").json(), list)

    def test_contains_mercadolivre(self, client_disabled):
        names = [e["name"] for e in client_disabled.get("/integrations/").json()]
        assert "mercadolivre" in names

    def test_contains_nuvemshop(self, client_disabled):
        names = [e["name"] for e in client_disabled.get("/integrations/").json()]
        assert "nuvemshop" in names

    def test_contains_shopee(self, client_disabled):
        names = [e["name"] for e in client_disabled.get("/integrations/").json()]
        assert "shopee" in names

    def test_each_entry_has_name_and_enabled(self, client_disabled):
        for entry in client_disabled.get("/integrations/").json():
            assert "name" in entry
            assert "enabled" in entry

    def test_enabled_is_bool(self, client_disabled):
        for entry in client_disabled.get("/integrations/").json():
            assert isinstance(entry["enabled"], bool)

    def test_ml_shows_disabled_when_env_off(self, monkeypatch, db_conn):
        monkeypatch.setenv("MERCADOLIVRE_ENABLED", "false")

        def _override_db():
            yield db_conn

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_ml_integration] = lambda: None
        with TestClient(app) as c:
            result = c.get("/integrations/").json()
        app.dependency_overrides.clear()

        ml_entry = next(e for e in result if e["name"] == "mercadolivre")
        assert ml_entry["enabled"] is False

    def test_ml_shows_enabled_when_env_on(self, monkeypatch, db_conn, ml_mock):
        monkeypatch.setenv("MERCADOLIVRE_ENABLED", "true")

        def _override_db():
            yield db_conn

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_ml_integration] = lambda: ml_mock
        with TestClient(app) as c:
            result = c.get("/integrations/").json()
        app.dependency_overrides.clear()

        ml_entry = next(e for e in result if e["name"] == "mercadolivre")
        assert ml_entry["enabled"] is True


# ── GET /integrations/mercadolivre/sales ─────────────────────────────────────

class TestMLSales:

    def test_returns_503_when_disabled(self, client_disabled):
        assert client_disabled.get("/integrations/mercadolivre/sales").status_code == 503

    def test_503_detail_mentions_env_var(self, client_disabled):
        resp = client_disabled.get("/integrations/mercadolivre/sales")
        assert "MERCADOLIVRE_ENABLED" in resp.json()["detail"]

    def test_returns_200_when_enabled(self, client_ml_enabled):
        c, mock = client_ml_enabled
        assert c.get("/integrations/mercadolivre/sales").status_code == 200

    def test_returns_list_when_enabled(self, client_ml_enabled):
        c, mock = client_ml_enabled
        result = c.get("/integrations/mercadolivre/sales").json()
        assert isinstance(result, list)

    def test_calls_get_sales_report(self, client_ml_enabled):
        c, mock = client_ml_enabled
        c.get("/integrations/mercadolivre/sales")
        mock.get_sales_report.assert_called_once()

    def test_passes_days_param(self, client_ml_enabled):
        c, mock = client_ml_enabled
        c.get("/integrations/mercadolivre/sales?days=7")
        mock.get_sales_report.assert_called_once_with(days=7)

    def test_returns_sales_data(self, client_ml_enabled):
        c, mock = client_ml_enabled
        mock.get_sales_report.return_value = [{"order_id": 1, "total_amount": 99.0}]
        result = c.get("/integrations/mercadolivre/sales").json()
        assert len(result) == 1
        assert result[0]["order_id"] == 1

    def test_days_below_minimum_rejected(self, client_ml_enabled):
        c, mock = client_ml_enabled
        assert c.get("/integrations/mercadolivre/sales?days=0").status_code == 422

    def test_days_above_maximum_rejected(self, client_ml_enabled):
        c, mock = client_ml_enabled
        assert c.get("/integrations/mercadolivre/sales?days=366").status_code == 422


# ── GET /integrations/mercadolivre/ads ───────────────────────────────────────

class TestMLAds:

    def test_returns_503_when_disabled(self, client_disabled):
        assert client_disabled.get("/integrations/mercadolivre/ads").status_code == 503

    def test_503_detail_mentions_env_var(self, client_disabled):
        resp = client_disabled.get("/integrations/mercadolivre/ads")
        assert "MERCADOLIVRE_ENABLED" in resp.json()["detail"]

    def test_returns_200_when_enabled(self, client_ml_enabled):
        c, mock = client_ml_enabled
        assert c.get("/integrations/mercadolivre/ads").status_code == 200

    def test_returns_list_when_enabled(self, client_ml_enabled):
        c, mock = client_ml_enabled
        result = c.get("/integrations/mercadolivre/ads").json()
        assert isinstance(result, list)

    def test_calls_get_ad_metrics(self, client_ml_enabled):
        c, mock = client_ml_enabled
        c.get("/integrations/mercadolivre/ads")
        mock.get_ad_metrics.assert_called_once()

    def test_passes_days_param(self, client_ml_enabled):
        c, mock = client_ml_enabled
        c.get("/integrations/mercadolivre/ads?days=14")
        mock.get_ad_metrics.assert_called_once_with(days=14)

    def test_returns_ad_data(self, client_ml_enabled):
        c, mock = client_ml_enabled
        mock.get_ad_metrics.return_value = [{"impressions": 500}]
        result = c.get("/integrations/mercadolivre/ads").json()
        assert len(result) == 1
        assert result[0]["impressions"] == 500

    def test_empty_list_when_no_ads(self, client_ml_enabled):
        c, mock = client_ml_enabled
        mock.get_ad_metrics.return_value = []
        result = c.get("/integrations/mercadolivre/ads").json()
        assert result == []

    def test_days_below_minimum_rejected(self, client_ml_enabled):
        c, mock = client_ml_enabled
        assert c.get("/integrations/mercadolivre/ads?days=0").status_code == 422

    def test_days_above_maximum_rejected(self, client_ml_enabled):
        c, mock = client_ml_enabled
        assert c.get("/integrations/mercadolivre/ads?days=366").status_code == 422
