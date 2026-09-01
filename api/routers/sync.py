"""
api/routers/sync.py — Pipeline de sincronização completa.

Endpoints
─────────
  POST /sync/run     → dispara scraper + agentes + vendas ML em background
  GET  /sync/status  → retorna estado atual e resultado da última execução
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends

from api.axen_deps import get_db
from axen_database import get_connection, migrate

router = APIRouter(prefix="/sync", tags=["sync"])

# ── Estado global da sincronização ────────────────────────────────────────────

_lock = threading.Lock()

_state: dict = {
    "status":     "idle",       # idle | running | done | error
    "step":       None,         # etapa atual
    "started_at": None,
    "finished_at": None,
    "result":     None,         # resumo do último run
    "error":      None,
}

_STEPS = [
    "scraper",    # coleta preços dos concorrentes
    "agentes",    # pricing + ml_position + promotions
    "vendas_ml",  # ingest vendas do Mercado Livre
]


def _set(**kwargs):
    with _lock:
        _state.update(kwargs)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Pipeline em background ────────────────────────────────────────────────────

def _run_pipeline():
    _set(
        status="running",
        step="scraper",
        started_at=_now(),
        finished_at=None,
        result=None,
        error=None,
    )

    result = {
        "scraper":          {"status": "skipped", "products": 0},
        "agentes":          {"status": "skipped", "recommendations": 0},
        "vendas_ml":        {"status": "skipped", "orders": 0},
        "vendas_nuvemshop": {"status": "skipped", "orders": 0},
        "visitas_ml":       {"status": "skipped", "records": 0},
        "visitas_nuvemshop": {"status": "skipped", "records": 0},
    }

    try:
        # ── Etapa 1: Scraper ──────────────────────────────────────────────────
        _set(step="scraper")
        try:
            from axen_scheduler import run_pipeline as _scraper
            r = _scraper()
            result["scraper"] = {
                "status":   r.get("status", "done"),
                "products": r.get("products_count", 0),
            }
        except Exception as e:
            result["scraper"] = {"status": "error", "error": str(e)}

        # ── Etapa 2: Agentes ──────────────────────────────────────────────────
        _set(step="agentes")
        try:
            from agents.axen_pricing_agent import PricingAgent
            from agents.axen_ml_position_agent import MLPositionAgent
            from agents.axen_promotions_agent import PromotionsAgent

            db_path = __import__("os").getenv("DB_PATH", "axen.db")
            conn = get_connection(db_path)
            migrate(conn)

            recs = []
            for AgentClass in [PricingAgent, MLPositionAgent, PromotionsAgent]:
                try:
                    recs.extend(AgentClass().run_safe(conn))
                except Exception:
                    pass
            conn.close()

            result["agentes"] = {"status": "done", "recommendations": len(recs)}
        except Exception as e:
            result["agentes"] = {"status": "error", "error": str(e)}

        # ── Etapa 3: Vendas ML ────────────────────────────────────────────────
        _set(step="vendas_ml")
        try:
            from integrations.axen_base_integration import IntegrationDisabledError, is_enabled
            if not is_enabled("mercadolivre"):
                result["vendas_ml"] = {"status": "disabled", "orders": 0}
            else:
                from integrations.axen_mercadolivre import MercadoLivreIntegration
                from axen_database import upsert_sale

                db_path = __import__("os").getenv("DB_PATH", "axen.db")
                conn = get_connection(db_path)
                migrate(conn)

                sales = MercadoLivreIntegration().get_sales_report(days=30)
                count = 0
                for sale in sales:
                    try:
                        upsert_sale(conn, {
                            "platform":     "mercadolivre",
                            "order_id":     str(sale.get("order_id", "")),
                            "product_name": sale.get("items", [{}])[0].get("title", "") if sale.get("items") else "",
                            "unit_price":   sale.get("items", [{}])[0].get("unit_price", 0) if sale.get("items") else 0,
                            "total_value":  sale.get("total_amount", 0),
                            "sold_at":      sale.get("date_created", ""),
                            "quantity":     sale.get("items", [{}])[0].get("quantity", 1) if sale.get("items") else 1,
                        })
                        count += 1
                    except Exception:
                        pass
                conn.close()
                result["vendas_ml"] = {"status": "done", "orders": count}
        except Exception as e:
            result["vendas_ml"] = {"status": "error", "error": str(e)}

        # ── Etapa 4: Vendas Nuvemshop ─────────────────────────────────────────
        _set(step="vendas_nuvemshop")
        try:
            from integrations.axen_nuvemshop_scraper import is_configured, get_orders
            from axen_database import upsert_sale

            if not is_configured():
                result["vendas_nuvemshop"] = {"status": "disabled", "orders": 0}
            else:
                db_path = __import__("os").getenv("DB_PATH", "axen.db")
                conn = get_connection(db_path)
                migrate(conn)

                orders = get_orders(days=30)
                count = 0
                for order in orders:
                    try:
                        if order.get("order_id") and order.get("sold_at"):
                            upsert_sale(conn, order)
                            count += 1
                    except Exception:
                        pass
                conn.close()
                result["vendas_nuvemshop"] = {"status": "done", "orders": count}
        except Exception as e:
            result["vendas_nuvemshop"] = {"status": "error", "error": str(e)}

        # ── Etapa 5: Visitas ML ───────────────────────────────────────────────
        _set(step="visitas_ml")
        try:
            from integrations.axen_base_integration import is_enabled
            if not is_enabled("mercadolivre"):
                result["visitas_ml"] = {"status": "disabled", "records": 0}
            else:
                from integrations.axen_mercadolivre import MercadoLivreIntegration
                from axen_database import upsert_visit

                db_path = __import__("os").getenv("DB_PATH", "axen.db")
                conn = get_connection(db_path)
                migrate(conn)

                records = MercadoLivreIntegration().get_visits_report(days=30)
                count = 0
                for rec in records:
                    try:
                        upsert_visit(conn, rec)
                        count += 1
                    except Exception:
                        pass
                conn.close()
                result["visitas_ml"] = {"status": "done", "records": count}
        except Exception as e:
            result["visitas_ml"] = {"status": "error", "error": str(e)}

        # ── Etapa 6: Visitas Nuvemshop ────────────────────────────────────────
        _set(step="visitas_nuvemshop")
        try:
            from integrations.axen_nuvemshop_scraper import is_configured, get_analytics
            from axen_database import upsert_visit

            if not is_configured():
                result["visitas_nuvemshop"] = {"status": "disabled", "records": 0}
            else:
                db_path = __import__("os").getenv("DB_PATH", "axen.db")
                conn = get_connection(db_path)
                migrate(conn)

                records = get_analytics(days=30)
                count = 0
                for rec in records:
                    try:
                        upsert_visit(conn, rec)
                        count += 1
                    except Exception:
                        pass
                conn.close()
                result["visitas_nuvemshop"] = {"status": "done", "records": count}
        except Exception as e:
            result["visitas_nuvemshop"] = {"status": "error", "error": str(e)}

        _set(status="done", step=None, finished_at=_now(), result=result)

    except Exception as e:
        _set(status="error", step=None, finished_at=_now(), error=str(e), result=result)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/run")
def sync_run() -> dict:
    """
    Dispara o pipeline completo em background.
    Se já estiver rodando, retorna o estado atual sem iniciar novo pipeline.
    """
    with _lock:
        if _state["status"] == "running":
            return {"started": False, "message": "Sincronização já em andamento.", **_state}

    thread = threading.Thread(target=_run_pipeline, daemon=True)
    thread.start()
    return {"started": True, "message": "Sincronização iniciada."}


@router.get("/status")
def sync_status() -> dict:
    """Retorna o estado atual do pipeline e o resultado da última execução."""
    with _lock:
        return dict(_state)
