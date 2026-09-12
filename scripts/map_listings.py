#!/usr/bin/env python3
"""
scripts/map_listings.py — Fase 0 / item 0.3: cadastro mestre de produto + mapeamento
de anúncios do Mercado Livre.

O QUE FAZ
─────────
Lista todos os anúncios ativos do vendedor e, para cada um (uma linha por
variação, ou uma linha só para anúncios sem variação), monta:

    item_id | variation_id | título do anúncio | variação (texto) | sku_axen

Dois modos de saída, controlados por flag:

  (padrão) Só imprime a lista em TSV no stdout — pra colar numa planilha.
           sku_axen sai vazio, pra preencher manualmente.

  --write-db
           Além de imprimir, GRAVA direto no banco (tabelas `products` e
           `product_listings`, migração v3): gera um sku_axen sugerido a
           partir do título do anúncio (ex. "Axen Forge Cinza 21 Cm" →
           "FORGE-21-CIN") e das variation_attributes (SIZE/COLOR), cria a
           linha em `products` se ainda não existir, e grava/atualiza a
           linha em `product_listings`.

           Idempotente e não-destrutivo: se o listing (platform+item_id+
           variation_id) já existir, o sku_axen JÁ GRAVADO nunca é
           sobrescrito (mesmo que a sugestão recalculada seja diferente) —
           só title/variation_label/status são atualizados. Isso preserva
           qualquer correção manual feita depois.

COMO RODAR
──────────
    cd /var/www/axen && source venv/bin/activate
    python scripts/map_listings.py --write-db   # grava no banco
    python scripts/map_listings.py               # só imprime TSV (como antes)

MODO OFFLINE (reprocessar fixtures já salvos pelo spike, sem chamar a API):
    python scripts/map_listings.py --from-fixtures
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ml"

COLUMNS = ["item_id", "variation_id", "titulo", "variacao", "sku_axen"]

# Abreviações conhecidas de cor (pt-BR) — fallback é usar as 3 primeiras letras.
_COLOR_ABBR = {
    "preto": "PTO", "branco": "BCO", "prata": "PRA", "dourado": "DOU",
    "azul": "AZU", "vermelho": "VER", "verde": "VDE", "cinza": "CIN",
    "rose": "ROS", "marrom": "MRR", "amarelo": "AMA", "roxo": "RXO",
    "bege": "BEG", "laranja": "LAR",
}

_STOPWORDS = {
    "pulseira", "colar", "anel", "brinco", "masculina", "masculino",
    "feminina", "feminino", "de", "com", "em", "para", "kit",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _slug_word(s: str, length: int = 10) -> str:
    s = _strip_accents(s or "").upper()
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s[:length] or "XXX"


def _abbr_color(color: str) -> str:
    if not color:
        return ""
    words = color.strip().split()
    key = _strip_accents(words[0]).lower() if words else ""
    base = _COLOR_ABBR.get(key, _slug_word(words[0], 3)) if words else "XXX"
    if len(words) > 1:
        # cor composta (ex. "Azul Escuro") — junta a inicial da 2ª palavra
        base = base[:2] + _slug_word(words[1], 1)
    return base


def _extract_model(title: str) -> str:
    """Modelo comercial — busca o padrão 'Axen <Modelo>' no título do anúncio."""
    m = re.search(r"\bAxen\s+([A-Za-zÀ-ÿ]+)", title or "", re.IGNORECASE)
    if m:
        return _slug_word(m.group(1))
    # Fallback: primeira palavra "significativa" do título (ignora tamanho/gênero/etc.)
    for word in (title or "").split():
        w_clean = re.sub(r"[^a-zà-ÿ]", "", _strip_accents(word).lower())
        if len(w_clean) > 3 and w_clean not in _STOPWORDS:
            return _slug_word(word)
    return "PROD"


def _extract_attr(variation: dict, attr_id: str) -> str:
    for c in (variation or {}).get("attribute_combinations") or []:
        if (c.get("id") or "").upper() == attr_id:
            return (c.get("value_name") or "").strip()
    return ""


def suggest_sku(title: str, variation: dict) -> str:
    """
    Sugere um sku_axen a partir do título do anúncio + atributos da variação.
    Ex.: título "... Axen Forge Cinza 21 Cm", SIZE='21', COLOR='Cinza'
         → 'FORGE-21-CIN'.
    Heurística, não é oficial — o Thiago revisa/ajusta depois.
    """
    model = _extract_model(title)
    size = _extract_attr(variation, "SIZE")
    color = _extract_attr(variation, "COLOR")
    parts = [model]
    if size:
        parts.append(_slug_word(size, 4))
    if color:
        parts.append(_abbr_color(color))
    return "-".join(parts)


def _variation_label(variation: dict) -> str:
    """Monta o texto legível da variação a partir de attribute_combinations."""
    combos = (variation or {}).get("attribute_combinations") or []
    parts = []
    for c in combos:
        name = (c.get("name") or "").strip()
        value = (c.get("value_name") or "").strip()
        if name and value:
            parts.append(f"{name}: {value}")
        elif value:
            parts.append(value)
    return ", ".join(parts)


def rows_for_item(item: dict, suggest: bool = False) -> list[list[str]]:
    """Uma linha por variação; se não houver variações, uma linha única."""
    item_id = item.get("id", "")
    title = item.get("title", "")
    variations = item.get("variations") or []

    if not variations:
        sku = suggest_sku(title, {}) if suggest else ""
        return [[item_id, "", title, "", sku]]

    rows = []
    for v in variations:
        variation_id = str(v.get("id", ""))
        label = _variation_label(v)
        sku = suggest_sku(title, v) if suggest else ""
        rows.append([item_id, variation_id, title, label, sku])
    return rows


def fetch_live() -> list[dict]:
    """Busca item_ids + detail (incl. status) de todos os anúncios ativos, ao vivo."""
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
    return ml.get_items_detail_batch(
        all_item_ids, attributes="id,title,pictures,variations,status"
    )


def load_from_fixtures() -> list[dict]:
    """Modo offline: usa tests/fixtures/ml/items_search.json + item_detail.json."""
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


def write_to_db(items: list[dict], db_path: str) -> dict:
    """
    Grava products + product_listings no banco (migração v3).

    Idempotente: sku_axen de um listing já existente nunca é sobrescrito
    (só title/variation_label/status/imported_at são atualizados);
    products só recebe INSERT quando o sku_axen ainda não existe.
    """
    from axen_database import get_connection, migrate

    conn = get_connection(db_path)
    migrate(conn)  # garante que a v3 está aplicada antes de escrever

    now = _now_iso()
    known_skus: set[str] = {
        row["sku_axen"] for row in conn.execute("SELECT sku_axen FROM products").fetchall()
    }
    products_created = 0
    listings_new = 0
    listings_refreshed = 0

    with conn:
        for item in items:
            item_id = item.get("id", "")
            title = item.get("title", "")
            status = item.get("status", "")
            variations = item.get("variations") or [{}]  # [{}]: sem variação → 1 linha

            for v in variations:
                variation_id = str(v.get("id", "")) if v else ""
                label = _variation_label(v)

                existing = conn.execute(
                    "SELECT sku_axen FROM product_listings "
                    "WHERE platform='mercadolivre' AND item_id=? AND variation_id=?",
                    (item_id, variation_id),
                ).fetchone()

                if existing:
                    sku = existing["sku_axen"]  # nunca sobrescreve um sku já gravado
                    listings_refreshed += 1
                else:
                    base_sku = suggest_sku(title, v)
                    sku = base_sku
                    n = 2
                    while sku in known_skus:
                        sku = f"{base_sku}-{n}"
                        n += 1
                    known_skus.add(sku)
                    listings_new += 1

                conn.execute(
                    """INSERT INTO products
                       (sku_axen, model, size, color, active, notes, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 1, 'SKU sugerido automaticamente por scripts/map_listings.py — revisar', ?, ?)
                       ON CONFLICT(sku_axen) DO NOTHING""",
                    (sku, _extract_model(title), _extract_attr(v, "SIZE"), _extract_attr(v, "COLOR"), now, now),
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    products_created += 1

                conn.execute(
                    """INSERT INTO product_listings
                       (sku_axen, platform, item_id, variation_id, title, variation_label,
                        status, imported_at, created_at, updated_at)
                       VALUES (?, 'mercadolivre', ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(platform, item_id, variation_id) DO UPDATE SET
                         title=excluded.title,
                         variation_label=excluded.variation_label,
                         status=excluded.status,
                         imported_at=excluded.imported_at,
                         updated_at=excluded.updated_at""",
                    (sku, item_id, variation_id, title, label, status, now, now, now),
                )

    conn.close()
    return {
        "products_created": products_created,
        "listings_new": listings_new,
        "listings_refreshed": listings_refreshed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--from-fixtures",
        action="store_true",
        help="usa tests/fixtures/ml/*.json em vez de chamar a API ao vivo (modo de teste, dados parciais)",
    )
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="além de imprimir, grava products + product_listings no banco (migração v3)",
    )
    args = parser.parse_args()

    items = load_from_fixtures() if args.from_fixtures else fetch_live()

    print("\t".join(COLUMNS))
    total_rows = 0
    for item in items:
        for row in rows_for_item(item, suggest=args.write_db):
            print("\t".join(row))
            total_rows += 1

    print(f"# {len(items)} anúncios → {total_rows} linhas (1 por variação).", file=sys.stderr)

    if args.write_db:
        db_path = os.getenv("DB_PATH", "axen.db")
        summary = write_to_db(items, db_path)
        print(
            f"# Banco ({db_path}): {summary['products_created']} produto(s) novo(s), "
            f"{summary['listings_new']} anúncio(s) mapeado(s) pela 1ª vez, "
            f"{summary['listings_refreshed']} já existiam (sku preservado, snapshot atualizado).",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
