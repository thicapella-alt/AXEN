"""
api/routers/tarefas.py — Tarefas do dia consolidadas.

Agrega em tempo real:
  1. Pedidos ML aguardando envio (order.status=paid)
  2. Perguntas ML sem resposta
  3. Campanhas com ROAS < 1 (lidos do banco)

Endpoint
────────
  GET /tarefas/hoje
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from api.axen_deps import get_db

router = APIRouter(prefix="/tarefas", tags=["tarefas"])
log = logging.getLogger(__name__)

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _fmtBRL(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_date(iso: str) -> str:
    if not iso or len(iso) < 10:
        return iso
    y, m, d = iso[:10].split("-")
    return f"{d}/{m}/{y}"


@router.get("/hoje")
def tarefas_hoje(conn=Depends(get_db)) -> dict:
    """
    Retorna lista consolidada de tarefas pendentes do dia.

    Tipos possíveis:
      - pedido_envio   → pedido ML pago aguardando envio
      - pergunta_ml    → pergunta de comprador sem resposta
      - roas_critico   → campanha com ROAS < 1x
    """
    tasks: list[dict] = []
    errors: list[str] = []

    # ── 1. Pedidos ML aguardando envio ────────────────────────────────────────
    try:
        from integrations.axen_base_integration import is_enabled
        if is_enabled("mercadolivre"):
            from integrations.axen_mercadolivre import MercadoLivreIntegration
            ml = MercadoLivreIntegration()

            for order in ml.get_pending_orders(days=7):
                parts = [order["buyer_name"], order["product_title"]]
                desc  = " — ".join(p for p in parts if p)
                if order["total"]:
                    desc += f" — {_fmtBRL(order['total'])}"
                if order["date_created"]:
                    desc += f" — pago em {_fmt_date(order['date_created'])}"

                tasks.append({
                    "id":          f"ml_order_{order['order_id']}",
                    "type":        "pedido_envio",
                    "priority":    "high",
                    "platform":    "mercadolivre",
                    "title":       f"Enviar pedido #{order['order_id']}",
                    "description": desc,
                    "action_url":  order["action_url"],
                })

            # ── 2. Perguntas ML sem resposta ──────────────────────────────────
            for q in ml.get_unanswered_questions():
                tasks.append({
                    "id":          f"ml_question_{q['question_id']}",
                    "type":        "pergunta_ml",
                    "priority":    "medium",
                    "platform":    "mercadolivre",
                    "title":       "Pergunta sem resposta no ML",
                    "description": q["text"][:150] if q["text"] else "(sem texto)",
                    "action_url":  q["action_url"],
                })

    except Exception as exc:
        log.warning("[tarefas] Erro ao consultar ML: %s", exc)
        errors.append(f"ML: {exc}")

    # ── 3. ROAS crítico (< 1x) ────────────────────────────────────────────────
    try:
        from axen_database import get_roas_campaigns
        for c in get_roas_campaigns(conn, days=30):
            if (c.get("roas") or 1.0) < 1.0:
                spend   = c.get("ad_spend", 0) or 0
                revenue = c.get("attributed_revenue", 0) or 0
                tasks.append({
                    "id":          f"roas_{c.get('campaign_id', '')}",
                    "type":        "roas_critico",
                    "priority":    "high",
                    "platform":    c.get("platform", ""),
                    "title":       f"ROAS crítico — {c.get('campaign_name') or c.get('campaign_id', '')}",
                    "description": (
                        f"ROAS: {c.get('roas', 0):.2f}x — "
                        f"Gasto: {_fmtBRL(spend)} — "
                        f"Receita: {_fmtBRL(revenue)}"
                    ),
                    "action_url":  None,
                })
    except Exception as exc:
        log.warning("[tarefas] Erro ao ler ROAS: %s", exc)
        errors.append(f"ROAS: {exc}")

    # ── Ordenação: high → medium → low, depois por tipo ──────────────────────
    tasks.sort(key=lambda t: (_PRIORITY_ORDER.get(t["priority"], 99), t["type"]))

    return {
        "total":  len(tasks),
        "tasks":  tasks,
        "errors": errors,
    }
