#!/usr/bin/env python3
"""
scripts/collect_orders.py — coletor de pedidos do Mercado Livre (S2 Parte 2).

Uso:
    cd /var/www/axen && source venv/bin/activate
    set -a; source .env; set +a
    python scripts/collect_orders.py                # janela padrão: 7 dias
    python scripts/collect_orders.py --days 14
    python scripts/collect_orders.py --dry-run        # só mostra o que faria

Fluxo (por pedido, dentro da janela de `--days`)
─────────────────────────────────────────────────
  1. GET /orders/search (paginado) — lista de pedidos no período, upsert
     por order_id (nunca duplica; reexecutar sobrescreve status/valores
     com a versão mais atual).
  2. GET /orders/{id} — detalhe autoritativo do pedido.
  3. GET /shipments/{shipment_id} — resolve tipo_envio ('full'/'proprio')
     via logistic_type e custo_frete.
  4. Cada item do pedido tem seu sku_axen resolvido via product_listings
     (item_id, variation_id) → sku_axen. Sem mapeamento = NULL, registrado
     como alerta — nunca inventa.
  5. Pra pedidos com status='paid' (critério documentado — não exige
     entrega, ver README/S2), cada item com sku_axen resolvido gera um
     movimento `venda_ml` em stock_movements:
       local_origem = 'full'           se tipo_envio == 'full'
       local_origem = 'estoque_pronto' se tipo_envio == 'proprio'
                                        (mapeia o "casa" do escopo pro
                                        bucket real do vocabulário — ver
                                        docs/movimentos-contagem-sheets.md)
       local_destino = 'cliente'
     dedupe_key = "api:<order_id>:<item_id>:<variation_id>" — idempotente,
     reexecutar não duplica.
  6. Pedido cancelado DEPOIS de já ter gerado venda_ml: gap conhecido,
     não reverte automaticamente nesta sessão (documentado, decisão do
     Thiago em 17/09/2026).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("axen.collect_orders")


@dataclass
class CollectSummary:
    orders_seen: int = 0
    orders_upserted: int = 0
    items_upserted: int = 0
    movements_created: int = 0
    movements_already_existed: int = 0
    unresolved_sku_alerts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Pure extraction helpers (fáceis de testar sem rede/DB) ──────────────────────

def resolve_tipo_envio(shipment_detail: Optional[dict]) -> Optional[str]:
    """
    'full' quando logistic_type == 'fulfillment' (único valor confirmado no
    spike — tests/fixtures/ml/shipment_detail.redacted.json). Qualquer outro
    valor observado vira 'proprio' — não temos um fixture de envio próprio
    pra confirmar o valor exato, mas 'fulfillment' é o único caso especial
    documentado pela API pra Full. None se não há shipment_detail (não
    adivinha).
    """
    if not shipment_detail:
        return None
    logistic_type = shipment_detail.get("logistic_type")
    if logistic_type is None:
        return None
    return "full" if logistic_type == "fulfillment" else "proprio"


def extract_order_fields(order_detail: dict, shipment_detail: Optional[dict]) -> dict:
    """Monta o dict de colunas de `orders` a partir do order_detail bruto
    (+ shipment_detail, se disponível). Não toca banco nem rede."""
    order_id = str(order_detail.get("id"))
    items = order_detail.get("order_items") or []

    tarifa_ml = sum(float(it.get("sale_fee") or 0) for it in items)
    descontos = sum(
        (float(it["gross_price"]) - float(it.get("unit_price") or 0)) * int(it.get("quantity") or 1)
        for it in items
        if it.get("gross_price") is not None
    )

    tipo_envio = resolve_tipo_envio(shipment_detail)
    custo_frete = None
    if shipment_detail:
        shipping_option = shipment_detail.get("shipping_option") or {}
        custo_frete = shipping_option.get("list_cost")

    receita_bruta = float(order_detail.get("total_amount") or 0)
    custo_frete_calc = float(custo_frete) if custo_frete is not None else 0.0
    receita_liquida = receita_bruta - tarifa_ml - custo_frete_calc

    status = order_detail.get("status", "")
    shipping = order_detail.get("shipping") or {}

    return {
        "order_id": order_id,
        "date_created": order_detail.get("date_created", ""),
        "status": status,
        "canal": (order_detail.get("context") or {}).get("channel", ""),
        "tags": json.dumps(order_detail.get("tags") or []),
        "shipment_id": str(shipping["id"]) if shipping.get("id") is not None else None,
        "tipo_envio": tipo_envio,
        "receita_bruta": receita_bruta,
        "tarifa_ml": tarifa_ml,
        "custo_frete": custo_frete,
        "descontos": descontos,
        "cancelado": 1 if status == "cancelled" else 0,
        "reembolsado": 1 if _has_refund(order_detail) else 0,
        "receita_liquida": receita_liquida,
        "raw_json": json.dumps(order_detail),
    }


def _has_refund(order_detail: dict) -> bool:
    """Best-effort: algum pagamento com status/status_detail indicando
    reembolso. Não confirmado contra um pedido reembolsado real (nenhum
    fixture do spike tinha um) — heurística simples, revisar se aparecer
    caso real divergente."""
    for payment in order_detail.get("payments") or []:
        status = (payment.get("status") or "").lower()
        detail = (payment.get("status_detail") or "").lower()
        if "refund" in status or "refund" in detail or "charged_back" in status:
            return True
    return False


def extract_item_fields(order_detail: dict) -> list[dict]:
    """Monta a lista de linhas de order_items a partir do order_detail bruto."""
    order_id = str(order_detail.get("id"))
    rows = []
    for it in order_detail.get("order_items") or []:
        item = it.get("item") or {}
        variation_id = item.get("variation_id")
        rows.append({
            "order_id": order_id,
            "item_id": item.get("id", ""),
            "variation_id": str(variation_id) if variation_id is not None else "",
            "quantidade": int(it.get("quantity") or 0),
            "preco_unitario": float(it.get("unit_price") or 0),
        })
    return rows


# ── DB helpers ───────────────────────────────────────────────────────────────

def resolve_sku_axen(conn: sqlite3.Connection, item_id: str, variation_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT sku_axen FROM product_listings WHERE platform='mercadolivre' AND item_id=? AND variation_id=?",
        (item_id, variation_id),
    ).fetchone()
    return row["sku_axen"] if row and row["sku_axen"] else None


def _upsert_order(conn: sqlite3.Connection, fields: dict) -> None:
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO orders (
            order_id, date_created, status, canal, tags, shipment_id, tipo_envio,
            receita_bruta, tarifa_ml, custo_frete, descontos, cancelado, reembolsado,
            receita_liquida, raw_json, ingested_at, updated_at
        ) VALUES (
            :order_id, :date_created, :status, :canal, :tags, :shipment_id, :tipo_envio,
            :receita_bruta, :tarifa_ml, :custo_frete, :descontos, :cancelado, :reembolsado,
            :receita_liquida, :raw_json, :ingested_at, :updated_at
        )
        ON CONFLICT(order_id) DO UPDATE SET
            status           = excluded.status,
            canal            = excluded.canal,
            tags             = excluded.tags,
            shipment_id      = excluded.shipment_id,
            tipo_envio       = excluded.tipo_envio,
            receita_bruta    = excluded.receita_bruta,
            tarifa_ml        = excluded.tarifa_ml,
            custo_frete      = excluded.custo_frete,
            descontos        = excluded.descontos,
            cancelado        = excluded.cancelado,
            reembolsado      = excluded.reembolsado,
            receita_liquida  = excluded.receita_liquida,
            raw_json         = excluded.raw_json,
            updated_at       = excluded.updated_at
        """,
        {**fields, "ingested_at": now, "updated_at": now},
    )


def _upsert_item(conn: sqlite3.Connection, sku_axen: Optional[str], **fields) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO order_items (order_id, item_id, variation_id, sku_axen, quantidade, preco_unitario)
        VALUES (:order_id, :item_id, :variation_id, :sku_axen, :quantidade, :preco_unitario)
        """,
        {**fields, "sku_axen": sku_axen},
    )


def _create_venda_ml_movement(
    conn: sqlite3.Connection, *, order_id: str, item_id: str, variation_id: str,
    sku_axen: str, quantidade: int, tipo_envio: Optional[str], data: str,
) -> bool:
    """Retorna True se um movimento novo foi gravado, False se já existia
    (dedupe) ou se quantidade<=0 (nada a gravar)."""
    if quantidade <= 0:
        return False
    dedupe_key = f"api:{order_id}:{item_id}:{variation_id}"
    if conn.execute("SELECT 1 FROM stock_movements WHERE dedupe_key=?", (dedupe_key,)).fetchone():
        return False

    local_origem = "full" if tipo_envio == "full" else "estoque_pronto"
    conn.execute(
        """
        INSERT INTO stock_movements (
            data, sku_axen, tipo, quantidade, local_origem, local_destino,
            referencia, observacao, fonte, dedupe_key, ingested_at
        ) VALUES (?, ?, 'venda_ml', ?, ?, 'cliente', ?, '', 'api', ?, ?)
        """,
        (data, sku_axen, quantidade, local_origem, order_id, dedupe_key, _now_iso()),
    )
    return True


# ── Coletor principal ────────────────────────────────────────────────────────

def collect_orders(conn: sqlite3.Connection, ml_client, days: int = 7, dry_run: bool = False) -> CollectSummary:
    summary = CollectSummary()

    order_summaries = ml_client.get_paginated_orders_search(days=days)
    summary.orders_seen = len(order_summaries)

    for order_summary in order_summaries:
        order_id = str(order_summary.get("id"))
        try:
            order_detail = ml_client.get_order_detail(order_id)
        except Exception as exc:  # noqa: BLE001 — coletor não pode cair por 1 pedido
            summary.errors.append(f"pedido {order_id}: falha ao buscar order_detail ({exc})")
            continue

        shipping = order_detail.get("shipping") or {}
        shipment_id = shipping.get("id")
        shipment_detail = None
        if shipment_id:
            try:
                shipment_detail = ml_client.get_shipment_detail(shipment_id)
            except Exception as exc:  # noqa: BLE001
                summary.errors.append(f"pedido {order_id}: falha ao buscar shipment_detail ({exc})")

        order_fields = extract_order_fields(order_detail, shipment_detail)
        item_rows = extract_item_fields(order_detail)

        if dry_run:
            log.info("[dry-run] pedido %s: status=%s, %d item(ns)", order_id, order_fields["status"], len(item_rows))
            summary.orders_upserted += 1
            summary.items_upserted += len(item_rows)
            continue

        try:
            _upsert_order(conn, order_fields)
            summary.orders_upserted += 1

            for item in item_rows:
                sku_axen = resolve_sku_axen(conn, item["item_id"], item["variation_id"])
                if not sku_axen:
                    summary.unresolved_sku_alerts.append(
                        f"pedido {order_id}: item_id={item['item_id']} variation_id={item['variation_id']!r} "
                        f"sem mapeamento em product_listings — sku_axen=NULL"
                    )
                _upsert_item(conn, sku_axen, **item)
                summary.items_upserted += 1

                if order_fields["status"] == "paid" and sku_axen:
                    created = _create_venda_ml_movement(
                        conn,
                        order_id=order_id,
                        item_id=item["item_id"],
                        variation_id=item["variation_id"],
                        sku_axen=sku_axen,
                        quantidade=item["quantidade"],
                        tipo_envio=order_fields["tipo_envio"],
                        data=order_fields["date_created"],
                    )
                    if created:
                        summary.movements_created += 1
                    else:
                        summary.movements_already_existed += 1

            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            summary.errors.append(f"pedido {order_id}: erro ao gravar no banco ({exc})")

    return summary


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_summary(s: CollectSummary) -> None:
    print("\n== Coletor de pedidos ==")
    print(f"  pedidos encontrados:      {s.orders_seen}")
    print(f"  pedidos gravados/atualiz: {s.orders_upserted}")
    print(f"  itens gravados:           {s.items_upserted}")
    print(f"  movimentos venda_ml novos: {s.movements_created}")
    print(f"  movimentos já existiam:   {s.movements_already_existed}")
    if s.unresolved_sku_alerts:
        print(f"  alertas — sku_axen não resolvido ({len(s.unresolved_sku_alerts)}):")
        for a in s.unresolved_sku_alerts:
            print(f"    - {a}")
    if s.errors:
        print(f"  erros ({len(s.errors)}):")
        for e in s.errors:
            print(f"    - {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7, help="janela de busca em dias (padrão: 7)")
    parser.add_argument("--dry-run", action="store_true", help="mostra o que faria, sem gravar no banco")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from axen_database import get_connection, migrate
    from integrations.axen_mercadolivre import MercadoLivreIntegration

    conn = get_connection(os.getenv("DB_PATH", "axen.db"))
    migrate(conn)

    ml_client = MercadoLivreIntegration()
    summary = collect_orders(conn, ml_client, days=args.days, dry_run=args.dry_run)

    conn.close()
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
