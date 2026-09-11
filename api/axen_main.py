"""
api/axen_main.py — AXEN Intelligence API entry point.

Run with:
    uvicorn api.axen_main:app --reload --port 8000

Or from the project root:
    python -m uvicorn api.axen_main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import agents, auth_ml, competitors, integrations, prices, recommendations, roas, sales, sync, tarefas, visits

# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="AXEN Intelligence API",
    version="1.0.0",
    description=(
        "Commercial intelligence API for AXEN men's bracelet brand. "
        "Aggregates competitor pricing, ML position tracking, promotions "
        "and sales data into actionable recommendations."
    ),
)

# ── CORS — permissive for local development ───────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(prices.router)
app.include_router(competitors.router)
app.include_router(recommendations.router)
app.include_router(agents.router)
app.include_router(integrations.router)
app.include_router(sales.router)
app.include_router(roas.router)
app.include_router(sync.router)
app.include_router(tarefas.router)
app.include_router(visits.router)
app.include_router(auth_ml.router)


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/healthz", tags=["meta"])
def healthz() -> dict:
    """Liveness probe — always returns 200 when the process is up."""
    return {"status": "ok", "version": "1.0.0"}
