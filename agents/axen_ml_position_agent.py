"""
agents/axen_ml_position_agent.py — Mercado Livre position-drop detection agent.

Analyses the per-item position trend over the past 7 days and emits one
"ml_position_drop" recommendation for each item whose ranking has drifted
by ≥ 5 positions (drift = max_position − min_position).

A drift of 5 means the item's worst recorded position was at least 5 spots
further back than its best — a signal of ranking instability that warrants
investigation (title changes, listing quality, competitor bids, etc.).

Priority mapping
────────────────
  max_position ≥ 20  → "high"   (slipped out of first page on Mercado Livre)
  max_position < 20  → "medium" (still on page 1 but trending down)

Duplicate guard
───────────────
  Recommendations are skipped if a matching title is already active.
"""

from __future__ import annotations

import sqlite3

from axen_database import get_ml_position_trend, save_recommendation
from agents.axen_base_agent import BaseAgent


class MLPositionAgent(BaseAgent):
    """
    Detects Mercado Livre listing position drops and emits ranking alerts.
    """

    name = "ml_position"
    description = "Detecta quedas de posição nos anúncios do Mercado Livre"

    #: Minimum number of scrape runs an item must appear in before it is
    #: considered for trend analysis (single-run items have no trend).
    MIN_RUNS: int = 2

    #: Minimum drift (max_position − min_position) that triggers an alert.
    MIN_DRIFT: int = 5

    #: max_position threshold above which the alert is escalated to "high".
    HIGH_PRIORITY_MAX_POSITION: int = 20

    #: How many days of history to analyse.
    LOOKBACK_DAYS: int = 7

    def run(self, conn: sqlite3.Connection) -> list[dict]:
        """
        Steps:
          1. Fetch per-item position trend for the past LOOKBACK_DAYS days.
          2. Keep only items with n_runs >= MIN_RUNS (stable trend data).
          3. Compute drift = max_position − min_position.
          4. Emit one alert per item with drift >= MIN_DRIFT.
          5. Skip titles already active in the DB.
          6. Persist via save_recommendation() and return created recs.
        """
        trend_rows = get_ml_position_trend(conn, days=self.LOOKBACK_DAYS)

        # Filter: need at least two observations for a meaningful trend
        trend_rows = [r for r in trend_rows if (r.get("n_runs") or 0) >= self.MIN_RUNS]
        if not trend_rows:
            return []

        active = self._active_titles(conn)
        created: list[dict] = []

        for row in trend_rows:
            item_id = row["item_id"]
            material = row["material"]
            query = row.get("query") or ""
            min_pos = int(row["min_position"])
            max_pos = int(row["max_position"])
            avg_pos = float(row["avg_position"])
            n_runs = int(row["n_runs"])
            drift = max_pos - min_pos

            if drift < self.MIN_DRIFT:
                continue

            title = f"Queda de posição ML — {material} (item {item_id})"
            if title in active:
                continue

            priority = (
                "high" if max_pos >= self.HIGH_PRIORITY_MAX_POSITION else "medium"
            )

            # Use the clean query slug for readability in the body
            readable_query = query.replace("_", " ") if query else "—"
            body = (
                f"O item {item_id} ({material}, query \"{readable_query}\") "
                f"passou da posição {min_pos} para {max_pos} "
                f"nos últimos {self.LOOKBACK_DAYS} dias "
                f"(média: {avg_pos:.1f}, {n_runs} execuções)."
            )

            data = {
                "item_id": item_id,
                "query": query,
                "material": material,
                "min_position": min_pos,
                "max_position": max_pos,
                "avg_position": avg_pos,
                "n_runs": n_runs,
                "drift": drift,
            }

            rec_id = save_recommendation(
                conn,
                type_="ml_position_drop",
                priority=priority,
                title=title,
                body=body,
                material=material,
                data_json=data,
            )

            created.append(
                {
                    "id": rec_id,
                    "type": "ml_position_drop",
                    "priority": priority,
                    "title": title,
                    "body": body,
                    "data": data,
                }
            )

        return created
