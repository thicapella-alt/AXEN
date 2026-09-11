"""
tests/api/test_axen_main.py

Smoke tests for the FastAPI application: health check, CORS headers,
router registration, and 404 behaviour on unknown routes.

Uses FastAPI's built-in TestClient (backed by httpx) — no real DB needed
for the meta-level tests here.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from api.axen_main import app
from api.axen_deps import get_db
from axen_database import get_connection, migrate


# ── DB override fixture ───────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path):
    """
    TestClient with an isolated in-memory DB injected via dependency override.
    The override is cleaned up after the test.
    """
    conn = get_connection(":memory:")
    migrate(conn)

    def _override_db():
        yield conn

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    conn.close()


# ── /healthz ──────────────────────────────────────────────────────────────────

class TestHealthz:

    def test_returns_200(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_status_is_ok(self, client):
        assert client.get("/healthz").json()["status"] == "ok"

    def test_version_present(self, client):
        assert "version" in client.get("/healthz").json()

    def test_version_value(self, client):
        assert client.get("/healthz").json()["version"] == "1.0.0"

    def test_content_type_is_json(self, client):
        resp = client.get("/healthz")
        assert "application/json" in resp.headers["content-type"]


# ── CORS ──────────────────────────────────────────────────────────────────────

class TestCORS:

    def test_cors_header_present_on_preflight(self, client):
        resp = client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" in resp.headers

    def test_cors_allows_all_origins(self, client):
        resp = client.get("/healthz", headers={"Origin": "http://example.com"})
        assert resp.headers.get("access-control-allow-origin") == "*"


# ── Router registration ───────────────────────────────────────────────────────

class TestRouterRegistration:

    def test_prices_root_reachable(self, client):
        resp = client.get("/prices/")
        assert resp.status_code == 200

    def test_competitors_root_reachable(self, client):
        resp = client.get("/competitors/")
        assert resp.status_code == 200

    def test_unknown_route_returns_404(self, client):
        resp = client.get("/does-not-exist")
        assert resp.status_code == 404

    def test_404_body_is_json(self, client):
        resp = client.get("/does-not-exist")
        assert resp.headers["content-type"].startswith("application/json")
