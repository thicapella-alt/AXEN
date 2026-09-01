"""
integrations/axen_mercadolivre.py — Mercado Livre API integration.

Implements the BaseIntegration contract for Mercado Livre Brazil.

Environment variables (all required when MERCADOLIVRE_ENABLED=true)
────────────────────────────────────────────────────────────────────
  MERCADOLIVRE_ENABLED     "true" to activate (default: false)
  ML_CLIENT_ID             OAuth 2.0 application client ID
  ML_CLIENT_SECRET         OAuth 2.0 application client secret
  ML_SELLER_ID             Numeric seller / user ID on Mercado Livre

Authentication
──────────────
  OAuth 2.0 client-credentials flow:
    POST https://api.mercadolibre.com/oauth/token
    grant_type=client_credentials, client_id=…, client_secret=…
  Tokens are cached in memory until they expire (expires_in seconds).
  On HTTP 401 the token is refreshed once and the request is retried.

Dependency injection
────────────────────
  __init__() accepts an optional `client` parameter (any object whose
  interface matches httpx.Client: .post(), .get()).  Pass a MagicMock in
  tests to avoid real network calls.  If omitted, a real httpx.Client is
  constructed with a 30-second timeout.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from integrations.axen_base_integration import BaseIntegration, IntegrationDisabledError  # noqa: F401

log = logging.getLogger(__name__)


class MercadoLivreIntegration(BaseIntegration):
    """
    Mercado Livre REST API client.

    Parameters
    ----------
    client:
        Optional pre-built HTTP client (httpx.Client-compatible).
        Useful for unit tests — pass a MagicMock with .post()/.get()
        returning fake Response objects.
    """

    name = "mercadolivre"
    BASE_URL = "https://api.mercadolibre.com"

    def __init__(self, client: Any = None) -> None:
        self.check_enabled()

        self._client_id = os.getenv("ML_CLIENT_ID", "")
        self._client_secret = os.getenv("ML_CLIENT_SECRET", "")
        self._seller_id = os.getenv("ML_SELLER_ID", "")

        # Injected or real HTTP client
        self._http: Any = client if client is not None else httpx.Client(timeout=30.0)

        # Token cache
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # ── Authentication ────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        """
        Return a valid Bearer token, refreshing it when expired.

        The token is cached in memory; a new one is fetched automatically
        when fewer than 60 seconds remain before expiry.
        """
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        resp = self._http.post(
            f"{self.BASE_URL}/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + float(payload.get("expires_in", 3600))
        return self._token  # type: ignore[return-value]

    # ── Low-level HTTP ────────────────────────────────────────────────────────

    def _get(self, path: str, **params: Any) -> Any:
        """
        Authenticated GET against *path*.

        On HTTP 401 the token cache is cleared and the request is retried
        exactly once before propagating the error.

        Returns
        -------
        Parsed JSON body (dict or list).
        """
        url = f"{self.BASE_URL}{path}"
        for attempt in range(2):
            token = self._get_token()
            resp = self._http.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            if resp.status_code == 401 and attempt == 0:
                # Token rejected — force refresh on next iteration
                self._token = None
                self._token_expires_at = 0.0
                continue
            resp.raise_for_status()
            return resp.json()
        # Should be unreachable, but satisfy the type checker
        raise RuntimeError("Unexpected exit from _get() retry loop")  # pragma: no cover

    # ── Public interface ──────────────────────────────────────────────────────

    def get_sales_report(self, days: int = 30) -> list[dict]:
        """
        Fetch orders placed in the last *days* days.

        Calls ``GET /orders/search`` with ``seller=<ML_SELLER_ID>``,
        ``sort=date_desc``, and ``date_created.from`` set to *days* ago.

        Returns
        -------
        list[dict]:
            Normalised order records::

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
            "%Y-%m-%dT%H:%M:%S.000-00:00"
        )

        try:
            raw = self._get(
                "/orders/search",
                seller=self._seller_id,
                sort="date_desc",
                **{"date_created.from": since},
            )
        except Exception as exc:
            log.error("[mercadolivre] get_sales_report failed: %s", exc)
            return []

        orders: list[dict] = raw.get("results", []) if isinstance(raw, dict) else []

        normalised: list[dict] = []
        for order in orders:
            buyer = order.get("buyer") or {}
            items_raw = order.get("order_items") or []
            items: list[dict] = [
                {
                    "sku":        (it.get("item") or {}).get("seller_sku"),
                    "title":      (it.get("item") or {}).get("title", ""),
                    "qty":        int(it.get("quantity") or 0),
                    "unit_price": float(it.get("unit_price") or 0.0),
                }
                for it in items_raw
            ]
            normalised.append(
                {
                    "order_id":     order.get("id"),
                    "buyer_id":     buyer.get("id"),
                    "total_amount": float(order.get("total_amount") or 0.0),
                    "status":       order.get("status", ""),
                    "date_created": order.get("date_created", ""),
                    "items":        items,
                }
            )

        return normalised

    def get_pending_orders(self, days: int = 7) -> list[dict]:
        """
        Returns paid orders that still need to be shipped (last N days).
        Filters for order.status=paid, sorted oldest-first so most urgent appear first.

        Returns
        -------
        list[dict]:
            {order_id, buyer_name, buyer_id, total, date_created,
             shipping_status, product_title, action_url}
        """
        from datetime import datetime, timedelta, timezone

        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%S.000-00:00"
        )
        try:
            raw = self._get(
                "/orders/search",
                seller=self._seller_id,
                sort="date_asc",
                **{"order.status": "paid", "date_created.from": since},
            )
        except Exception as exc:
            log.error("[ML] get_pending_orders falhou: %s", exc)
            return []

        orders = raw.get("results", []) if isinstance(raw, dict) else []
        result = []
        for order in orders:
            buyer    = order.get("buyer") or {}
            shipping = order.get("shipping") or {}
            items    = order.get("order_items") or []
            title    = ((items[0].get("item") or {}).get("title", "")) if items else ""
            result.append({
                "order_id":       str(order.get("id", "")),
                "buyer_name":     buyer.get("nickname", buyer.get("first_name", "")),
                "total":          float(order.get("total_amount") or 0),
                "date_created":   order.get("date_created", ""),
                "shipping_status": shipping.get("status", ""),
                "product_title":  title,
                "action_url":     f"https://www.mercadolivre.com.br/vendas/{order.get('id', '')}",
            })

        log.info("[ML] %d pedidos aguardando envio.", len(result))
        return result

    def get_unanswered_questions(self) -> list[dict]:
        """
        Returns buyer questions not yet answered by the seller.

        Returns
        -------
        list[dict]:
            {question_id, text, date_created, item_id, item_title, action_url}
        """
        try:
            raw = self._get(
                "/questions/search",
                seller_id=self._seller_id,
                status="UNANSWERED",
                sort_fields="date_created",
                sort_types="DESC",
            )
        except Exception as exc:
            log.error("[ML] get_unanswered_questions falhou: %s", exc)
            return []

        questions = raw.get("questions", []) if isinstance(raw, dict) else []
        result = []
        for q in questions:
            item_id = q.get("item_id", "")
            result.append({
                "question_id":  str(q.get("id", "")),
                "text":         q.get("text", ""),
                "date_created": q.get("date_created", ""),
                "item_id":      item_id,
                "action_url":   f"https://www.mercadolivre.com.br/perguntas/{item_id}",
            })

        log.info("[ML] %d perguntas sem resposta.", len(result))
        return result

    def get_visits_report(self, days: int = 30) -> list[dict]:
        """
        Fetch visit totals per listing for the last *days* days.

        Steps:
          1. GET /users/{seller_id}/items/search → list of item IDs (MLB…)
          2. GET /visits/items?ids=<single_id>&date_from=…&date_to=… → {item_id: total}
             NOTE: API accepts only 1 ID per request.

        Returns
        -------
        list[dict]:
            One record per item, using today as date::

                {
                    "platform": "mercadolivre",
                    "source":   "MLB123456789",
                    "date":     "2026-06-19",
                    "visits":   142,
                }
        """
        from datetime import datetime, timedelta, timezone
        import json as _json
        import urllib.error as _urlerr
        import urllib.request as _urlreq

        # Coleta visitas apenas do dia atual para construir histórico evolutivo.
        # Cada sync adiciona um registro diário; o gráfico mostra a tendência ao longo do tempo.
        date_to   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        date_from = date_to

        # Step 1: list item IDs
        try:
            raw = self._get(f"/users/{self._seller_id}/items/search", limit=100)
            item_ids: list[str] = raw.get("results", []) if isinstance(raw, dict) else []
        except Exception as exc:
            log.error("[ML] get_visits_report items/search falhou: %s", exc)
            return []

        if not item_ids:
            log.info("[ML] Nenhum anúncio ativo encontrado.")
            return []

        log.info("[ML] Consultando visitas (time_window %dd) para %d anúncios.", days, len(item_ids))

        import json as _json
        import urllib.error as _urlerr
        import urllib.request as _urlreq

        # Step 1b: títulos em batch
        titles: dict[str, str] = {}
        try:
            ids_param = ",".join(item_ids)
            titles_url = f"{self.BASE_URL}/items?ids={ids_param}&attributes=id,title"
            token = self._get_token()
            req = _urlreq.Request(titles_url, headers={"Authorization": f"Bearer {token}"})
            with _urlreq.urlopen(req, timeout=30) as resp:
                raw_items = _json.loads(resp.read())
            if isinstance(raw_items, list):
                for entry in raw_items:
                    body = entry.get("body", {}) if isinstance(entry, dict) else {}
                    iid = body.get("id", "")
                    short = " ".join((body.get("title", "") or "").split()[:2])
                    if iid:
                        titles[iid] = short
                        log.info("[ML] Título %s: %s", iid, short)
        except Exception as exc:
            log.warning("[ML] Falha ao buscar títulos: %s", exc)

        # Step 2: /items/{id}/visits/time_window — retorna breakdown diário + total único
        # Substitui /visits/items que só dava um número agregado sem breakdown por dia
        normalised: list[dict] = []
        for item_id in item_ids:
            full_url = (
                f"{self.BASE_URL}/items/{item_id}/visits/time_window"
                f"?last={days}&unit=day"
            )
            try:
                data = None
                for attempt in range(2):
                    token = self._get_token()
                    req = _urlreq.Request(
                        full_url,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    try:
                        with _urlreq.urlopen(req, timeout=30) as resp:
                            data = _json.loads(resp.read())
                        break
                    except _urlerr.HTTPError as he:
                        if he.code == 401 and attempt == 0:
                            self._token = None
                            self._token_expires_at = 0.0
                            continue
                        try:
                            _body = he.read().decode("utf-8", errors="replace")[:300]
                            log.warning("[ML] visits %s %d: %s", item_id, he.code, _body)
                        except Exception:
                            pass
                        raise

                title = titles.get(item_id, "")
                total = int((data or {}).get("total_visits", 0))
                daily = (data or {}).get("results", [])
                log.info("[ML] %s (%s) → total=%d, %d dias com visitas", item_id, title, total, len(daily))

                for day_entry in daily:
                    date_str = (day_entry.get("date") or "")[:10]  # "2026-06-20T00:00:00Z" → "2026-06-20"
                    day_visits = int(day_entry.get("total", 0))
                    if date_str and day_visits > 0:
                        normalised.append({
                            "platform":      "mercadolivre",
                            "source":        item_id,
                            "date":          date_str,
                            "visits":        day_visits,
                            "listing_title": title,
                        })
            except Exception as exc:
                log.warning("[ML] visits %s falhou: %s", item_id, exc)
                continue

        log.info("[ML] %d registros de visitas coletados.", len(normalised))
        return normalised

    def get_ad_metrics(self, days: int = 30) -> list[dict]:
        """
        Fetch Product Ads (advertising) metrics for the last *days* days.

        Calls ``GET /advertising/product_ads/reports``.
        Returns ``[]`` on any error (non-critical path).

        Returns
        -------
        list[dict]:
            Raw report rows as returned by the API, each converted to a
            plain Python dict.
        """
        from datetime import datetime, timedelta, timezone

        date_from = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        date_to = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            raw = self._get(
                "/advertising/product_ads/reports",
                date_from=date_from,
                date_to=date_to,
                user_id=self._seller_id,
            )
        except Exception as exc:
            log.warning("[mercadolivre] get_ad_metrics failed (non-critical): %s", exc)
            return []

        if isinstance(raw, list):
            return [dict(row) for row in raw]
        if isinstance(raw, dict):
            rows = raw.get("results") or raw.get("data") or []
            return [dict(row) for row in rows]
        return []
