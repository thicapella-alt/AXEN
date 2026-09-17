#!/usr/bin/env python3
"""
scripts/create_products.py — cadastra produtos NOVOS na tabela `products`.

Uso:
    cd /var/www/axen && source venv/bin/activate
    python scripts/create_products.py            # mostra o SQL que seria aplicado (dry-run)
    python scripts/create_products.py --apply     # aplica de verdade

Regras:
  - Só INSERE. Se o sku_axen já existir, a linha é IGNORADA com aviso
    (para atualizar um produto existente use scripts/import_products.py —
    ele nunca cria linha nova, de propósito).
  - Sem sku_axen duplicado dentro da própria lista `_NEW_PRODUCTS` — o
    script recusa rodar se achar um (evita erro de digitação silencioso).

_NEW_PRODUCTS abaixo veio da aba "Estoque" da planilha DRE AXEN, 11
variações novas com Status=Inativo (2026-09-17). Tamanho/cor conferidos
com o Thiago antes de aplicar:
  - LUCKY-VAR-*: "Var" = tamanho variável/sem medida fixa (size='Variado').
  - LUCKY-VAR-VER: "VER" = Vermelho (não Verde).
  - LOOM: a planilha tinha SKU=LOOM-19-PTO mas Tamanho=21 — Thiago confirmou
    que o tamanho (21) está certo, então o sku_axen abaixo é LOOM-21-PTO.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# (sku_axen, model, size, color)
_NEW_PRODUCTS = [
    ("BARON-185-PTO", "Baron", "18,5cm", "Preto"),
    ("BRACE-185-AZU", "Brace", "18,5cm", "Azul Escuro"),
    ("BRACE-185-PTO", "Brace", "18,5cm", "Preto"),
    ("CLIP-195-AZU", "Clip", "19,5cm", "Azul Escuro"),
    ("FORGE-19-PTO", "Forge", "19cm", "Preto"),
    ("FORGE-21-PTO", "Forge", "21cm", "Preto"),
    ("LOOM-21-PTO", "Loom", "21cm", "Preto"),
    ("LUCKY-VAR-PTO", "Lucky", "Variado", "Preto"),
    ("LUCKY-VAR-VER", "Lucky", "Variado", "Vermelho"),
    ("SHACKLE-185-PTO", "Shackle", "18,5cm", "Preto"),
    ("SHACKLE-185-MRR", "Shackle", "18,5cm", "Marrom Escuro"),
]

# Defaults documentados em axen_database.py (§ products, migração v3).
_DEFAULT_LEAD_TIME_PURCHASE_DAYS = 20
_DEFAULT_LEAD_TIME_PROCESSING_DAYS = 5
_DEFAULT_LEAD_TIME_FULFILLMENT_DAYS = 3
_DEFAULT_TARGET_COVERAGE_DAYS = 45

_INSERT_SQL = """\
INSERT INTO products (
    sku_axen, brand, model, size, color,
    lead_time_purchase_days, lead_time_processing_days, lead_time_fulfillment_days,
    target_coverage_days, active, created_at, updated_at
) VALUES (?, 'AXEN', ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="grava no banco (default: só mostra o SQL)")
    args = parser.parse_args()

    skus = [row[0] for row in _NEW_PRODUCTS]
    dupes = {s for s in skus if skus.count(s) > 1}
    if dupes:
        print(f"ERRO: sku_axen duplicado em _NEW_PRODUCTS: {', '.join(sorted(dupes))}", file=sys.stderr)
        return 1

    from axen_database import get_connection, migrate

    db_path = os.getenv("DB_PATH", "axen.db")
    conn = get_connection(db_path)
    migrate(conn)

    now = datetime.now(timezone.utc).isoformat()
    created = 0
    skipped_existing = []

    with conn:
        for sku, model, size, color in _NEW_PRODUCTS:
            existing = conn.execute("SELECT sku_axen FROM products WHERE sku_axen=?", (sku,)).fetchone()
            if existing:
                skipped_existing.append(sku)
                continue

            params = (
                sku, model, size, color,
                _DEFAULT_LEAD_TIME_PURCHASE_DAYS, _DEFAULT_LEAD_TIME_PROCESSING_DAYS,
                _DEFAULT_LEAD_TIME_FULFILLMENT_DAYS, _DEFAULT_TARGET_COVERAGE_DAYS,
                now, now,
            )
            if args.apply:
                conn.execute(_INSERT_SQL, params)
            else:
                print(f"[dry-run] {_INSERT_SQL}\n  params={params}\n")
            created += 1

    conn.close()

    action = "criado(s)" if args.apply else "seria(m) criado(s)"
    print(f"# {created} produto(s) {action}.", file=sys.stderr)
    if skipped_existing:
        print(
            f"# {len(skipped_existing)} sku_axen já existiam (ignorados): {', '.join(skipped_existing)}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
