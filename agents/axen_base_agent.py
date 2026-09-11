"""
agents/axen_base_agent.py — Abstract base class for AXEN intelligence agents.

Agents are stateless read+write workers:
  - They receive a live SQLite connection.
  - They query the DB for signals (price changes, promotions, ML positions …).
  - They emit actionable recommendations via save_recommendation().
  - They never perform HTTP requests or scraping.
  - They are called by the scheduler after each ingest_scrape_run().

Subclass contract
─────────────────
  class MyAgent(BaseAgent):
      name        = "my_agent"           # machine-readable id (lower_snake)
      description = "What it detects"   # shown in CLI / dashboard

      def run(self, conn: sqlite3.Connection) -> list[dict]:
          ...  # query DB, call save_recommendation(), return list of created recs

Usage
─────
  agent = MyAgent()
  recs   = agent.run_safe(conn)   # never raises; returns [] on any error
"""

from __future__ import annotations

import logging
import sqlite3
from abc import ABC, abstractmethod

from axen_database import get_recommendations, save_recommendation

log = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Abstract base for all AXEN intelligence agents.

    Class attributes (must be defined on every subclass):
        name        : str — machine-readable identifier, e.g. "promotions"
        description : str — one-line description shown in the UI

    Each recommendation returned by run() must be a dict with keys:
        id       : int   — DB row id assigned by save_recommendation()
        type     : str   — recommendation type, e.g. "promotion_alert"
        priority : str   — "high" | "medium" | "low"
        title    : str   — ≤ 80 chars, used as the UI card header
        body     : str   — full explanatory text
        data     : dict  — agent-specific payload (JSON-serialisable)
    """

    name: str = ""
    description: str = ""

    @abstractmethod
    def run(self, conn: sqlite3.Connection) -> list[dict]:
        """
        Analyse the database and emit recommendations.

        Implementations must:
          1. Query the DB using helpers from axen_database.
          2. Check _active_titles(conn) to avoid duplicate inserts.
          3. Call save_recommendation() for each new recommendation.
          4. Return a list of dicts (one per recommendation created).

        This method may raise — callers should use run_safe() instead.
        """
        ...

    def run_safe(self, conn: sqlite3.Connection) -> list[dict]:
        """
        Safe wrapper around run().

        Catches all exceptions, logs them, and returns [] — never raises.
        Use this in the scheduler pipeline so a buggy agent cannot abort the run.
        """
        try:
            return self.run(conn)
        except Exception as exc:
            log.exception("[%s] Agent crashed: %s", self.name or type(self).__name__, exc)
            return []

    def _active_titles(self, conn: sqlite3.Connection) -> set[str]:
        """
        Return the set of titles belonging to currently active recommendations
        (i.e. not yet dismissed or applied).

        Subclasses use this to skip duplicate inserts:

            active = self._active_titles(conn)
            if title in active:
                continue  # already alerted — do not re-insert

        A high limit (10 000) is used to capture the full active set; the
        recommendations table is expected to be small in practice.
        """
        rows = get_recommendations(conn, active_only=True, limit=10_000)
        return {r["title"] for r in rows}
