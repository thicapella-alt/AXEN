"""
api/routers/integrations.py — /integrations endpoints.

Exposes platform integration status and proxies data from enabled
third-party APIs (Mercado Livre, Nuvemshop, Shopee).

Endpoints
─────────
  GET /integrations/                       → enabled/disabled status per platform
  GET /integrations/mercadolivre/sales     → sales report (503 when disabled)
  GET /integrations/mercadolivre/ads       → ad metrics  (503 when disabled)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.axen_deps import get_ml_integration
from integrations.axen_base_integration import is_enabled
from integrations.axen_mercadolivre import MercadoLivreIntegration

router = APIRouter(prefix="/integrations", tags=["integrations"])

# All known platforms in registration order
_PLATFORMS = ["mercadolivre", "nuvemshop", "shopee"]


@router.get("/")
def list_integrations() -> list[dict]:
    """
    Return the enabled/disabled status of every known integration.

    Each entry::

        {"name": str, "enabled": bool}
    """
    return [{"name": p, "enabled": is_enabled(p)} for p in _PLATFORMS]


@router.get("/mercadolivre/sales")
def ml_sales(
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
    ml: Optional[MercadoLivreIntegration] = Depends(get_ml_integration),
) -> list[dict]:
    """
    Fetch the Mercado Livre sales report for the past *days* days.

    Returns HTTP 503 when the integration is disabled
    (``MERCADOLIVRE_ENABLED`` is not ``"true"``).

    Each order in the response::

        {
            "order_id":     int,
            "buyer_id":     int | None,
            "total_amount": float,
            "status":       str,
            "date_created": str,
            "items": [{"sku", "title", "qty", "unit_price"}, …]
        }
    """
    if ml is None:
        raise HTTPException(
            status_code=503,
            detail="Mercado Livre integration is disabled. Set MERCADOLIVRE_ENABLED=true.",
        )
    return ml.get_sales_report(days=days)


@router.get("/mercadolivre/ads")
def ml_ads(
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
    ml: Optional[MercadoLivreIntegration] = Depends(get_ml_integration),
) -> list[dict]:
    """
    Fetch Mercado Livre Product Ads metrics for the past *days* days.

    Returns HTTP 503 when the integration is disabled.
    Returns an empty list on any API-level error (non-critical path).
    """
    if ml is None:
        raise HTTPException(
            status_code=503,
            detail="Mercado Livre integration is disabled. Set MERCADOLIVRE_ENABLED=true.",
        )
    return ml.get_ad_metrics(days=days)
