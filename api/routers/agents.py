"""
api/routers/agents.py — /agents endpoints.

Exposes the registered intelligence agents and allows running them
on demand against the live database.

Endpoints
─────────
  GET  /agents/     → list all agents with name + description
  POST /agents/run  → run one or all agents; body: {"agent": "..."}
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agents.axen_ml_position_agent import MLPositionAgent
from agents.axen_pricing_agent import PricingAgent
from agents.axen_promotions_agent import PromotionsAgent
from api.axen_deps import get_db

router = APIRouter(prefix="/agents", tags=["agents"])

# ── Registry ──────────────────────────────────────────────────────────────────
# Order matters for /run "all": agents are executed in this sequence.

_REGISTRY = [
    PricingAgent(),
    MLPositionAgent(),
    PromotionsAgent(),
]

_AGENT_MAP: dict[str, object] = {a.name: a for a in _REGISTRY}  # type: ignore[attr-defined]


# ── Request schema ────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    agent: Literal["pricing", "ml_position", "promotions", "all"] = "all"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/")
def list_agents() -> list[dict]:
    """
    Return all registered intelligence agents with their name and description.

    The list is static — it reflects the agents compiled into this build.
    """
    return [
        {"name": a.name, "description": a.description}  # type: ignore[attr-defined]
        for a in _REGISTRY
    ]


@router.post("/run")
def run_agents(
    body: RunRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """
    Run one or all agents against the live database.

    Each agent's ``run_safe()`` method is used so a crash in one agent
    does not prevent the others from executing.

    Parameters
    ----------
    body.agent:
        ``"pricing"`` | ``"ml_position"`` | ``"promotions"`` | ``"all"``

    Returns
    -------
    list[dict]:
        Concatenated list of recommendation dicts created during this run.
        Returns an empty list when no new recommendations were emitted
        (e.g. all conditions are within acceptable ranges or all matching
        recommendations are already active).
    """
    if body.agent == "all":
        agents_to_run = _REGISTRY
    else:
        agent = _AGENT_MAP.get(body.agent)
        agents_to_run = [agent] if agent else []

    results: list[dict] = []
    for agent in agents_to_run:
        results.extend(agent.run_safe(conn))  # type: ignore[attr-defined]
    return results
