"""
agents/axen_pricing_agent.py — Competitive pricing gap detection agent.

Compares AXEN's reference selling prices against the current market floor
(cheapest competitor price per material) and emits a "price_suggestion"
recommendation whenever AXEN is more than 10 % above the market floor.

Only materials that AXEN actively sells are evaluated.  Materials with a
None reference price (e.g. "pedra", which AXEN does not stock) are skipped.

AXEN reference prices
─────────────────────
These mirror the values in axen_price_scraper.py (AXEN_PRICES).
Update both files whenever AXEN's actual selling price changes.

Priority mapping
────────────────
  gap_pct > 25 %  → "high"   (significant competitive disadvantage)
  gap_pct 10–25 % → "medium" (worth monitoring / minor adjustment)

Duplicate guard
───────────────
  Recommendations are skipped if a matching title is already active.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Optional

from axen_database import get_latest_prices, get_price_changes, save_recommendation
from agents.axen_base_agent import BaseAgent


# ─────────────────────────────────────────────
#  AXEN REFERENCE PRICES
#  Single selling price per material (R$).
#  None → AXEN does not currently sell this material.
# ─────────────────────────────────────────────

AXEN_PRICES: dict[str, Optional[float]] = {
    "corda": 125.0,
    "metal": 215.0,
    "couro": 215.0,
    "pedra": None,   # not stocked — excluded from analysis
}


class PricingAgent(BaseAgent):
    """
    Detects materials where competitors undercut AXEN's reference price
    by more than GAP_THRESHOLD_PCT and emits price adjustment suggestions.
    """

    name = "pricing"
    description = "Sugere ajustes de preço AXEN baseados em variações competitivas"

    #: Minimum gap (%) between AXEN reference and market floor to trigger alert.
    GAP_THRESHOLD_PCT: float = 10.0

    #: Gap (%) above which the alert is escalated to "high" priority.
    HIGH_PRIORITY_GAP_PCT: float = 25.0

    #: How many days back to look for recent price changes (context data).
    PRICE_CHANGE_DAYS: int = 7

    #: Minimum |change_pct| to count as a "significant" recent price change.
    PRICE_CHANGE_MIN_PCT: float = 5.0

    def run(self, conn: sqlite3.Connection) -> list[dict]:
        """
        Steps:
          1. Fetch all prices from the latest scrape run.
          2. Fetch recent significant price changes (context only).
          3. For each material that AXEN sells and that has market data:
               market_min  = min competitor price
               axen_ref    = AXEN_PRICES[material]
               gap_pct     = (axen_ref − market_min) / market_min × 100
          4. If gap_pct > GAP_THRESHOLD_PCT → emit alert.
          5. Duplicate guard, persist, return.
        """
        all_prices = get_latest_prices(conn)
        if not all_prices:
            return []

        # Recent price changes — used only as context in the data payload
        recent_changes = get_price_changes(
            conn,
            days=self.PRICE_CHANGE_DAYS,
            min_pct=self.PRICE_CHANGE_MIN_PCT,
        )

        # Count recent changes per material
        changes_by_material: dict[str, int] = defaultdict(int)
        for chg in recent_changes:
            changes_by_material[chg["material"]] += 1

        # Group market prices by material
        prices_by_material: dict[str, list[float]] = defaultdict(list)
        for row in all_prices:
            mat = row.get("material")
            price = row.get("price")
            if mat and price is not None:
                prices_by_material[mat].append(float(price))

        active = self._active_titles(conn)
        created: list[dict] = []

        for material, axen_ref in AXEN_PRICES.items():
            if axen_ref is None:
                continue  # AXEN does not sell this material

            market_prices = prices_by_material.get(material)
            if not market_prices:
                continue  # no competitor data for this material

            market_min = min(market_prices)
            if market_min <= 0:
                continue  # guard against zero / invalid prices

            gap_pct = (axen_ref - market_min) / market_min * 100.0

            if gap_pct <= self.GAP_THRESHOLD_PCT:
                continue  # within acceptable range

            title = f"Preço AXEN acima do mercado — {material}"
            if title in active:
                continue

            priority = (
                "high" if gap_pct > self.HIGH_PRIORITY_GAP_PCT else "medium"
            )

            body = (
                f"O preço de referência AXEN para {material} (R$ {axen_ref:.2f}) "
                f"está {gap_pct:.1f}% acima do menor preço do mercado "
                f"(R$ {market_min:.2f}). "
                f"Considere revisar o posicionamento de preço."
            )

            price_changes_count = changes_by_material.get(material, 0)
            data = {
                "material": material,
                "axen_ref_price": axen_ref,
                "market_min": market_min,
                "gap_pct": round(gap_pct, 2),
                "price_changes_count": price_changes_count,
            }

            rec_id = save_recommendation(
                conn,
                type_="price_suggestion",
                priority=priority,
                title=title,
                body=body,
                material=material,
                suggested_value=market_min,
                data_json=data,
            )

            created.append(
                {
                    "id": rec_id,
                    "type": "price_suggestion",
                    "priority": priority,
                    "title": title,
                    "body": body,
                    "data": data,
                }
            )

        return created
