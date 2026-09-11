"""
integrations/axen_shopee.py — Shopee Open Platform API integration.

Implements the BaseIntegration contract for Shopee Brazil.

Environment variables (all required when SHOPEE_ENABLED=true)
─────────────────────────────────────────────────────────────
  SHOPEE_ENABLED        "true" to activate (default: false)
  SHOPEE_PARTNER_ID     Numeric partner / app ID
  SHOPEE_PARTNER_KEY    Secret key for HMAC-SHA256 signing
  SHOPEE_ACCESS_TOKEN   OAuth access token obtained via Shopee auth flow
  SHOPEE_SHOP_ID        Numeric shop ID

Authentication
──────────────
  Every request is signed with HMAC-SHA256:
    sign_string = "{partner_id}{path}{timestamp}{access_token}{shop_id}"
    sign        = HMAC-SHA256(key=partner_key, msg=sign_string).hexdigest()

  Mandatory query parameters on every call:
    partner_id, timestamp, access_token, shop_id, sign

Dependency injection
────────────────────
  __init__() accepts an optional `client` parameter (httpx.Client-compatible).
  Pass a MagicMock in tests to avoid real network calls.

Ad metrics
──────────
  The Shopee Ads API requires a separate approval process.
  get_ad_metrics() always returns [] — callers treat this as non-critical.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any

import httpx

from integrations.axen_base_integration import BaseIntegration

log = logging.getLogger(__name__)


class ShopeeIntegration(BaseIntegration):
    """
    Shopee Open Platform API client (v2).

    Parameters
    ----------
    client:
        Optional pre-built HTTP client (httpx.Client-compatible).
        Useful for unit tests — pass a MagicMock with .get() returning
        fake Response objects.
    """

    name = "shopee"
    BASE_URL = "https://partner.shopeemobile.com/api/v2"

    def __init__(self, client: Any = None) -> None:
        self.check_enabled()

        self._partner_id = os.getenv("SHOPEE_PARTNER_ID", "")
        self._partner_key = os.getenv("SHOPEE_PARTNER_KEY", "")
        self._access_token = os.getenv("SHOPEE_ACCESS_TOKEN", "")
        self._shop_id = os.getenv("SHOPEE_SHOP_ID", "")

        self._http: Any = client if client is not None else httpx.Client(timeout=30.0)

    # ── Authentication / signing ──────────────────────────────────────────────

    def _sign(self, path: str, timestamp: int) -> str:
        """
        Compute the HMAC-SHA256 request signature for *path*.

        Parameters
        ----------
        path:
            API path WITHOUT the base URL, e.g. ``"/order/get_order_list"``.
        timestamp:
            Unix timestamp (seconds) — must match the ``timestamp`` query param.

        Returns
        -------
        Lowercase hex digest.
        """
        base_string = (
            f"{self._partner_id}{path}{timestamp}"
            f"{self._access_token}{self._shop_id}"
        )
        return hmac.new(
            self._partner_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _auth_params(self, path: str) -> dict[str, Any]:
        """
        Return the mandatory auth query parameters for a signed request.

        A fresh timestamp is generated on every call so signatures are
        always valid (Shopee rejects timestamps older than 5 minutes).
        """
        ts = int(time.time())
        return {
            "partner_id":   self._partner_id,
            "timestamp":    ts,
            "access_token": self._access_token,
            "shop_id":      self._shop_id,
            "sign":         self._sign(path, ts),
        }

    # ── Low-level HTTP ────────────────────────────────────────────────────────

    def _get(self, path: str, **extra_params: Any) -> Any:
        """
        Signed GET against *path*.

        Returns
        -------
        Parsed JSON body (dict).
        """
        url = f"{self.BASE_URL}{path}"
        params = {**self._auth_params(path), **extra_params}
        resp = self._http.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    # ── Public interface ──────────────────────────────────────────────────────

    def get_sales_report(self, days: int = 30) -> list[dict]:
        """
        Fetch orders created in the last *days* days.

        Calls ``GET /order/get_order_list`` and normalises the response to the
        shared schema used by all AXEN integrations.

        Returns
        -------
        list[dict]:
            Normalised order records::

                {
                    "order_id":     str,         # Shopee order IDs are strings
                    "buyer_id":     str | None,
                    "total_amount": float,
                    "status":       str,
                    "date_created": str,          # ISO-8601
                    "items": [
                        {
                            "sku":        str | None,
                            "title":      str,
                            "qty":        int,
                            "unit_price": float,
                        },
                        …
                    ],
                }
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        time_from = int((now - timedelta(days=days)).timestamp())
        time_to = int(now.timestamp())

        path = "/order/get_order_list"

        try:
            raw = self._get(
                path,
                time_range_field="create_time",
                time_from=time_from,
                time_to=time_to,
                page_size=100,
                response_optional_fields="buyer_user_id,item_list,total_amount,pay_time",
            )
        except Exception as exc:
            log.error("[shopee] get_sales_report failed: %s", exc)
            return []

        response_body: dict = raw.get("response") or {}
        order_list: list[dict] = response_body.get("order_list") or []

        normalised: list[dict] = []
        for order in order_list:
            items_raw: list[dict] = order.get("item_list") or []
            items: list[dict] = [
                {
                    "sku":        it.get("item_sku"),
                    "title":      it.get("item_name", ""),
                    "qty":        int(it.get("model_quantity_purchased") or 0),
                    "unit_price": float(it.get("model_discounted_price") or 0.0),
                }
                for it in items_raw
            ]

            # Shopee timestamps are Unix epoch integers
            create_time = order.get("create_time") or 0
            try:
                date_created = datetime.fromtimestamp(
                    int(create_time), tz=timezone.utc
                ).isoformat()
            except (ValueError, OSError, OverflowError):
                date_created = ""

            normalised.append(
                {
                    "order_id":     order.get("order_sn"),
                    "buyer_id":     order.get("buyer_user_id"),
                    "total_amount": float(order.get("total_amount") or 0.0),
                    "status":       order.get("order_status", ""),
                    "date_created": date_created,
                    "items":        items,
                }
            )

        return normalised

    def get_ad_metrics(self, days: int = 30) -> list[dict]:
        """
        Shopee Ads API requires a separate approval process.

        Always returns an empty list (non-critical).
        """
        return []
