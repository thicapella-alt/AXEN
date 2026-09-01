"""
api/routers/prices.py — /prices endpoints.

Exposes the competitive pricing gap between AXEN's reference prices and
the current market floor (cheapest competitor per material).

Endpoints
─────────
  GET /prices/            → all materials with market data
  GET /prices/{material}  → single material; 404 if no competitor data
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from agents.axen_pricing_agent import AXEN_PRICES
from api.axen_deps import get_db
from axen_database import get_latest_prices

router = APIRouter(prefix="/prices", tags=["prices"])


def _build_price_summary(rows: list[dict]) -> list[dict]:
    """
    Group raw price rows by material and compute the competitive gap.

    Parameters
    ----------
    rows:
        Output of get_latest_prices() — one dict per scraped product.

    Returns
    -------
    list[dict]:
        One entry per material that has both competitor data and an AXEN
        reference price::

            {
                "material":    str,
                "market_min":  float,
                "axen_ref":    float | None,
                "gap_pct":     float | None,  # None when axen_ref is None
            }

        Sorted by material name.
    """
    prices_by_material: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        mat = row.get("material")
        price = row.get("price")
        if mat and price is not None:
            prices_by_material[mat].append(float(price))

    results: list[dict] = []
    for material, prices in sorted(prices_by_material.items()):
        market_min = min(prices)
        axen_ref: Optional[float] = AXEN_PRICES.get(material)
        gap_pct: Optional[float] = None
        if axen_ref is not None and market_min > 0:
            gap_pct = round((axen_ref - market_min) / market_min * 100.0, 2)
        results.append(
            {
                "material":   material,
                "market_min": round(market_min, 2),
                "axen_ref":   axen_ref,
                "gap_pct":    gap_pct,
            }
        )
    return results


@router.get("/")
def list_prices(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    """
    Return the competitive price summary for all materials with market data.

    Each entry shows the cheapest competitor price for that material, the
    AXEN reference price, and the gap percentage (positive = AXEN is more
    expensive than the market floor).

    Returns an empty list when no scrape run has been completed yet.
    """
    rows = get_latest_prices(conn)
    return _build_price_summary(rows)


@router.get("/{material}")
def get_price_by_material(
    material: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Return the competitive price summary for a single *material*.

    Raises 404 when no competitor data exists for the requested material.
    """
    rows = get_latest_prices(conn, material=material)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No price data found for material '{material}'",
        )
    summary = _build_price_summary(rows)
    # summary will contain exactly one entry (or zero, guarded above)
    return summary[0]
