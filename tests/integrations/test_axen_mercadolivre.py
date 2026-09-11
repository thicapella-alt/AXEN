"""
tests/integrations/test_axen_mercadolivre.py

Unit tests for MercadoLivreIntegration.

All HTTP calls are intercepted via client dependency injection:
  MercadoLivreIntegration(client=<MagicMock>)

The mock client exposes .post() and .get() that return fake
httpx.Response-like objects (themselves MagicMock with .status_code,
.raise_for_status(), and .json()).

Environment variables are set per-test using monkeypatch so no real
credentials are ever needed.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, call

import pytest

from integrations.axen_base_integration import IntegrationDisabledError, is_enabled
from integrations.axen_mercadolivre import MercadoLivreIntegration


# ── Constants used across tests ───────────────────────────────────────────────

_ENV = {
    "MERCADOLIVRE_ENABLED": "true",
    "ML_CLIENT_ID":         "fake_client_id",
    "ML_CLIENT_SECRET":     "fake_secret",
    "ML_SELLER_ID":         "123456789",
}

_TOKEN_PAYLOAD = {"access_token": "test_token_abc", "expires_in": 3600}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_response(json_body, status_code: int = 200) -> MagicMock:
    """Return a MagicMock that looks like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _build_client(
    token_payload=None,
    get_body=None,
    get_status: int = 200,
) -> MagicMock:
    """
    Build a mock HTTP client.

    Parameters
    ----------
    token_payload:
        JSON returned by POST /oauth/token (default: _TOKEN_PAYLOAD).
    get_body:
        JSON returned by the first GET call.
    get_status:
        HTTP status for the GET call.
    """
    client = MagicMock()
    client.post.return_value = _mock_response(token_payload or _TOKEN_PAYLOAD)
    client.get.return_value = _mock_response(get_body or {}, get_status)
    return client


def _ml_env(monkeypatch, **overrides) -> None:
    """Apply ML environment variables for a test."""
    env = {**_ENV, **overrides}
    for k, v in env.items():
        monkeypatch.setenv(k, v)


# ── is_enabled() ─────────────────────────────────────────────────────────────

class TestIsEnabled:

    def test_returns_true_when_env_var_is_true(self, monkeypatch):
        monkeypatch.setenv("MERCADOLIVRE_ENABLED", "true")
        assert is_enabled("mercadolivre") is True

    def test_returns_false_when_env_var_is_false(self, monkeypatch):
        monkeypatch.setenv("MERCADOLIVRE_ENABLED", "false")
        assert is_enabled("mercadolivre") is False

    def test_case_insensitive_true(self, monkeypatch):
        monkeypatch.setenv("MERCADOLIVRE_ENABLED", "TRUE")
        assert is_enabled("mercadolivre") is True

    def test_case_insensitive_name(self, monkeypatch):
        monkeypatch.setenv("MERCADOLIVRE_ENABLED", "true")
        assert is_enabled("MercadoLivre") is True

    def test_returns_false_when_not_set(self, monkeypatch):
        monkeypatch.delenv("MERCADOLIVRE_ENABLED", raising=False)
        assert is_enabled("mercadolivre") is False

    def test_nuvemshop_flag(self, monkeypatch):
        monkeypatch.setenv("NUVEMSHOP_ENABLED", "true")
        assert is_enabled("nuvemshop") is True

    def test_shopee_flag(self, monkeypatch):
        monkeypatch.setenv("SHOPEE_ENABLED", "true")
        assert is_enabled("shopee") is True

    def test_unknown_platform_uses_auto_generated_var(self, monkeypatch):
        monkeypatch.setenv("MYNEWPLATFORM_ENABLED", "true")
        assert is_enabled("mynewplatform") is True

    def test_unknown_platform_defaults_to_false(self, monkeypatch):
        monkeypatch.delenv("XYZPLATFORM_ENABLED", raising=False)
        assert is_enabled("xyzplatform") is False


# ── IntegrationDisabledError ──────────────────────────────────────────────────

class TestIntegrationDisabledError:

    def test_raised_when_flag_is_off(self, monkeypatch):
        monkeypatch.setenv("MERCADOLIVRE_ENABLED", "false")
        with pytest.raises(IntegrationDisabledError):
            MercadoLivreIntegration()

    def test_raised_when_flag_not_set(self, monkeypatch):
        monkeypatch.delenv("MERCADOLIVRE_ENABLED", raising=False)
        with pytest.raises(IntegrationDisabledError):
            MercadoLivreIntegration()

    def test_error_message_mentions_integration_name(self, monkeypatch):
        monkeypatch.setenv("MERCADOLIVRE_ENABLED", "false")
        with pytest.raises(IntegrationDisabledError, match="mercadolivre"):
            MercadoLivreIntegration()

    def test_no_error_when_flag_is_true(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client()
        # Should not raise
        integration = MercadoLivreIntegration(client=client)
        assert integration is not None


# ── __init__ / env-var reading ────────────────────────────────────────────────

class TestInit:

    def test_reads_client_id_from_env(self, monkeypatch):
        _ml_env(monkeypatch)
        integration = MercadoLivreIntegration(client=_build_client())
        assert integration._client_id == "fake_client_id"

    def test_reads_client_secret_from_env(self, monkeypatch):
        _ml_env(monkeypatch)
        integration = MercadoLivreIntegration(client=_build_client())
        assert integration._client_secret == "fake_secret"

    def test_reads_seller_id_from_env(self, monkeypatch):
        _ml_env(monkeypatch)
        integration = MercadoLivreIntegration(client=_build_client())
        assert integration._seller_id == "123456789"

    def test_uses_injected_client(self, monkeypatch):
        _ml_env(monkeypatch)
        fake_client = _build_client()
        integration = MercadoLivreIntegration(client=fake_client)
        assert integration._http is fake_client

    def test_token_initially_none(self, monkeypatch):
        _ml_env(monkeypatch)
        integration = MercadoLivreIntegration(client=_build_client())
        assert integration._token is None

    def test_token_expires_at_initially_zero(self, monkeypatch):
        _ml_env(monkeypatch)
        integration = MercadoLivreIntegration(client=_build_client())
        assert integration._token_expires_at == 0.0


# ── _get_token() ──────────────────────────────────────────────────────────────

class TestGetToken:

    def test_fetches_token_on_first_call(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client()
        integration = MercadoLivreIntegration(client=client)
        token = integration._get_token()
        assert token == "test_token_abc"
        client.post.assert_called_once()

    def test_posts_to_oauth_token_endpoint(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client()
        integration = MercadoLivreIntegration(client=client)
        integration._get_token()
        url = client.post.call_args[0][0]
        assert url.endswith("/oauth/token")

    def test_posts_client_credentials_grant(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client()
        integration = MercadoLivreIntegration(client=client)
        integration._get_token()
        data = client.post.call_args[1]["data"]
        assert data["grant_type"] == "client_credentials"

    def test_posts_client_id(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client()
        integration = MercadoLivreIntegration(client=client)
        integration._get_token()
        data = client.post.call_args[1]["data"]
        assert data["client_id"] == "fake_client_id"

    def test_posts_client_secret(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client()
        integration = MercadoLivreIntegration(client=client)
        integration._get_token()
        data = client.post.call_args[1]["data"]
        assert data["client_secret"] == "fake_secret"

    def test_token_cached_on_second_call(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client()
        integration = MercadoLivreIntegration(client=client)
        integration._get_token()
        integration._get_token()
        # POST should only have been called once
        assert client.post.call_count == 1

    def test_token_refreshed_when_expired(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client()
        integration = MercadoLivreIntegration(client=client)
        # Pre-warm with an already-expired token
        integration._token = "old_token"
        integration._token_expires_at = time.time() - 1  # expired
        integration._get_token()
        assert client.post.call_count == 1
        assert integration._token == "test_token_abc"

    def test_token_not_refreshed_when_still_valid(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client()
        integration = MercadoLivreIntegration(client=client)
        integration._token = "still_valid_token"
        integration._token_expires_at = time.time() + 3600  # valid for an hour
        token = integration._get_token()
        assert token == "still_valid_token"
        client.post.assert_not_called()

    def test_raises_on_http_error(self, monkeypatch):
        _ml_env(monkeypatch)
        client = MagicMock()
        client.post.return_value = _mock_response({}, status_code=500)
        integration = MercadoLivreIntegration(client=client)
        with pytest.raises(Exception):
            integration._get_token()


# ── _get() ────────────────────────────────────────────────────────────────────

class TestGet:

    def test_sends_bearer_token(self, monkeypatch):
        _ml_env(monkeypatch)
        body = {"key": "value"}
        client = _build_client(get_body=body)
        integration = MercadoLivreIntegration(client=client)
        integration._get("/some/path")
        headers = client.get.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer test_token_abc"

    def test_constructs_full_url(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body={})
        integration = MercadoLivreIntegration(client=client)
        integration._get("/some/path")
        url = client.get.call_args[0][0]
        assert url == "https://api.mercadolibre.com/some/path"

    def test_passes_query_params(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body={})
        integration = MercadoLivreIntegration(client=client)
        integration._get("/orders/search", seller="123")
        params = client.get.call_args[1]["params"]
        assert params["seller"] == "123"

    def test_returns_parsed_json(self, monkeypatch):
        _ml_env(monkeypatch)
        expected = {"results": [{"id": 1}]}
        client = _build_client(get_body=expected)
        integration = MercadoLivreIntegration(client=client)
        result = integration._get("/orders/search")
        assert result == expected

    def test_retries_on_401_once(self, monkeypatch):
        _ml_env(monkeypatch)
        client = MagicMock()
        # POST always returns a valid token
        client.post.return_value = _mock_response(_TOKEN_PAYLOAD)
        # First GET → 401; second GET → 200
        ok_resp = _mock_response({"results": []})
        client.get.side_effect = [_mock_response({}, 401), ok_resp]
        integration = MercadoLivreIntegration(client=client)
        result = integration._get("/orders/search")
        assert result == {"results": []}
        assert client.get.call_count == 2

    def test_token_cleared_after_401(self, monkeypatch):
        _ml_env(monkeypatch)
        client = MagicMock()
        client.post.return_value = _mock_response(_TOKEN_PAYLOAD)
        client.get.side_effect = [
            _mock_response({}, 401),
            _mock_response({}),
        ]
        integration = MercadoLivreIntegration(client=client)
        integration._get("/orders/search")
        # POST was called twice: once per _get_token() call
        assert client.post.call_count == 2

    def test_raises_after_two_401s(self, monkeypatch):
        """If the refreshed token is also rejected the error propagates."""
        _ml_env(monkeypatch)
        client = MagicMock()
        client.post.return_value = _mock_response(_TOKEN_PAYLOAD)
        client.get.return_value = _mock_response({}, 401)
        integration = MercadoLivreIntegration(client=client)
        with pytest.raises(Exception):
            integration._get("/orders/search")


# ── get_sales_report() ────────────────────────────────────────────────────────

class TestGetSalesReport:

    def _order_payload(self, n: int = 1) -> dict:
        return {
            "results": [
                {
                    "id":           100 + i,
                    "buyer":        {"id": 9000 + i},
                    "total_amount": 199.90,
                    "status":       "paid",
                    "date_created": "2026-05-01T12:00:00.000-03:00",
                    "order_items": [
                        {
                            "item":       {"title": "Pulseira Corda", "seller_sku": "SKU-001"},
                            "quantity":   1,
                            "unit_price": 199.90,
                        }
                    ],
                }
                for i in range(n)
            ]
        }

    def test_returns_list(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(1))
        result = MercadoLivreIntegration(client=client).get_sales_report()
        assert isinstance(result, list)

    def test_returns_one_record_per_order(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(3))
        result = MercadoLivreIntegration(client=client).get_sales_report()
        assert len(result) == 3

    def test_record_has_required_keys(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(1))
        rec = MercadoLivreIntegration(client=client).get_sales_report()[0]
        for key in ("order_id", "buyer_id", "total_amount", "status", "date_created", "items"):
            assert key in rec

    def test_order_id_is_correct(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(1))
        rec = MercadoLivreIntegration(client=client).get_sales_report()[0]
        assert rec["order_id"] == 100

    def test_buyer_id_is_correct(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(1))
        rec = MercadoLivreIntegration(client=client).get_sales_report()[0]
        assert rec["buyer_id"] == 9000

    def test_total_amount_is_float(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(1))
        rec = MercadoLivreIntegration(client=client).get_sales_report()[0]
        assert isinstance(rec["total_amount"], float)
        assert rec["total_amount"] == pytest.approx(199.90)

    def test_status_correct(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(1))
        rec = MercadoLivreIntegration(client=client).get_sales_report()[0]
        assert rec["status"] == "paid"

    def test_items_is_list(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(1))
        rec = MercadoLivreIntegration(client=client).get_sales_report()[0]
        assert isinstance(rec["items"], list)
        assert len(rec["items"]) == 1

    def test_item_has_required_keys(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(1))
        item = MercadoLivreIntegration(client=client).get_sales_report()[0]["items"][0]
        for key in ("sku", "title", "qty", "unit_price"):
            assert key in item

    def test_item_sku_correct(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(1))
        item = MercadoLivreIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["sku"] == "SKU-001"

    def test_item_title_correct(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(1))
        item = MercadoLivreIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["title"] == "Pulseira Corda"

    def test_item_qty_correct(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(1))
        item = MercadoLivreIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["qty"] == 1

    def test_item_unit_price_correct(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=self._order_payload(1))
        item = MercadoLivreIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["unit_price"] == pytest.approx(199.90)

    def test_empty_results_key(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body={"results": []})
        result = MercadoLivreIntegration(client=client).get_sales_report()
        assert result == []

    def test_returns_empty_list_on_http_error(self, monkeypatch):
        _ml_env(monkeypatch)
        client = MagicMock()
        client.post.return_value = _mock_response(_TOKEN_PAYLOAD)
        client.get.return_value = _mock_response({}, status_code=500)
        result = MercadoLivreIntegration(client=client).get_sales_report()
        assert result == []

    def test_calls_orders_search_endpoint(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body={"results": []})
        MercadoLivreIntegration(client=client).get_sales_report()
        url = client.get.call_args[0][0]
        assert "/orders/search" in url

    def test_passes_seller_id_as_param(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body={"results": []})
        MercadoLivreIntegration(client=client).get_sales_report()
        params = client.get.call_args[1]["params"]
        assert str(params.get("seller")) == "123456789"

    def test_sku_none_when_missing(self, monkeypatch):
        """SKU is optional — should be None if seller_sku key is absent."""
        _ml_env(monkeypatch)
        payload = {
            "results": [
                {
                    "id":           200,
                    "buyer":        {"id": 8000},
                    "total_amount": 89.90,
                    "status":       "paid",
                    "date_created": "2026-05-01T12:00:00.000-03:00",
                    "order_items": [
                        {
                            "item":       {"title": "Pulseira Metal"},
                            "quantity":   2,
                            "unit_price": 44.95,
                        }
                    ],
                }
            ]
        }
        client = _build_client(get_body=payload)
        item = MercadoLivreIntegration(client=client).get_sales_report()[0]["items"][0]
        assert item["sku"] is None


# ── get_ad_metrics() ──────────────────────────────────────────────────────────

class TestGetAdMetrics:

    def test_returns_list(self, monkeypatch):
        _ml_env(monkeypatch)
        rows = [{"impressions": 1000, "clicks": 50, "spend": 10.0}]
        client = _build_client(get_body=rows)
        result = MercadoLivreIntegration(client=client).get_ad_metrics()
        assert isinstance(result, list)

    def test_returns_rows_when_api_returns_list(self, monkeypatch):
        _ml_env(monkeypatch)
        rows = [{"impressions": 1000}, {"impressions": 2000}]
        client = _build_client(get_body=rows)
        result = MercadoLivreIntegration(client=client).get_ad_metrics()
        assert len(result) == 2

    def test_returns_rows_when_api_returns_dict_with_results(self, monkeypatch):
        _ml_env(monkeypatch)
        body = {"results": [{"impressions": 500}, {"impressions": 800}]}
        client = _build_client(get_body=body)
        result = MercadoLivreIntegration(client=client).get_ad_metrics()
        assert len(result) == 2

    def test_returns_rows_when_api_returns_dict_with_data(self, monkeypatch):
        _ml_env(monkeypatch)
        body = {"data": [{"impressions": 300}]}
        client = _build_client(get_body=body)
        result = MercadoLivreIntegration(client=client).get_ad_metrics()
        assert len(result) == 1

    def test_returns_empty_list_on_http_error(self, monkeypatch):
        _ml_env(monkeypatch)
        client = MagicMock()
        client.post.return_value = _mock_response(_TOKEN_PAYLOAD)
        client.get.return_value = _mock_response({}, status_code=503)
        result = MercadoLivreIntegration(client=client).get_ad_metrics()
        assert result == []

    def test_returns_empty_list_on_unexpected_exception(self, monkeypatch):
        _ml_env(monkeypatch)
        client = MagicMock()
        client.post.return_value = _mock_response(_TOKEN_PAYLOAD)
        client.get.side_effect = RuntimeError("network down")
        result = MercadoLivreIntegration(client=client).get_ad_metrics()
        assert result == []

    def test_rows_are_plain_dicts(self, monkeypatch):
        _ml_env(monkeypatch)
        rows = [{"impressions": 100, "clicks": 5}]
        client = _build_client(get_body=rows)
        result = MercadoLivreIntegration(client=client).get_ad_metrics()
        assert all(isinstance(r, dict) for r in result)

    def test_calls_advertising_endpoint(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=[])
        MercadoLivreIntegration(client=client).get_ad_metrics()
        url = client.get.call_args[0][0]
        assert "/advertising/product_ads/reports" in url

    def test_passes_date_range_params(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=[])
        MercadoLivreIntegration(client=client).get_ad_metrics(days=7)
        params = client.get.call_args[1]["params"]
        assert "date_from" in params
        assert "date_to" in params

    def test_returns_empty_on_empty_api_list(self, monkeypatch):
        _ml_env(monkeypatch)
        client = _build_client(get_body=[])
        assert MercadoLivreIntegration(client=client).get_ad_metrics() == []


# ── User-authorized (Authorization Code) endpoints — Fase 0 / item 0.2 spike ───
#
# These methods use _get_authed() / _get_user_token(), which delegates to
# api.routers.auth_ml.refresh_token_if_needed() instead of this class's own
# client-credentials _get_token(). So here we monkeypatch that function
# directly rather than relying on the mock client's .post() (which is only
# used by the client-credentials flow).

def _patch_user_token(monkeypatch, token="user_token_xyz"):
    import api.routers.auth_ml as auth_ml
    monkeypatch.setattr(auth_ml, "refresh_token_if_needed", lambda: token)


def _get_only_client(get_body=None, get_status: int = 200) -> MagicMock:
    """Mock client with only .get() wired — .post() must not be called."""
    client = MagicMock()
    client.get.return_value = _mock_response(get_body or {}, get_status)
    return client


class TestGetUserToken:

    def test_returns_token_from_refresh_token_if_needed(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch, token="abc123")
        integration = MercadoLivreIntegration(client=MagicMock())
        assert integration._get_user_token() == "abc123"

    def test_raises_when_no_token_available(self, monkeypatch):
        _ml_env(monkeypatch)
        import api.routers.auth_ml as auth_ml
        monkeypatch.setattr(auth_ml, "refresh_token_if_needed", lambda: None)
        integration = MercadoLivreIntegration(client=MagicMock())
        with pytest.raises(RuntimeError):
            integration._get_user_token()


class TestGetOrderDetail:

    def test_calls_orders_endpoint_with_id(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body={"id": 555, "status": "paid"})
        result = MercadoLivreIntegration(client=client).get_order_detail(555)
        assert result == {"id": 555, "status": "paid"}
        url = client.get.call_args[0][0]
        assert url.endswith("/orders/555")

    def test_uses_user_token_not_client_credentials(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch, token="user_tok")
        client = _get_only_client(get_body={"id": 1})
        MercadoLivreIntegration(client=client).get_order_detail(1)
        client.post.assert_not_called()
        headers = client.get.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer user_tok"


class TestGetShipmentDetail:

    def test_calls_shipments_endpoint_with_id(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body={"id": 999, "status": "delivered"})
        result = MercadoLivreIntegration(client=client).get_shipment_detail(999)
        assert result == {"id": 999, "status": "delivered"}
        url = client.get.call_args[0][0]
        assert url.endswith("/shipments/999")


class TestGetItemDetail:

    def test_calls_items_endpoint_with_attributes_filter(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body={"id": "MLB1", "pictures": [], "variations": []})
        result = MercadoLivreIntegration(client=client).get_item_detail("MLB1")
        assert result["id"] == "MLB1"
        url = client.get.call_args[0][0]
        assert url.endswith("/items/MLB1")
        params = client.get.call_args[1]["params"]
        assert params["attributes"] == "id,title,pictures,variations"


class TestGetInventoryStock:

    def test_calls_inventories_stock_fulfillment_endpoint(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body={"total": 10})
        result = MercadoLivreIntegration(client=client).get_inventory_stock("INV1")
        assert result == {"total": 10}
        url = client.get.call_args[0][0]
        assert url.endswith("/inventories/INV1/stock/fulfillment")


class TestGetClaimsSearch:

    def test_calls_claims_search_with_seller_as_respondent(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body={"data": []})
        MercadoLivreIntegration(client=client).get_claims_search()
        url = client.get.call_args[0][0]
        assert url.endswith("/post-purchase/v1/claims/search")
        params = client.get.call_args[1]["params"]
        assert params["player_id"] == "123456789"
        assert params["player_role"] == "respondent"


class TestGetQuestionsSearchRaw:

    def test_calls_questions_search_unanswered(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body={"questions": []})
        MercadoLivreIntegration(client=client).get_questions_search()
        url = client.get.call_args[0][0]
        assert url.endswith("/questions/search")
        params = client.get.call_args[1]["params"]
        assert params["status"] == "UNANSWERED"


class TestGetItemsDetailBatch:

    def test_returns_bodies_for_200_entries(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        body = [
            {"code": 200, "body": {"id": "MLB1", "title": "A"}},
            {"code": 200, "body": {"id": "MLB2", "title": "B"}},
        ]
        client = _get_only_client(get_body=body)
        result = MercadoLivreIntegration(client=client).get_items_detail_batch(["MLB1", "MLB2"])
        assert result == [{"id": "MLB1", "title": "A"}, {"id": "MLB2", "title": "B"}]

    def test_skips_non_200_entries(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        body = [
            {"code": 200, "body": {"id": "MLB1"}},
            {"code": 404, "body": {"error": "not_found"}},
        ]
        client = _get_only_client(get_body=body)
        result = MercadoLivreIntegration(client=client).get_items_detail_batch(["MLB1", "MLB2"])
        assert result == [{"id": "MLB1"}]

    def test_chunks_requests_at_20_ids(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body=[])
        ids = [f"MLB{i}" for i in range(45)]
        MercadoLivreIntegration(client=client).get_items_detail_batch(ids)
        assert client.get.call_count == 3  # 20 + 20 + 5

    def test_passes_attributes_param(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body=[])
        MercadoLivreIntegration(client=client).get_items_detail_batch(["MLB1"], attributes="id,title")
        params = client.get.call_args[1]["params"]
        assert params["attributes"] == "id,title"
        assert params["ids"] == "MLB1"

    def test_returns_empty_list_when_no_ids(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body=[])
        result = MercadoLivreIntegration(client=client).get_items_detail_batch([])
        assert result == []
        client.get.assert_not_called()


class TestGetOrdersSearchRaw:

    def test_calls_orders_search_with_seller_and_date_filter(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body={"results": []})
        MercadoLivreIntegration(client=client).get_orders_search_raw()
        url = client.get.call_args[0][0]
        assert url.endswith("/orders/search")
        params = client.get.call_args[1]["params"]
        assert params["seller"] == "123456789"
        assert "date_created.from" in params


class TestGetItemsSearchRaw:

    def test_calls_users_items_search(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body={"results": []})
        MercadoLivreIntegration(client=client).get_items_search_raw()
        url = client.get.call_args[0][0]
        assert url.endswith("/users/123456789/items/search")


class TestGetItemVisitsTimeWindow:

    def test_calls_visits_time_window_for_item(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body={"total_visits": 10, "results": []})
        MercadoLivreIntegration(client=client).get_item_visits_time_window("MLB1", days=30)
        url = client.get.call_args[0][0]
        assert url.endswith("/items/MLB1/visits/time_window")
        params = client.get.call_args[1]["params"]
        assert params["last"] == 30
        assert params["unit"] == "day"


class TestGetAdCampaigns:

    def test_calls_product_ads_campaigns_search_with_api_version_header(self, monkeypatch):
        _ml_env(monkeypatch)
        _patch_user_token(monkeypatch)
        client = _get_only_client(get_body={"results": []})
        MercadoLivreIntegration(client=client).get_ad_campaigns()
        url = client.get.call_args[0][0]
        assert "/advertising/advertisers/123456789/product_ads/campaigns/search" in url
        headers = client.get.call_args[1]["headers"]
        assert headers["Api-Version"] == "1"
