#!/usr/bin/env python3
"""
scripts/import_products.py — lê o CSV editado (gerado por
scripts/export_products.py) e atualiza a tabela `products` no banco.

Uso:
    cd /var/www/axen && source venv/bin/activate
    python scripts/import_products.py produtos.csv          # aplica de verdade
    python scripts/import_products.py produtos.csv --dry-run # só mostra o que mudaria

Regras:
  - sku_axen identifica a linha — NUNCA é atualizado por este script (é a
    chave que product_listings usa pra apontar pro produto). Uma linha do
    CSV cujo sku_axen não existir no banco é IGNORADA com aviso (não cria
    produto novo — isso é trabalho do scripts/map_listings.py).
  - Campos numéricos vazios no CSV viram NULL no banco (ex. apagar o custo
    de um produto que ainda não foi precificado).
  - `active` aceita 1/0, true/false, sim/não (case-insensitive).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_TEXT_COLUMNS = [
    "brand", "model", "ali_name", "category", "size", "color",
    "material", "supplier", "barcode", "notes",
]
_NUMERIC_COLUMNS = [
    "cost_price", "sale_price", "lead_time_purchase_days",
    "lead_time_processing_days", "lead_time_fulfillment_days",
    "target_coverage_days",
]

_TRUE_VALUES = {"1", "true", "sim", "yes", "ativo"}
_FALSE_VALUES = {"0", "false", "nao", "não", "no", "inativo"}


def _parse_active(value: str, default: int) -> int:
    v = (value or "").strip().lower()
    if v in _TRUE_VALUES:
        return 1
    if v in _FALSE_VALUES:
        return 0
    return default


def _parse_numeric(value: str):
    """Aceita tanto '18.50' quanto '18,50' (vírgula é comum em planilha BR)."""
    v = (value or "").strip().replace(",", ".")
    if not v:
        return None
    try:
        return float(v) if "." in v else int(v)
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", help="CSV editado (mesmas colunas do export_products.py)")
    parser.add_argument("--dry-run", action="store_true", help="mostra o que mudaria sem gravar")
    args = parser.parse_args()

    from axen_database import get_connection, migrate

    db_path = os.getenv("DB_PATH", "axen.db")
    conn = get_connection(db_path)
    migrate(conn)

    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    skipped_not_found = []

    with open(args.csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    with conn:
        for row in rows:
            sku = (row.get("sku_axen") or "").strip()
            if not sku:
                continue

            existing = conn.execute(
                "SELECT sku_axen FROM products WHERE sku_axen=?", (sku,)
            ).fetchone()
            if not existing:
                skipped_not_found.append(sku)
                continue

            values = {}
            for col in _TEXT_COLUMNS:
                values[col] = (row.get(col) or "").strip() or None
            for col in _NUMERIC_COLUMNS:
                values[col] = _parse_numeric(row.get(col, ""))
            values["active"] = _parse_active(row.get("active", ""), default=1)

            if args.dry_run:
                print(f"[dry-run] atualizaria {sku}: {values}")
            else:
                conn.execute(
                    """UPDATE products SET
                         brand=?, model=?, ali_name=?, category=?, size=?, color=?,
                         material=?, supplier=?, barcode=?, cost_price=?, sale_price=?,
                         lead_time_purchase_days=?, lead_time_processing_days=?,
                         lead_time_fulfillment_days=?, target_coverage_days=?,
                         active=?, notes=?, updated_at=?
                       WHERE sku_axen=?""",
                    (
                        values["brand"], values["model"], values["ali_name"], values["category"],
                        values["size"], values["color"], values["material"], values["supplier"],
                        values["barcode"], values["cost_price"], values["sale_price"],
                        values["lead_time_purchase_days"], values["lead_time_processing_days"],
                        values["lead_time_fulfillment_days"], values["target_coverage_days"],
                        values["active"], values["notes"], now, sku,
                    ),
                )
            updated += 1

    conn.close()

    action = "seriam atualizados" if args.dry_run else "atualizados"
    print(f"# {updated} produto(s) {action}.", file=sys.stderr)
    if skipped_not_found:
        print(
            f"# {len(skipped_not_found)} sku_axen do CSV não encontrados no banco (ignorados): "
            f"{', '.join(skipped_not_found)}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
