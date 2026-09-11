"""
api/routers/roas.py — /roas endpoints.

Exposes ROAS (Return On Ad Spend) analytics and a write path for
ingesting campaign data from platform ad integrations.

Endpoints
─────────
  GET  /roas/             → aggregate ROAS summary
  GET  /roas/campaigns    → per-campaign detail rows
  POST /roas/ingest       → bulk-upsert campaign records; returns {inserted: N}
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Depends, Query

from api.axen_deps import get_db
from axen_database import (
    get_roas_campaigns,
    get_roas_summary,
    upsert_roas_campaign,
)

router = APIRouter(prefix="/roas", tags=["roas"])


@router.get("/")
def roas_summary(
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Return aggregate ROAS metrics for the past *days* days.

    Returns
    -------
    dict::

        {
            "avg_roas":       float | None,
            "total_spend":    float,
            "total_revenue":  float,
            "campaign_count": int,
            "best_campaign":  {"campaign_name": str, "roas": float} | None,
        }

    All monetary values are in R$.  ``avg_roas`` is None when no campaign
    records exist for the requested period.
    """
    return get_roas_summary(conn, days=days)


@router.get("/campaigns")
def list_campaigns(
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """
    Return all ROAS campaign records whose ``period_start`` falls within
    the past *days* days.

    Each entry contains all ``roas_campaigns`` table columns:
    ``platform``, ``campaign_id``, ``campaign_name``, ``period_start``,
    ``period_end``, ``ad_spend``, ``attributed_revenue``, ``roas``,
    ``impressions``, ``clicks``, ``recorded_at``.

    Sorted by ``period_start`` descending.
    """
    return get_roas_campaigns(conn, days=days)


@router.post("/ingest")
def ingest_campaigns(
    body: list[dict] = Body(..., description="List of ROAS campaign records to upsert"),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Bulk-upsert ROAS campaign records.

    Existing ``(platform, campaign_id)`` records are replaced so that
    updated spend / revenue figures from a re-pull are always reflected.
    ROAS is computed automatically from ``ad_spend`` and
    ``attributed_revenue``.

    Required keys per record:
        ``platform``, ``campaign_id``, ``period_start``, ``period_end``,
        ``ad_spend``, ``attributed_revenue``

    Optional keys:
        ``campaign_name``, ``impressions``, ``clicks``

    Returns
    -------
    dict::

        {"inserted": N}   # N = number of records processed
    """
    count = 0
    for campaign in body:
        upsert_roas_campaign(conn, campaign)
        count += 1
    return {"inserted": count}
