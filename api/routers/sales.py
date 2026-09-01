"""
api/routers/sales.py — /sales endpoints.

Exposes sales analytics (top products, weekly trend, geographic breakdown)
and a write path for ingesting normalised orders from platform integrations.

Endpoints
─────────
  GET  /sales/            → {top_products, weekly} analytics
  GET  /sales/by-state    → geographic breakdown
  POST /sales/ingest      → bulk-upsert sale records; returns {inserted: N}
"""

from __future__ import annotations

import math
import sqlite3
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query

from api.axen_deps import get_db
from axen_database import (
    get_sales_by_state,
    get_top_products,
    get_weekly_sales,
    upsert_sale,
)

router = APIRouter(prefix="/sales", tags=["sales"])

_TOP_PRODUCTS_LIMIT = 20


@router.get("/")
def sales_summary(
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
    platform: Optional[str] = Query(default=None, description="Platform filter (reserved for future use)"),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Return a combined sales summary for the past *days* days.

    ``top_products`` lists the top 20 products by units sold.
    ``weekly`` aggregates revenue and units by ISO week.

    The *platform* query parameter is accepted but not yet propagated to
    the underlying analytics queries (reserved for a future platform-aware
    breakdown).

    Returns
    -------
    dict::

        {
            "top_products": [
                {"product_name": str, "material": str,
                 "total_units": int, "total_revenue": float},
                …
            ],
            "weekly": [
                {"week_label": str, "total_units": int, "total_revenue": float},
                …
            ],
        }
    """
    top = get_top_products(conn, days=days, limit=_TOP_PRODUCTS_LIMIT)
    # get_weekly_sales uses weeks, not days; convert with ceiling so that
    # days=7 → weeks=1, days=30 → weeks=5, days=365 → weeks=53.
    weeks = max(1, math.ceil(days / 7))
    weekly = get_weekly_sales(conn, weeks=weeks)
    return {"top_products": top, "weekly": weekly}


@router.get("/by-state")
def sales_by_state(
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
    material: Optional[str] = Query(default=None, description="Filter by material"),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """
    Return sales aggregated by buyer state for the past *days* days.

    Each entry::

        {
            "buyer_state":    str | None,
            "material":       str | None,
            "total_units":    int,
            "total_revenue":  float,
        }

    Sorted by total_units descending.
    """
    return get_sales_by_state(conn, days=days, material=material)


@router.get("/hoje")
def sales_hoje(
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Return today's sales totals aggregated by platform.

    Returns
    -------
    dict::

        {
            "date": "YYYY-MM-DD",
            "platforms": {
                "mercadolivre": {"orders": int, "revenue": float, "avg_ticket": float},
                "nuvemshop":    {"orders": int, "revenue": float, "avg_ticket": float},
            },
            "total": {"orders": int, "revenue": float}
        }
    """
    from datetime import datetime, timezone

    # Mostra vendas das últimas 24h para não perder pedidos feitos ao fim do dia anterior
    since = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
             .strftime("%Y-%m-%d"))

    rows = conn.execute(
        """
        SELECT platform,
               COUNT(*)                   AS orders,
               ROUND(SUM(total_value), 2) AS revenue
        FROM sales
        WHERE date(sold_at) >= date(?, '-1 day')
        GROUP BY platform
        """,
        (since,),
    ).fetchall()

    platforms: dict = {}
    for row in rows:
        plat, orders, revenue = row["platform"], row["orders"], row["revenue"] or 0.0
        platforms[plat] = {
            "orders":     orders,
            "revenue":    revenue,
            "avg_ticket": round(revenue / orders, 2) if orders else 0.0,
        }

    total_orders  = sum(p["orders"]  for p in platforms.values())
    total_revenue = sum(p["revenue"] for p in platforms.values())

    return {
        "date":      since,
        "platforms": platforms,
        "total":     {"orders": total_orders, "revenue": round(total_revenue, 2)},
    }


@router.post("/ingest")
def ingest_sales(
    body: list[dict] = Body(..., description="List of normalised sale records to upsert"),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Bulk-upsert sale records into the database.

    Duplicate ``(platform, order_id)`` pairs are silently ignored so this
    endpoint is safe to call multiple times with the same data.

    Required keys per record:
        ``platform``, ``order_id``, ``product_name``, ``unit_price``,
        ``total_value``, ``sold_at``

    Optional keys:
        ``item_id``, ``material``, ``color``, ``quantity``, ``buyer_state``

    Returns
    -------
    dict::

        {"inserted": N}   # N = number of records processed (including duplicates)
    """
    count = 0
    for sale in body:
        upsert_sale(conn, sale)
        count += 1
    return {"inserted": count}
