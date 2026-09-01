"""
tests/integrations/test_axen_nuvemshop.py

Unit tests for NuvemshopIntegration.

All HTTP calls are intercepted via client dependency injection:
  NuvemshopIntegration(client=<MagicMock>)

Environment variables are set per-test using monkeypatch.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from integrations.axen_base_integration import IntegrationDisabledError
from integrations.axen_nuvemshop import NuvemshopIntegration, _PER_PAGE


# ── Constants ─────────────────────────────────────────────────────────────────

_ENV = {
    "NUVEMSHOP_ENABLED":      "true",
    "NUVEMSHOP_USER_ID":      "987654",
    "NUVEMSHOP_ACCESS_TOKEN": "nv_test_token",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_response(json_body, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _build_client(get_body=None, get_status: int = 200) -> MagicMock:
    client = MagicMock()
    client.get.return_value = _mock_response(get_body or [], get_status)
    return client


def _nv_env(monkeypatch, **overrides) -> None:
    env = {**_ENV, **overrides}
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def _order(i: int = 0) -> dict:
    return {
        "id":             1000 + i,
        "customer":       {"id": 5000 + i},
        "total":          "149.90",
        "payment_status": "paid",
        "created_at":     "2026-05-01T12:00:00+00:00",
        "products": [
            {
                "sku":      f"SKU-{i:03d}",
                "name":     "Pulseira Corda",
                "quantity": 1,
                "price":    "149.90",
            }
        ],
    }


# ── Feature flag / init ───────────────────────────────────────────────────────

class TestInit:

    def test_raises_when_disabled(self, monkeypatch):
        monkeypatch.setenv("NUVEMSHOP_ENABLED", "false")
        with pytest.raises(IntegrationDisabledError):
            NuvemshopIntegration()

    def test_raises_when_flag_not_set(self, monkeypatch):
        monkeypatch.delenv("NUVEMSHOP_ENABLED", raising=False)
        with pytest.raises(IntegrationDisabledError):
            NuvemshopIntegration()

    def test_no_error_when_enabled(self, monkeypatch):
        _nv_env(monkeypatch)
        assert NuvemshopIntegration(client=_build_client()) is not None

    def test_reads_user_id(self, monkeypatch):
        _nv_env(monkeypatch)
        intg = NuvemshopIntegration(client=_build_client())
        assert intg._user_id == "987654"

    def test_reads_access_token(self, monkeypatch):
        _nv_env(monkeypatch)
        intg = NuvemshopIntegration(client=_build_client())
        assert intg._access_token == "nv_test_token"

    def test_base_url_contains_user_id(self, monkeypatch):
        _nv_env(monkeypatch)
        intg = NuvemshopIntegration(client=_build_client())
        assert "987654" in intg._base_url

    def test_uses_injected_client(self, monkeypatch):
        _nv_env(monkeypatch)
        fake = _build_client()
        intg = NuvemshopIntegration(client=fake)
        assert intg._http is fake


# ── _headers() ───────────────────────────────────────────────────────────────

class TestHeaders:

    def test_authentication_header_contains_token(self, monkeypatch):
        _nv_env(monkeypatch)
        intg = NuvemshopIntegration(client=_build_client())
        h = intg._headers()
        assert "nv_test_token" in h["Authentication"]

    def test_authentication_header_uses_bearer(self, monkeypatch):
        _nv_env(monkeypatch)
        intg = NuvemshopIntegration(client=_build_client())
        h = intg._headers()
        assert h["Authentication"].startswith("bearer ")

    def test_user_agent_is_set(self, monkeypatch):
        _nv_env(monkeypatch)
        intg = NuvemshopIntegration(client=_build_client())
        assert "User-Agent" in intg._headers()


# ── get_sales_report() ────────────────────────────────────────────────────────

class TestGetSalesReport:

    def test_returns_list(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order()])
        result = NuvemshopIntegration(client=client).get_sales_report()
        assert isinstance(result, list)

    def test_returns_one_record(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order()])
        result = NuvemshopIntegration(client=client).get_sales_report()
        assert len(result) == 1

    def test_record_has_required_keys(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order()])
        rec = NuvemshopIntegration(client=client).get_sales_report()[0]
        for key in ("order_id", "buyer_id", "total_amount", "status", "date_created", "items"):
            assert key in rec

    def test_order_id_correct(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order(0)])
        rec = NuvemshopIntegration(client=client).get_sales_report()[0]
        assert rec["order_id"] == 1000

    def test_buyer_id_correct(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order(0)])
        rec = NuvemshopIntegration(client=client).get_sales_report()[0]
        assert rec["buyer_id"] == 5000

    def test_total_amount_is_float(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order()])
        rec = NuvemshopIntegration(client=client).get_sales_report()[0]
        assert isinstance(rec["total_amount"], float)
        assert rec["total_amount"] == pytest.approx(149.90)

    def test_status_correct(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order()])
        rec = NuvemshopIntegration(client=client).get_sales_report()[0]
        assert rec["status"] == "paid"

    def test_items_is_list(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order()])
        rec = NuvemshopIntegration(client=client).get_sales_report()[0]
        assert isinstance(rec["items"], list)
        assert len(rec["items"]) == 1

    def test_item_has_required_keys(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order()])
        item = NuvemshopIntegration(client=client).get_sales_report()[0]["items"][0]
        for key in ("sku", "title", "qty", "unit_price"):
            assert key in item

    def test_item_sku_correct(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order(0)])
        item = NuvemshopIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["sku"] == "SKU-000"

    def test_item_title_correct(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order()])
        item = NuvemshopIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["title"] == "Pulseira Corda"

    def test_item_qty_correct(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order()])
        item = NuvemshopIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["qty"] == 1

    def test_item_unit_price_correct(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order()])
        item = NuvemshopIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["unit_price"] == pytest.approx(149.90)

    def test_empty_page_returns_empty_list(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[])
        result = NuvemshopIntegration(client=client).get_sales_report()
        assert result == []

    def test_returns_empty_on_http_error(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[], get_status=500)
        result = NuvemshopIntegration(client=client).get_sales_report()
        assert result == []

    def test_calls_orders_endpoint(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[])
        NuvemshopIntegration(client=client).get_sales_report()
        url = client.get.call_args[0][0]
        assert "/orders" in url

    def test_passes_per_page_param(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[])
        NuvemshopIntegration(client=client).get_sales_report()
        params = client.get.call_args[1]["params"]
        assert params["per_page"] == _PER_PAGE

    def test_passes_created_at_min_param(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client(get_body=[])
        NuvemshopIntegration(client=client).get_sales_report()
        params = client.get.call_args[1]["params"]
        assert "created_at_min" in params

    def test_pagination_stops_when_page_is_full(self, monkeypatch):
        """
        When the first page has exactly _PER_PAGE items the client fetches
        a second page; the second page is empty so it stops.
        """
        _nv_env(monkeypatch)
        full_page = [_order(i) for i in range(_PER_PAGE)]
        client = MagicMock()
        client.get.side_effect = [
            _mock_response(full_page),   # page 1 — full
            _mock_response([]),          # page 2 — empty → stop
        ]
        result = NuvemshopIntegration(client=client).get_sales_report()
        assert len(result) == _PER_PAGE
        assert client.get.call_count == 2

    def test_pagination_stops_after_partial_page(self, monkeypatch):
        """Partial first page (< _PER_PAGE) → only one GET call."""
        _nv_env(monkeypatch)
        client = _build_client(get_body=[_order(0), _order(1)])
        NuvemshopIntegration(client=client).get_sales_report()
        assert client.get.call_count == 1

    def test_buyer_id_none_when_customer_missing(self, monkeypatch):
        _nv_env(monkeypatch)
        order = {**_order(), "customer": None}
        client = _build_client(get_body=[order])
        rec = NuvemshopIntegration(client=client).get_sales_report()[0]
        assert rec["buyer_id"] is None

    def test_sku_none_when_missing(self, monkeypatch):
        _nv_env(monkeypatch)
        order = _order()
        order["products"][0].pop("sku")
        client = _build_client(get_body=[order])
        item = NuvemshopIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["sku"] is None


# ── get_ad_metrics() ──────────────────────────────────────────────────────────

class TestGetAdMetrics:

    def test_returns_empty_list(self, monkeypatch):
        _nv_env(monkeypatch)
        result = NuvemshopIntegration(client=_build_client()).get_ad_metrics()
        assert result == []

    def test_returns_empty_list_regardless_of_days(self, monkeypatch):
        _nv_env(monkeypatch)
        result = NuvemshopIntegration(client=_build_client()).get_ad_metrics(days=90)
        assert result == []

    def test_makes_no_http_call(self, monkeypatch):
        _nv_env(monkeypatch)
        client = _build_client()
        NuvemshopIntegration(client=client).get_ad_metrics()
        client.get.assert_not_called()
