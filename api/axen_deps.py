"""
api/axen_deps.py — FastAPI dependency providers for AXEN Intelligence API.

All providers are designed for dependency injection via FastAPI's
Depends() mechanism.  Tests override them using app.dependency_overrides
so no real database or integrations are needed in the test suite.

Providers
─────────
  get_db()            → yields a SQLite connection (WAL mode, FK enforcement)
  get_ml_integration() → returns MercadoLivreIntegration or None
"""

from __future__ import annotations

import os
import sqlite3
from typing import Generator

from axen_database import get_connection, migrate
from integrations.axen_base_integration import IntegrationDisabledError
from integrations.axen_mercadolivre import MercadoLivreIntegration


# ── Database ──────────────────────────────────────────────────────────────────

def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Yield a ready-to-use SQLite connection for the lifetime of one request.

    The database path is read from the ``DB_PATH`` environment variable;
    defaults to ``axen.db`` in the current working directory when unset.

    The schema is migrated on every connection so that a freshly deployed
    instance self-heals without a separate migration step.

    Yields
    ------
    sqlite3.Connection
        WAL-mode, FK-enforced connection with row_factory set to
        sqlite3.Row (handled by get_connection / migrate).
    """
    db_path = os.getenv("DB_PATH", "axen.db")
    conn = get_connection(db_path)
    migrate(conn)
    try:
        yield conn
    finally:
        conn.close()


# ── Integrations ──────────────────────────────────────────────────────────────

def get_ml_integration() -> MercadoLivreIntegration | None:
    """
    Return a live MercadoLivreIntegration when the feature flag is on.

    Returns ``None`` when ``MERCADOLIVRE_ENABLED`` is not ``"true"``.
    The caller is responsible for handling the ``None`` case (e.g. returning
    an empty list or a 503 response).
    """
    try:
        return MercadoLivreIntegration()
    except IntegrationDisabledError:
        return None
