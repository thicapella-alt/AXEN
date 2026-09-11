#!/usr/bin/env python3
"""
scripts/map_listings.py — Fase 0 / item 0.3: cadastro mestre de produto + mapeamento
de anúncios do Mercado Livre.

O QUE FAZ
─────────
Lista todos os anúncios ativos do vendedor e, para cada um (uma linha por
variação, ou uma linha só para anúncios sem variação), imprime:

    item_id | variation_id | título do anúncio | variação (texto) | sku_axen

Formatado como TSV (separado por tab) para colar direto numa planilha —
sku_axen sempre vazio, é o Thiago quem preenche manualmente na aba
"Anúncios ML" da planilha DRE AXEN. Este script NÃO escreve em planilha
nenhuma, só imprime no stdout.

COMO RODAR
──────────
No VPS de produção (onde ml_tokens.json e as env vars ML_* já existem):

    cd /var/www/axen
    source venv/bin/activate
    python scripts/map_listings.py > anuncios_ml.tsv

Ou direto para a área de transferência via SSH, ex.:
    ssh vps "cd /var/www/axen && python scripts/map_listings.py" | pbcopy

MODO OFFLINE (reprocessar fixtures já salvos pelo spike, sem chamar a API):
    python scripts/map_listings.py --from-fixtures

    Usa tests/fixtures/ml/items_search.json (lista de IDs) e, se existir,
    tests/fixtures/ml/item_detail.json (só tem 1 item — útil pra testar o
    script, não para gerar a planilha completa).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ml"

COLUMNS = ["item_id", "variation_id", "titulo", "variacao", "sku_axen"]


def _variation_label(variation: dict) -> str:
    """
    Monta o texto legível da variação a partir de attribute_combinations,
    ex.: "Cor: Preto, Tamanho: M". Cai pra string vazia se não achar nada.
    """
    combos = variation.get("attribute_combinations") or []
    parts = []
    for c in combos:
        name = (c.get("name") or "").strip()
        value = (c.get("value_name") or "").strip()
        if name and value:
            parts.append(f"{name}: {value}")
        elif value:
            parts.append(value)
    return ", ".join(parts)


def rows_for_item(item: dict) -> list[list[str]]:
    """Uma linha por variação; se não houver variações, uma linha única."""
    item_id = item.get("id", "")
    title = item.get("title", "")
    variations = item.get("variations") or []

    if not variations:
        return [[item_id, "", title, "", ""]]

    rows = []
    for v in variations:
        variation_id = str(v.get("id", ""))
        label = _variation_label(v)
        rows.append([item_id, variation_id, title, label, ""])
    return rows


def fetch_live() -> list[dict]:
    """Busca item_ids + detail de todos os anúncios ativos, direto na API."""
    from integrations.axen_mercadolivre import MercadoLivreIntegration

    ml = MercadoLivreIntegration()

    all_item_ids: list[str] = []
    offset = 0
    limit = 100
    while True:
        raw = ml._get_authed(
            f"/users/{ml._seller_id}/items/search",
            limit=limit,
            offset=offset,
        )
        ids = raw.get("results", []) if isinstance(raw, dict) else []
        if not ids:
            break
        all_item_ids.extend(ids)
        paging = raw.get("paging", {}) if isinstance(raw, dict) else {}
        total = paging.get("total", len(all_item_ids))
        offset += limit
        if offset >= total:
            break

    print(f"# {len(all_item_ids)} anúncios ativos encontrados.", file=sys.stderr)
    return ml.get_items_detail_batch(all_item_ids, attributes="id,title,pictures,variations")


def load_from_fixtures() -> list[dict]:
    """
    Modo offline: usa tests/fixtures/ml/items_search.json (IDs) +
    tests/fixtures/ml/item_detail.json (só o item de exemplo do spike).
    Serve para testar o script, não para gerar a planilha completa —
    a fixture do spike só tem 1 item por design (item 0.2).
    """
    items_search_path = FIXTURES_DIR / "items_search.json"
    item_detail_path = FIXTURES_DIR / "item_detail.json"

    if not items_search_path.exists():
        print(f"# aviso: {items_search_path} não existe — rode scripts/ml_spike.py no VPS primeiro.", file=sys.stderr)
        return []

    items_search = json.loads(items_search_path.read_text(encoding="utf-8"))
    ids = items_search.get("results", []) if isinstance(items_search, dict) else []
    print(f"# {len(ids)} IDs em items_search.json (modo offline só tem detail do 1º item — {item_detail_path.name}).", file=sys.stderr)

    if item_detail_path.exists():
        return [json.loads(item_detail_path.read_text(encoding="utf-8"))]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--from-fixtures",
        action="store_true",
        help="usa tests/fixtures/ml/*.json em vez de chamar a API ao vivo (modo de teste, dados parciais)",
    )
    args = parser.parse_args()

    items = load_from_fixtures() if args.from_fixtures else fetch_live()

    print("\t".join(COLUMNS))
    total_rows = 0
    for item in items:
        for row in rows_for_item(item):
            print("\t".join(row))
            total_rows += 1

    print(f"# {len(items)} anúncios → {total_rows} linhas (1 por variação).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
