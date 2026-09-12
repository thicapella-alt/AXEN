#!/usr/bin/env python3
"""
scripts/export_products.py — exporta a tabela `products` pra CSV, pra
revisar/preencher os campos (custo, preço, categoria, fornecedor, código
de barras, lead times...) numa planilha.

Uso:
    cd /var/www/axen && source venv/bin/activate
    python scripts/export_products.py > produtos.csv

Depois de editar o CSV (no Excel/Google Sheets — exportar de volta como
CSV), importa as mudanças com:
    python scripts/import_products.py produtos.csv

NÃO renomeie a coluna sku_axen nas linhas existentes — é a chave que liga
esse produto aos anúncios em product_listings; renomear aqui não atualiza
lá (import_products.py ignora silenciosamente um sku_axen que não bate
com nenhum produto existente). Pra trocar um sku_axen de verdade, é um
passo separado — me chama que eu ajudo.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

COLUMNS = [
    "sku_axen", "brand", "model", "ali_name", "category", "size", "color",
    "material", "supplier", "barcode", "cost_price", "sale_price",
    "lead_time_purchase_days", "lead_time_processing_days",
    "lead_time_fulfillment_days", "target_coverage_days", "active", "notes",
]


def main() -> int:
    from axen_database import get_connection, migrate

    db_path = os.getenv("DB_PATH", "axen.db")
    conn = get_connection(db_path)
    migrate(conn)

    rows = conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM products ORDER BY brand, model, size"
    ).fetchall()
    conn.close()

    writer = csv.writer(sys.stdout)
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([row[c] if row[c] is not None else "" for c in COLUMNS])

    print(f"# {len(rows)} produto(s) exportado(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
