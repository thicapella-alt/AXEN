"""
agents/axen_promotions_agent.py — Competitor promotion detection agent.

Detects competitor products with discount_pct ≥ 10 % from the latest
scrape run and emits one "promotion_alert" recommendation per material,
centred on the store with the highest discount in that material.

Priority mapping
────────────────
  discount ≥ 20 %  → "high"
  discount 10–19 % → "medium"

Duplicate guard
───────────────
  If a recommendation with the same title is already active in the DB,
  it is skipped — the user has not yet dismissed it, so re-alerting is
  noise.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from axen_database import get_promotions, save_recommendation
from agents.axen_base_agent import BaseAgent


class PromotionsAgent(BaseAgent):
    """
    Scans the most recent scraped prices for promotional discounts and
    creates one alert recommendation per material affected.
    """

    name = "promotions"
    description = "Detecta concorrentes com descontos ≥10 % e emite alertas de promoção"

    #: Minimum discount percentage that triggers an alert.
    MIN_DISCOUNT_PCT: int = 10

    #: Discount threshold for "high" priority (vs "medium" below this).
    HIGH_PRIORITY_THRESHOLD: int = 20

    def run(self, conn: sqlite3.Connection) -> list[dict]:
        """
        Steps:
          1. Fetch all discounted products from the last 24 h
             (get_promotions returns rows ordered by discount_pct DESC).
          2. Filter to discount_pct >= MIN_DISCOUNT_PCT.
          3. Group by material.
          4. For each material, identify the store with the highest
             maximum discount.
          5. Skip if a recommendation with the same title is already active.
          6. Persist via save_recommendation() and return created recs.
        """
        rows = get_promotions(conn, days=1)

        # Python-side minimum filter (get_promotions only guarantees > 0)
        rows = [r for r in rows if (r.get("discount_pct") or 0) >= self.MIN_DISCOUNT_PCT]
        if not rows:
            return []

        # ── Group rows by material ────────────────────────────────────────
        by_material: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_material[row["material"]].append(row)

        active = self._active_titles(conn)
        created: list[dict] = []

        for material, group in sorted(by_material.items()):
            # Find the store with the highest maximum discount in this material
            store_max: dict[str, float] = defaultdict(float)
            for item in group:
                disc = float(item.get("discount_pct") or 0)
                store_max[item["store"]] = max(store_max[item["store"]], disc)

            best_store = max(store_max, key=lambda s: store_max[s])
            max_discount = int(store_max[best_store])

            title = (
                f"Promoção detectada — {material} ({best_store}, -{max_discount}%)"
            )
            if title in active:
                continue  # already alerted and still active

            # ── Aggregate stats for this material group ───────────────────
            prices = [float(r["price"]) for r in group if r.get("price") is not None]
            min_price = min(prices) if prices else 0.0
            product_count = len(group)
            sample_urls = [r["url"] for r in group[:3] if r.get("url")]

            priority = (
                "high" if max_discount >= self.HIGH_PRIORITY_THRESHOLD else "medium"
            )

            body = (
                f"{best_store} está com {product_count} produto(s) de {material} "
                f"em promoção, com até {max_discount}% de desconto "
                f"(preço mínimo: R$ {min_price:.2f})."
            )

            data = {
                "material": material,
                "store": best_store,
                "max_discount_pct": max_discount,
                "min_price": min_price,
                "product_count": product_count,
                "sample_urls": sample_urls,
            }

            rec_id = save_recommendation(
                conn,
                type_="promotion_alert",
                priority=priority,
                title=title,
                body=body,
                material=material,
                store=best_store,
                data_json=data,
            )

            created.append(
                {
                    "id": rec_id,
                    "type": "promotion_alert",
                    "priority": priority,
                    "title": title,
                    "body": body,
                    "data": data,
                }
            )

        return created
