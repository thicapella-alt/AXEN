"""
Statistical analysis and formatted output for competitive price data.
"""
import statistics
from typing import Optional

from .models import Product

MATERIALS = ["couro", "metal", "corda", "pedra"]
STORES = ["Key Design", "W. Buscatti", "Beroc"]


def analyze(products: list[Product]) -> dict[str, dict[str, dict]]:
    """
    Group products by (store, material) and compute min/avg/max/count.

    Returns:
        {store: {material: {min, avg, max, count, median}}}
    """
    groups: dict[tuple[str, str], list[float]] = {}
    for p in products:
        key = (p.store, p.material)
        groups.setdefault(key, []).append(p.price)

    result: dict[str, dict[str, dict]] = {}
    for (store, material), prices in groups.items():
        result.setdefault(store, {})[material] = {
            "min": round(min(prices), 2),
            "avg": round(statistics.mean(prices), 2),
            "median": round(statistics.median(prices), 2),
            "max": round(max(prices), 2),
            "count": len(prices),
        }
    return result


def cross_material_insights(stats: dict[str, dict[str, dict]]) -> list[str]:
    """Return a list of plain-text insight strings per material."""
    insights = []
    for material in MATERIALS:
        avgs = {
            store: data[material]["avg"]
            for store, data in stats.items()
            if material in data
        }
        if not avgs:
            insights.append(f"{material.upper()}: sem dados coletados.")
            continue

        cheapest = min(avgs, key=avgs.get)
        priciest = max(avgs, key=avgs.get)
        spread = avgs[priciest] - avgs[cheapest]
        pct = (spread / avgs[cheapest] * 100) if avgs[cheapest] else 0

        lines = [
            f"{material.upper()}",
            f"  Mais acessível : {cheapest} — média R$ {avgs[cheapest]:.2f}",
            f"  Mais premium   : {priciest} — média R$ {avgs[priciest]:.2f}",
            f"  Spread de preço: R$ {spread:.2f} ({pct:.0f}% acima do mais barato)",
        ]
        if len(avgs) >= 2:
            sorted_stores = sorted(avgs.items(), key=lambda x: x[1])
            lines.append(
                "  Ranking: " + " < ".join(f"{s} (R${v:.0f})" for s, v in sorted_stores)
            )
        insights.append("\n".join(lines))

    return insights
