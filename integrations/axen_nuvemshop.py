"""
integrations/axen_nuvemshop.py — Nuvemshop REST API integration.

Implements the BaseIntegration contract for Nuvemshop (Tiendanube) Brazil.

Environment variables (all required when NUVEMSHOP_ENABLED=true)
────────────────────────────────────────────────────────────────
  NUVEMSHOP_ENABLED       "true" to activate (default: false)
  NUVEMSHOP_USER_ID       Numeric store / user ID on Nuvemshop
  NUVEMSHOP_ACCESS_TOKEN  Permanent access token (no OAuth refresh needed)

Authentication
──────────────
  Header-based — every request includes:
    Authentication: bearer <NUVEMSHOP_ACCESS_TOKEN>
    User-Agent: AXEN-Intelligence/1.0 (thicapella@gmail.com)
  Tokens on Nuvemshop do not expire; no refresh logic is required.

Pagination
──────────
  Nuvemshop paginates with page/per_page query params (max 200 per page).
  get_sales_report() fetches pages until a page contains fewer than
  per_page items, then stops.

Dependency injection
────────────────────
  __init__() accepts an optional `client` parameter (httpx.Client-compatible).
  Pass a MagicMock in tests to avoid real network calls.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from integrations.axen_base_integration import BaseIntegration

log = logging.getLogger(__name__)

_PER_PAGE = 200
_USER_AGENT = "AXEN-Intelligence/1.0 (thicapella@gmail.com)"


class NuvemshopIntegration(BaseIntegration):
    """
    Nuvemshop REST API client.

    Parameters
    ----------
    client:
        Optional pre-built HTTP client (httpx.Client-compatible).
        Useful for unit tests — pass a MagicMock with .get() returning
        fake Response objects.
    """

    name = "nuvemshop"
    BASE_URL_TEMPLATE = "https://api.nuvemshop.com.br/v1/{user_id}"

    def __init__(self, client: Any = None) -> None:
        self.check_enabled()

        self._user_id = os.getenv("NUVEMSHOP_USER_ID", "")
        self._access_token = os.getenv("NUVEMSHOP_ACCESS_TOKEN", "")
        self._base_url = self.BASE_URL_TEMPLATE.format(user_id=self._user_id)

        self._http: Any = client if client is not None else httpx.Client(timeout=30.0)

    # ── Low-level HTTP ────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authentication": f"bearer {self._access_token}",
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, **params: Any) -> Any:
        """
        Authenticated GET against *path* (relative to base URL).

        Returns
        -------
        Parsed JSON body (dict or list).
        """
        url = f"{self._base_url}{path}"
        resp = self._http.get(url, headers=self._headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    # ── Public interface ──────────────────────────────────────────────────────

    def get_sales_report(self, days: int = 30) -> list[dict]:
        """
        Fetch orders created in the last *days* days.

        Calls ``GET /orders`` with pagination (200 items per page).

        Returns
        -------
        list[dict]:
            Normalised order records (same schema as MercadoLivreIntegration)::

                {
                    "order_id":     int,
                    "buyer_id":     int | None,
                    "total_amount": float,
                    "status":       str,
                    "date_created": str,   # ISO-8601
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

        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )

        all_orders: list[dict] = []
        page = 1

        try:
            while True:
                raw = self._get(
                    "/orders",
                    per_page=_PER_PAGE,
                    page=page,
                    created_at_min=since,
                )
                page_orders: list[dict] = raw if isinstance(raw, list) else []

                for order in page_orders:
                    buyer = order.get("customer") or {}
                    products_raw = order.get("products") or []
                    items: list[dict] = [
                        {
                            "sku":        p.get("sku"),
                            "title":      p.get("name", ""),
                            "qty":        int(p.get("quantity") or 0),
                            "unit_price": float(p.get("price") or 0.0),
                        }
                        for p in products_raw
                    ]
                    all_orders.append(
                        {
                            "order_id":     order.get("id"),
                            "buyer_id":     buyer.get("id"),
                            "total_amount": float(order.get("total") or 0.0),
                            "status":       order.get("payment_status", ""),
                            "date_created": order.get("created_at", ""),
                            "items":        items,
                        }
                    )

                if len(page_orders) < _PER_PAGE:
                    break
                page += 1

        except Exception as exc:
            log.error("[nuvemshop] get_sales_report failed: %s", exc)
            return []

        return all_orders

    def get_ad_metrics(self, days: int = 30) -> list[dict]:
        """
        Nuvemshop has no native advertising API.

        Always returns an empty list.
        """
        return []
