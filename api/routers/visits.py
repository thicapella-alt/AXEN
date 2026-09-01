"""
api/routers/visits.py — Histórico de visitas por plataforma.

Endpoints
─────────
  GET /visits/historico?days=30
      Retorna séries diárias de visitas agrupadas por plataforma.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from api.axen_deps import get_db

router = APIRouter(prefix="/visits", tags=["visits"])


@router.get("/historico")
def visits_historico(days: int = 30, conn=Depends(get_db)) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    rows = conn.execute(
        """
        SELECT platform, source, date, visits, visitors,
               add_to_cart, reached_checkout, purchased, listing_title
        FROM visits
        WHERE date >= ?
        ORDER BY date ASC
        """,
        (cutoff,),
    ).fetchall()

    # ── Aggregate por plataforma × data ──────────────────────────────────────
    by_platform: dict[str, dict[str, dict]] = defaultdict(dict)

    for r in rows:
        platform = r["platform"]
        date     = r["date"]

        if date not in by_platform[platform]:
            by_platform[platform][date] = {
                "date":            date,
                "visits":          0,
                "visitors":        0,
                "add_to_cart":     0,
                "reached_checkout": 0,
                "purchased":       0,
            }

        day = by_platform[platform][date]
        day["visits"]           += int(r["visits"] or 0)
        day["visitors"]         += int(r["visitors"] or 0)
        day["add_to_cart"]      += int(r["add_to_cart"] or 0)
        day["reached_checkout"] += int(r["reached_checkout"] or 0)
        day["purchased"]        += int(r["purchased"] or 0)

    # ── Summary por plataforma ────────────────────────────────────────────────
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary: dict[str, dict] = {}

    for platform, days_map in by_platform.items():
        all_days = list(days_map.values())
        total_visits = sum(d["visits"] for d in all_days)
        today_visits = days_map.get(today, {}).get("visits", 0)

        entry: dict = {"total_visits": total_visits, "today": today_visits}

        if platform == "nuvemshop":
            total_purchased = sum(d["purchased"] for d in all_days)
            conv_rate = round(total_purchased / total_visits * 100, 2) if total_visits else 0
            entry["conversion_rate"] = conv_rate

        summary[platform] = entry

    # ── Séries temporais ──────────────────────────────────────────────────────
    series = [
        {
            "platform": platform,
            "data":     sorted(days_map.values(), key=lambda x: x["date"]),
        }
        for platform, days_map in by_platform.items()
    ]

    return {
        "days":    days,
        "series":  series,
        "summary": summary,
    }


@router.get("/anuncios-ml")
def visits_anuncios_ml(days: int = 30, conn=Depends(get_db)) -> dict:
    """Retorna listings ML com visitas + vendas para a página de Anúncios."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    visits_rows = conn.execute(
        """
        SELECT source,
               COALESCE(MAX(listing_title), source) AS title,
               SUM(visits)                           AS total_visits
        FROM visits
        WHERE platform = 'mercadolivre' AND date >= ?
        GROUP BY source
        ORDER BY total_visits DESC
        """,
        (cutoff,),
    ).fetchall()

    sales_rows = conn.execute(
        """
        SELECT item_id,
               COUNT(*)                   AS orders,
               ROUND(SUM(total_value), 2) AS revenue
        FROM sales
        WHERE platform = 'mercadolivre' AND sold_at >= ?
        GROUP BY item_id
        """,
        (cutoff,),
    ).fetchall()
    sales_map = {r["item_id"]: r for r in sales_rows}

    listings = []
    for r in visits_rows:
        s = sales_map.get(r["source"], {})
        listings.append({
            "item_id":      r["source"],
            "title":        r["title"],
            "visits":       r["total_visits"],
            "orders":       s.get("orders", 0) if s else 0,
            "revenue":      s.get("revenue", 0.0) if s else 0.0,
            "url":          f"https://www.mercadolivre.com.br/anuncios/{r['source']}",
        })

    return {"days": days, "listings": listings}


@router.get("/por-modelo")
def visits_por_modelo(days: int = 30, conn=Depends(get_db)) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    # Agora usamos /items/{id}/visits/time_window que retorna breakdown diário real.
    # Cada registro é um dia distinto → SUM é correto (visitas únicas no período).
    rows = conn.execute(
        """
        SELECT source,
               COALESCE(MAX(listing_title), source) AS title,
               SUM(visits)                           AS total
        FROM visits
        WHERE platform = 'mercadolivre' AND date >= ?
        GROUP BY source
        ORDER BY total DESC
        """,
        (cutoff,),
    ).fetchall()

    return {
        "days": days,
        "modelos": [
            {
                "item_id": r["source"],
                "title":   r["title"],
                "visits":  r["total"],
            }
            for r in rows
        ],
    }
