"""
tests/integrations/test_axen_shopee.py

Unit tests for ShopeeIntegration.

All HTTP calls are intercepted via client dependency injection:
  ShopeeIntegration(client=<MagicMock>)

Environment variables are set per-test using monkeypatch.
HMAC-SHA256 signature verification is tested against the known formula.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from unittest.mock import MagicMock

import pytest

from integrations.axen_base_integration import IntegrationDisabledError
from integrations.axen_shopee import ShopeeIntegration


# ── Constants ─────────────────────────────────────────────────────────────────

_ENV = {
    "SHOPEE_ENABLED":      "true",
    "SHOPEE_PARTNER_ID":   "100001",
    "SHOPEE_PARTNER_KEY":  "super_secret_key",
    "SHOPEE_ACCESS_TOKEN": "sp_access_token",
    "SHOPEE_SHOP_ID":      "200002",
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
    client.get.return_value = _mock_response(get_body or {}, get_status)
    return client


def _sp_env(monkeypatch, **overrides) -> None:
    env = {**_ENV, **overrides}
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def _order_list_payload(n: int = 1) -> dict:
    """Build a minimal Shopee /order/get_order_list response."""
    orders = [
        {
            "order_sn":      f"SP{1000 + i:06d}",
            "buyer_user_id": f"BUY{i:04d}",
            "total_amount":  "89.90",
            "order_status":  "COMPLETED",
            "create_time":   1746100000 + i,
            "item_list": [
                {
                    "item_sku":                   f"SKU-SP-{i:03d}",
                    "item_name":                  "Pulseira Metal Shopee",
                    "model_quantity_purchased":   2,
                    "model_discounted_price":     "44.95",
                }
            ],
        }
        for i in range(n)
    ]
    return {"response": {"order_list": orders}}


def _expected_sign(partner_id: str, path: str, ts: int,
                   access_token: str, shop_id: str,
                   partner_key: str) -> str:
    """Replicate the HMAC-SHA256 formula from axen_shopee.py."""
    base = f"{partner_id}{path}{ts}{access_token}{shop_id}"
    return hmac.new(
        partner_key.encode("utf-8"),
        base.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ── Feature flag / init ───────────────────────────────────────────────────────

class TestInit:

    def test_raises_when_disabled(self, monkeypatch):
        monkeypatch.setenv("SHOPEE_ENABLED", "false")
        with pytest.raises(IntegrationDisabledError):
            ShopeeIntegration()

    def test_raises_when_flag_not_set(self, monkeypatch):
        monkeypatch.delenv("SHOPEE_ENABLED", raising=False)
        with pytest.raises(IntegrationDisabledError):
            ShopeeIntegration()

    def test_no_error_when_enabled(self, monkeypatch):
        _sp_env(monkeypatch)
        assert ShopeeIntegration(client=_build_client()) is not None

    def test_reads_partner_id(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        assert intg._partner_id == "100001"

    def test_reads_partner_key(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        assert intg._partner_key == "super_secret_key"

    def test_reads_access_token(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        assert intg._access_token == "sp_access_token"

    def test_reads_shop_id(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        assert intg._shop_id == "200002"

    def test_uses_injected_client(self, monkeypatch):
        _sp_env(monkeypatch)
        fake = _build_client()
        intg = ShopeeIntegration(client=fake)
        assert intg._http is fake


# ── _sign() ───────────────────────────────────────────────────────────────────

class TestSign:

    def test_sign_matches_expected_formula(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        path = "/order/get_order_list"
        ts = 1746100000
        expected = _expected_sign(
            "100001", path, ts, "sp_access_token", "200002", "super_secret_key"
        )
        assert intg._sign(path, ts) == expected

    def test_sign_is_lowercase_hex(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        sig = intg._sign("/some/path", 1746100000)
        assert sig == sig.lower()
        assert all(c in "0123456789abcdef" for c in sig)

    def test_sign_length_is_64_chars(self, monkeypatch):
        """SHA-256 hex digest is always 64 characters."""
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        assert len(intg._sign("/order/get_order_list", 1746100000)) == 64

    def test_different_paths_produce_different_signatures(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        ts = 1746100000
        sig1 = intg._sign("/order/get_order_list", ts)
        sig2 = intg._sign("/shop/get_shop_info", ts)
        assert sig1 != sig2

    def test_different_timestamps_produce_different_signatures(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        path = "/order/get_order_list"
        sig1 = intg._sign(path, 1746100000)
        sig2 = intg._sign(path, 1746100001)
        assert sig1 != sig2


# ── _auth_params() ────────────────────────────────────────────────────────────

class TestAuthParams:

    def test_contains_partner_id(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        params = intg._auth_params("/order/get_order_list")
        assert params["partner_id"] == "100001"

    def test_contains_access_token(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        params = intg._auth_params("/order/get_order_list")
        assert params["access_token"] == "sp_access_token"

    def test_contains_shop_id(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        params = intg._auth_params("/order/get_order_list")
        assert params["shop_id"] == "200002"

    def test_contains_timestamp(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        params = intg._auth_params("/order/get_order_list")
        assert "timestamp" in params
        assert isinstance(params["timestamp"], int)

    def test_contains_sign(self, monkeypatch):
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        params = intg._auth_params("/order/get_order_list")
        assert "sign" in params

    def test_sign_is_valid_for_returned_timestamp(self, monkeypatch):
        """The sign in auth_params must be consistent with the timestamp."""
        _sp_env(monkeypatch)
        intg = ShopeeIntegration(client=_build_client())
        path = "/order/get_order_list"
        params = intg._auth_params(path)
        expected = _expected_sign(
            params["partner_id"], path, params["timestamp"],
            params["access_token"], params["shop_id"],
            "super_secret_key",
        )
        assert params["sign"] == expected


# ── get_sales_report() ────────────────────────────────────────────────────────

class TestGetSalesReport:

    def test_returns_list(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        result = ShopeeIntegration(client=client).get_sales_report()
        assert isinstance(result, list)

    def test_returns_one_record(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        result = ShopeeIntegration(client=client).get_sales_report()
        assert len(result) == 1

    def test_record_has_required_keys(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        rec = ShopeeIntegration(client=client).get_sales_report()[0]
        for key in ("order_id", "buyer_id", "total_amount", "status", "date_created", "items"):
            assert key in rec

    def test_order_id_correct(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        rec = ShopeeIntegration(client=client).get_sales_report()[0]
        assert rec["order_id"] == "SP001000"

    def test_buyer_id_correct(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        rec = ShopeeIntegration(client=client).get_sales_report()[0]
        assert rec["buyer_id"] == "BUY0000"

    def test_total_amount_is_float(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        rec = ShopeeIntegration(client=client).get_sales_report()[0]
        assert isinstance(rec["total_amount"], float)
        assert rec["total_amount"] == pytest.approx(89.90)

    def test_status_correct(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        rec = ShopeeIntegration(client=client).get_sales_report()[0]
        assert rec["status"] == "COMPLETED"

    def test_date_created_is_iso_string(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        rec = ShopeeIntegration(client=client).get_sales_report()[0]
        # Should be a non-empty ISO-8601 string
        assert isinstance(rec["date_created"], str)
        assert len(rec["date_created"]) > 0

    def test_items_is_list(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        rec = ShopeeIntegration(client=client).get_sales_report()[0]
        assert isinstance(rec["items"], list)
        assert len(rec["items"]) == 1

    def test_item_has_required_keys(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        item = ShopeeIntegration(client=client).get_sales_report()[0]["items"][0]
        for key in ("sku", "title", "qty", "unit_price"):
            assert key in item

    def test_item_sku_correct(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        item = ShopeeIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["sku"] == "SKU-SP-000"

    def test_item_title_correct(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        item = ShopeeIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["title"] == "Pulseira Metal Shopee"

    def test_item_qty_correct(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        item = ShopeeIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["qty"] == 2

    def test_item_unit_price_correct(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(1))
        item = ShopeeIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["unit_price"] == pytest.approx(44.95)

    def test_multiple_orders(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body=_order_list_payload(3))
        result = ShopeeIntegration(client=client).get_sales_report()
        assert len(result) == 3

    def test_empty_order_list(self, monkeypatch):
        _sp_env(monkeypatch)
        payload = {"response": {"order_list": []}}
        client = _build_client(get_body=payload)
        result = ShopeeIntegration(client=client).get_sales_report()
        assert result == []

    def test_returns_empty_on_http_error(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body={}, get_status=500)
        result = ShopeeIntegration(client=client).get_sales_report()
        assert result == []

    def test_returns_empty_on_unexpected_exception(self, monkeypatch):
        _sp_env(monkeypatch)
        client = MagicMock()
        client.get.side_effect = RuntimeError("network down")
        result = ShopeeIntegration(client=client).get_sales_report()
        assert result == []

    def test_calls_order_get_order_list_endpoint(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body={"response": {"order_list": []}})
        ShopeeIntegration(client=client).get_sales_report()
        url = client.get.call_args[0][0]
        assert "/order/get_order_list" in url

    def test_passes_sign_in_params(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body={"response": {"order_list": []}})
        ShopeeIntegration(client=client).get_sales_report()
        params = client.get.call_args[1]["params"]
        assert "sign" in params

    def test_passes_time_range_params(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client(get_body={"response": {"order_list": []}})
        ShopeeIntegration(client=client).get_sales_report()
        params = client.get.call_args[1]["params"]
        assert "time_from" in params
        assert "time_to" in params

    def test_missing_response_key_returns_empty(self, monkeypatch):
        """API response with no 'response' key → graceful empty list."""
        _sp_env(monkeypatch)
        client = _build_client(get_body={})
        result = ShopeeIntegration(client=client).get_sales_report()
        assert result == []

    def test_item_sku_none_when_missing(self, monkeypatch):
        """item_sku absent → sku=None."""
        _sp_env(monkeypatch)
        payload = {
            "response": {
                "order_list": [
                    {
                        "order_sn":      "SP999999",
                        "buyer_user_id": "BUY9999",
                        "total_amount":  "49.90",
                        "order_status":  "PAID",
                        "create_time":   1746100000,
                        "item_list": [
                            {
                                "item_name":                  "Pulseira Corda",
                                "model_quantity_purchased":   1,
                                "model_discounted_price":     "49.90",
                            }
                        ],
                    }
                ]
            }
        }
        client = _build_client(get_body=payload)
        item = ShopeeIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["sku"] is None


# ── get_ad_metrics() ──────────────────────────────────────────────────────────

class TestGetAdMetrics:

    def test_returns_empty_list(self, monkeypatch):
        _sp_env(monkeypatch)
        result = ShopeeIntegration(client=_build_client()).get_ad_metrics()
        assert result == []

    def test_returns_empty_regardless_of_days(self, monkeypatch):
        _sp_env(monkeypatch)
        result = ShopeeIntegration(client=_build_client()).get_ad_metrics(days=90)
        assert result == []

    def test_makes_no_http_call(self, monkeypatch):
        _sp_env(monkeypatch)
        client = _build_client()
        ShopeeIntegration(client=client).get_ad_metrics()
        client.get.assert_not_called()
