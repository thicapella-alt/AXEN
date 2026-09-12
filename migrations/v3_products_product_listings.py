"""
migrations/v3_products_product_listings.py — PROPOSTA, NÃO APLICADA.

Fase 0 / item 0.3 do escopo pede as tabelas `products` (cadastro mestre) e
`product_listings` (mapeamento anúncio ↔ produto). Este arquivo deixa o SQL
pronto para revisão do Thiago — NÃO é chamado de lugar nenhum, `migrate()`
em axen_database.py continua rodando só v1+v2 (_SCHEMA_VERSION=2) até este
schema ser aprovado.

AVISO — Este schema foi desenhado a partir do que o item 0.3 do prompt de
abertura pede (colunas item_id | variation_id | título | variação | sku_axen
na planilha) e das convenções já usadas em axen_database.py — NÃO a partir do
escopo-automacao-axen-2026-09-07.md §5, que este spike não teve acesso ao
conteúdo (não está no repo). Reconciliar com o §5 real antes de aplicar.

Como aplicar depois de revisado (não faça isso sem o Thiago revisar antes):
  1. Copiar o corpo de `_migrate_v3()` abaixo para dentro de axen_database.py
     (mesmo lugar de `_migrate_v1`/`_migrate_v2`).
  2. Em `migrate()`, adicionar:
         if current < 3:
             _migrate_v3(conn)
  3. Trocar `_SCHEMA_VERSION = 2` para `_SCHEMA_VERSION = 3`.
  4. Rodar os testes de tests/test_axen_database.py e só then rodar migrate()
     contra o banco de produção (com backup do .db antes).

Design
──────
  products
    Cadastro mestre AXEN — uma linha por produto "canônico" da empresa,
    independente de em quantas plataformas/anúncios ele aparece.
    `sku_axen` é a chave que o Thiago preenche manualmente na planilha
    (aba "Anúncios ML" da DRE AXEN) — por isso fica UNIQUE mas nullable até
    ser preenchido (linha pode existir em product_listings sem produto
    mestre ainda vinculado).

  product_listings
    Uma linha por (plataforma, item_id, variation_id) — ou seja, por anúncio
    ou por variação de anúncio. `product_id` liga pro cadastro mestre quando
    o Thiago já mapeou o sku_axen; até lá fica NULL (staging). `sku_axen`
    também fica denormalizado aqui como conveniência de import direto da
    planilha (scripts/map_listings.py gera as colunas nessa mesma forma),
    evitando um JOIN pra toda leitura simples de "que sku é esse anúncio".
    title/variation_label são um snapshot do que veio da API no momento do
    mapeamento — não é para ficar sincronizado automaticamente com o ML;
    se mudar o título lá, precisa rodar o script de novo pra atualizar.
"""

from __future__ import annotations

import sqlite3


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """Version 3 (PROPOSTA) — cadastro mestre de produto + mapeamento de anúncios."""
    with conn:
        conn.executescript("""
            -- ── products ───────────────────────────────────────────────────────
            -- Cadastro mestre AXEN. Uma linha por produto canônico da empresa.
            CREATE TABLE IF NOT EXISTS products (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sku_axen        TEXT    UNIQUE,            -- preenchido manualmente pelo Thiago
                name            TEXT    NOT NULL,
                material        TEXT,                      -- mesmo vocabulário de prices/sales
                color           TEXT,
                active          INTEGER NOT NULL DEFAULT 1, -- 0/1
                notes           TEXT,
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_products_sku_axen
                ON products (sku_axen);
            CREATE INDEX IF NOT EXISTS idx_products_active
                ON products (active);

            -- ── product_listings ──────────────────────────────────────────────
            -- Mapeamento anúncio (ou variação de anúncio) ↔ produto mestre.
            -- Gerada/atualizada por scripts/map_listings.py + import manual do
            -- sku_axen preenchido na planilha DRE AXEN.
            CREATE TABLE IF NOT EXISTS product_listings (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id      INTEGER REFERENCES products(id),  -- NULL até mapear o sku_axen
                platform        TEXT    NOT NULL,          -- 'mercadolivre' | 'nuvemshop' | ...
                item_id         TEXT    NOT NULL,           -- 'MLB123456789' ou id da outra plataforma
                variation_id    TEXT    NOT NULL DEFAULT '',-- '' quando o anúncio não tem variação
                sku_axen        TEXT,                       -- denormalizado — cópia de products.sku_axen
                title           TEXT    NOT NULL DEFAULT '',      -- snapshot do título do anúncio
                variation_label TEXT    NOT NULL DEFAULT '',      -- snapshot do texto da variação (ex. "Cor: Preto")
                inventory_id    TEXT,                       -- inventory_id Full/Fulfillment, se houver
                imported_at     TEXT    NOT NULL,            -- quando este snapshot foi gerado
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                UNIQUE (platform, item_id, variation_id)
            );

            CREATE INDEX IF NOT EXISTS idx_product_listings_product
                ON product_listings (product_id);
            CREATE INDEX IF NOT EXISTS idx_product_listings_sku_axen
                ON product_listings (sku_axen);
            CREATE INDEX IF NOT EXISTS idx_product_listings_platform_item
                ON product_listings (platform, item_id);

            PRAGMA user_version = 3;
        """)
