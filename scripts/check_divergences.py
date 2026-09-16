#!/usr/bin/env python3
"""
scripts/check_divergences.py — motor de reconciliação estoque (S2 Parte 3).

Uso:
    cd /var/www/axen && source venv/bin/activate
    set -a; source .env; set +a
    python scripts/check_divergences.py                # janela padrão: 7 dias
    python scripts/check_divergences.py --days 14
    python scripts/check_divergences.py --only-diverging

Pra cada sku_axen com movimento nos últimos `--days` dias:
  1. Calcula o saldo do ledger (soma de stock_movements) no bucket
     relevante — 'full' se o listing usa Fulfillment (product_listings
     tem inventory_id), 'proprio' caso contrário (mapeado internamente
     pro local real 'estoque_pronto' — ver docs/movimentos-contagem-sheets.md
     §"Reconciliação com o Mercado Livre").
  2. Busca o saldo que o ML declara: GET /inventories/{id}/stock/fulfillment
     pro bucket 'full', GET /items/{id} (achando a variação certa) pro
     bucket 'proprio'.
  3. Grava em stock_snapshots (1 linha por sku_axen/local/dia — reexecutar
     no mesmo dia atualiza a linha em vez de duplicar).

Sem UI ainda (Fase 1/S6-S7) — este script é a forma de conferir por
enquanto. Retorna exit code 1 se algum SKU tiver divergência ≠ 0 (útil
pra rodar num cron e notar silenciosamente quando está tudo OK).
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("axen.check_divergences")

# stock_snapshots.local (reconciliação, só o que o ML expõe) -> local real
# em stock_movements (vocabulário completo de 8 locais). Ver Q3.
_BUCKET_TO_LEDGER_LOCAL = {"full": "full", "proprio": "estoque_pronto"}


@dataclass
class SkuSnapshot:
    sku_axen: str
    local: str                     # 'full' | 'proprio'
    saldo_ledger: int
    saldo_declarado_ml: Optional[int]
    divergencia: Optional[int]


@dataclass
class ReconcileSummary:
    skus_checked: int = 0
    skus_written: int = 0
    skus_no_listing: list[str] = field(default_factory=list)  # sem product_listings mapeado
    errors: list[str] = field(default_factory=list)
    snapshots: list[SkuSnapshot] = field(default_factory=list)


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Pure helpers ─────────────────────────────────────────────────────────────

def extract_available_quantity_from_item(item_detail: dict, variation_id: str) -> Optional[int]:
    """
    Lê o saldo declarado pelo ML pra um item/variação a partir de um
    GET /items/{id} (com variations no payload). Retorna None se a
    variação não for encontrada no item_detail atual (ex. variação
    removida do anúncio) — não adivinha.
    """
    if variation_id:
        for v in item_detail.get("variations") or []:
            if str(v.get("id")) == str(variation_id):
                return v.get("available_quantity")
        return None
    return item_detail.get("available_quantity")


def extract_available_quantity_from_inventory(inventory_stock: dict) -> Optional[int]:
    return inventory_stock.get("available_quantity")


# ── DB helpers ───────────────────────────────────────────────────────────────

def skus_with_recent_movement(conn: sqlite3.Connection, days: int = 7) -> list[str]:
    """
    SKUs com pelo menos 1 stock_movement nos últimos `days` dias.
    Compara só os 10 primeiros caracteres de `data` (YYYY-MM-DD) — o
    campo mistura formatos (planilha: 'AAAA-MM-DD HH:MM', API: ISO com
    timezone), mas os dois começam com data ISO, então o prefixo
    ordena/compara corretamente pra um filtro de janela em dias.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT DISTINCT sku_axen FROM stock_movements WHERE substr(data, 1, 10) >= ?",
        (cutoff,),
    ).fetchall()
    return [r["sku_axen"] for r in rows]


def ledger_saldo(conn: sqlite3.Connection, sku_axen: str, bucket: str) -> int:
    """Saldo atual (todos os movimentos, sem corte de data) no local real
    correspondente ao bucket de reconciliação ('full'/'proprio')."""
    local = _BUCKET_TO_LEDGER_LOCAL[bucket]
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN local_destino=? THEN quantidade ELSE 0 END), 0)
          - COALESCE(SUM(CASE WHEN local_origem=? THEN quantidade ELSE 0 END), 0)
        FROM stock_movements WHERE sku_axen=?
        """,
        (local, local, sku_axen),
    ).fetchone()
    return row[0]


def listing_for_sku(conn: sqlite3.Connection, sku_axen: str) -> Optional[sqlite3.Row]:
    """Um listing mercadolivre pra esse sku_axen (o primeiro, se houver mais de um)."""
    return conn.execute(
        "SELECT item_id, variation_id, inventory_id FROM product_listings "
        "WHERE sku_axen=? AND platform='mercadolivre' AND item_id IS NOT NULL "
        "ORDER BY updated_at DESC LIMIT 1",
        (sku_axen,),
    ).fetchone()


def _write_snapshot(conn: sqlite3.Connection, snap: SkuSnapshot, data: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO stock_snapshots (data, sku_axen, local, saldo_ledger, saldo_declarado_ml, divergencia, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(data, sku_axen, local) DO UPDATE SET
            saldo_ledger       = excluded.saldo_ledger,
            saldo_declarado_ml = excluded.saldo_declarado_ml,
            divergencia        = excluded.divergencia,
            created_at         = excluded.created_at
        """,
        (data, snap.sku_axen, snap.local, snap.saldo_ledger, snap.saldo_declarado_ml, snap.divergencia, now),
    )


# ── Motor principal ──────────────────────────────────────────────────────────

def reconcile(conn: sqlite3.Connection, ml_client, days: int = 7, dry_run: bool = False) -> ReconcileSummary:
    summary = ReconcileSummary()
    today = _today_str()

    for sku_axen in skus_with_recent_movement(conn, days=days):
        summary.skus_checked += 1
        listing = listing_for_sku(conn, sku_axen)
        if not listing:
            summary.skus_no_listing.append(sku_axen)
            continue

        item_id = listing["item_id"]
        variation_id = listing["variation_id"] or ""
        inventory_id = listing["inventory_id"]
        bucket = "full" if inventory_id else "proprio"

        saldo_ledger = ledger_saldo(conn, sku_axen, bucket)

        saldo_ml: Optional[int] = None
        try:
            if bucket == "full":
                inventory_stock = ml_client.get_inventory_stock(inventory_id)
                saldo_ml = extract_available_quantity_from_inventory(inventory_stock)
            else:
                item_detail = ml_client.get_item_detail(item_id)
                saldo_ml = extract_available_quantity_from_item(item_detail, variation_id)
        except Exception as exc:  # noqa: BLE001 — 1 SKU não pode derrubar o lote
            summary.errors.append(f"{sku_axen}: falha ao consultar ML ({exc})")

        divergencia = (saldo_ml - saldo_ledger) if saldo_ml is not None else None
        snap = SkuSnapshot(
            sku_axen=sku_axen, local=bucket,
            saldo_ledger=saldo_ledger, saldo_declarado_ml=saldo_ml, divergencia=divergencia,
        )
        summary.snapshots.append(snap)

        if not dry_run:
            try:
                _write_snapshot(conn, snap, today)
                conn.commit()
                summary.skus_written += 1
            except sqlite3.Error as exc:
                conn.rollback()
                summary.errors.append(f"{sku_axen}: falha ao gravar snapshot ({exc})")
        else:
            summary.skus_written += 1

    return summary


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_summary(s: ReconcileSummary, only_diverging: bool) -> None:
    print("\n== Reconciliação de estoque ==")
    print(f"  SKUs com movimento na janela: {s.skus_checked}")
    print(f"  snapshots gravados:           {s.skus_written}")
    if s.skus_no_listing:
        print(f"  sem listing mapeado ({len(s.skus_no_listing)}): {', '.join(s.skus_no_listing)}")
    if s.errors:
        print(f"  erros ({len(s.errors)}):")
        for e in s.errors:
            print(f"    - {e}")

    print("\n  sku_axen          local     ledger  ml    divergência")
    for snap in s.snapshots:
        if only_diverging and (snap.divergencia or 0) == 0:
            continue
        ml_str = str(snap.saldo_declarado_ml) if snap.saldo_declarado_ml is not None else "?"
        div_str = str(snap.divergencia) if snap.divergencia is not None else "?"
        flag = " ⚠" if snap.divergencia not in (0, None) else ""
        print(f"  {snap.sku_axen:<17} {snap.local:<9} {snap.saldo_ledger:<7} {ml_str:<5} {div_str}{flag}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7, help="janela de SKUs a conferir (padrão: 7)")
    parser.add_argument("--dry-run", action="store_true", help="calcula mas não grava em stock_snapshots")
    parser.add_argument("--only-diverging", action="store_true", help="só lista SKUs com divergência != 0")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from axen_database import get_connection, migrate
    from integrations.axen_mercadolivre import MercadoLivreIntegration

    conn = get_connection(os.getenv("DB_PATH", "axen.db"))
    migrate(conn)

    ml_client = MercadoLivreIntegration()
    summary = reconcile(conn, ml_client, days=args.days, dry_run=args.dry_run)

    conn.close()
    _print_summary(summary, args.only_diverging)

    any_diverging = any((s.divergencia or 0) != 0 for s in summary.snapshots)
    return 1 if any_diverging else 0


if __name__ == "__main__":
    sys.exit(main())
