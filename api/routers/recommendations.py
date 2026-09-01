"""
api/routers/recommendations.py — /recommendations endpoints.

CRUD-like access to agent_recommendations: list, get by id,
dismiss, and apply.

Endpoints
─────────
  GET  /recommendations/                → filtered list
  GET  /recommendations/{rec_id}        → single record; 404 if missing
  POST /recommendations/{rec_id}/dismiss → mark dismissed; 404 if missing
  POST /recommendations/{rec_id}/apply   → mark applied;   404 if missing
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.axen_deps import get_db
from axen_database import (
    apply_recommendation,
    dismiss_recommendation,
    get_recommendation_by_id,
    get_recommendations,
)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/")
def list_recommendations(
    type_: Optional[str] = Query(default=None, alias="type", description="Filter by recommendation type"),
    priority: Optional[str] = Query(default=None, description="Filter by priority: high | medium | low"),
    active_only: bool = Query(default=True, description="When true, excludes dismissed and applied records"),
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum number of records"),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """
    Return recommendations filtered by optional query parameters.

    Results are ordered high → medium → low priority, then by creation
    date descending.  ``data_json`` is returned as a parsed dict (not a
    JSON string).
    """
    return get_recommendations(
        conn,
        type_=type_,
        priority=priority,
        active_only=active_only,
        limit=limit,
    )


@router.get("/{rec_id}")
def get_recommendation(
    rec_id: int,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Return a single recommendation by its integer ID.

    Raises HTTP 404 when the record does not exist.
    """
    rec = get_recommendation_by_id(conn, rec_id)
    if rec is None:
        raise HTTPException(
            status_code=404,
            detail=f"Recommendation {rec_id} not found",
        )
    return rec


@router.post("/{rec_id}/dismiss")
def dismiss(
    rec_id: int,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Mark a recommendation as dismissed.

    The same alert can be re-issued by the agent on its next run once
    the underlying condition is still present.

    Raises HTTP 404 when the recommendation ID does not exist.
    Returns ``{"ok": true}`` on success.
    """
    ok = dismiss_recommendation(conn, rec_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Recommendation {rec_id} not found",
        )
    return {"ok": True}


@router.post("/{rec_id}/apply")
def apply(
    rec_id: int,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Mark a recommendation as applied (action taken).

    Raises HTTP 404 when the recommendation ID does not exist.
    Returns ``{"ok": true}`` on success.
    """
    ok = apply_recommendation(conn, rec_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Recommendation {rec_id} not found",
        )
    return {"ok": True}
