"""
api/routers/competitors.py — /competitors endpoints.

Exposes competitor product data collected by the scraper.

Endpoints
─────────
  GET /competitors/                         → latest scrape run products
  GET /competitors/history?material=&days=  → price-change history
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.axen_deps import get_db
from axen_database import get_latest_prices, get_price_changes

router = APIRouter(prefix="/competitors", tags=["competitors"])


@router.get("/")
def list_competitors(
    material: Optional[str] = Query(default=None, description="Filter by material"),
    store: Optional[str] = Query(default=None, description="Filter by store name"),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """
    Return all products from the most recent completed scrape run.

    Optionally filter by *material* and/or *store*.
    Returns an empty list when no scrape run exists.

    Each entry::

        {
            "store":         str,
            "name":          str,
            "price":         float,
            "material":      str,
            "url":           str,
            "delivery_info": str,
            "discount_pct":  float | None,
            "scraped_at":    str,
        }
    """
    rows = get_latest_prices(conn, material=material, store=store)
    return [
        {
            "store":        row["store"],
            "name":         row["product_name"],
            "price":        float(row["price"]),
            "material":     row["material"],
            "url":          row.get("url", ""),
            "delivery_info": row.get("delivery_info", ""),
            "discount_pct": row.get("discount_pct"),
            "scraped_at":   row.get("scraped_at", ""),
        }
        for row in rows
    ]


@router.get("/history")
def price_history(
    material: Optional[str] = Query(default=None, description="Filter by material"),
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """
    Return price-change events detected in the past *days* days.

    A price-change event is recorded when a product's price differs from
    its previous scrape run price by any amount (min_pct=0 returns all
    changes regardless of size).

    Each entry::

        {
            "store":        str,
            "material":     str,
            "price_before": float,
            "price_after":  float,
            "change_pct":   float,
            "changed_at":   str,
        }
    """
    changes = get_price_changes(conn, days=days, min_pct=0.0)

    if material:
        changes = [c for c in changes if c.get("material") == material]

    return [
        {
            "store":        c["store"],
            "material":     c["material"],
            "price_before": float(c["price_before"]),
            "price_after":  float(c["price_after"]),
            "change_pct":   float(c["change_pct"]),
            "changed_at":   c.get("detected_at", ""),
        }
        for c in changes
    ]
