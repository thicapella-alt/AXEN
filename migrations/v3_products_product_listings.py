"""
migrations/v3_products_product_listings.py — PROPOSTA, NÃO APLICADA.

Fase 0 / item 0.3 do escopo: tabelas `products` (cadastro mestre) e
`product_listings` (mapeamento anúncio ↔ produto). Este arquivo deixa o SQL
pronto para revisão do Thiago — NÃO é chamado de lugar nenhum, `migrate()`
em axen_database.py continua rodando só v1+v2 (_SCHEMA_VERSION=2) até este
schema ser aprovado e aplicado.

Revisado contra escopo-automacao-axen-2026-09-07.md v1.3 §5 (tabela
`products`/`product_listings`) + respostas do Thiago em 12/09/2026:
  - Campos oficiais do §5: sku_axen, modelo comercial, nome Ali, tamanho,
    cor, material, custo unitário, lead times, cobertura alvo, ativo.
  - Adicionados a pedido do Thiago: preço de venda, categoria, fornecedor,
    código de barras.
  - `sku_axen` vira a PRIMARY KEY de `products` (chave natural), não um id
    substituto — bate com D10 do escopo ("chave de produto: sku_axen
    interno") e com o fato de que TODAS as tabelas futuras da Fase 1+
    (order_items, stock_movements, dre_daily…) referenciam sku_axen
    diretamente, não um id interno.
  - Nuvemshop (Fase 1.5): nenhuma mudança de schema necessária — já cabe
    via `product_listings.platform = 'nuvemshop'` apontando pro mesmo
    sku_axen (mesmo mecanismo do D4/D10).
  - `product_listings.status` adicionado (estava no §5: "título; status" —
    eu tinha esquecido na primeira versão).

Como aplicar depois de aprovado (não faça isso sem o Thiago confirmar):
  1. Copiar o corpo de `_migrate_v3()` abaixo para dentro de axen_database.py
     (mesmo lugar de `_migrate_v1`/`_migrate_v2`).
  2. Em `migrate()`, adicionar:
         if current < 3:
             _migrate_v3(conn)
  3. Trocar `_SCHEMA_VERSION = 2` para `_SCHEMA_VERSION = 3`.
  4. Rodar os testes de tests/test_axen_database.py e só então rodar
     migrate() contra o banco de produção (com backup do .db antes).

Design
──────
  products
    Cadastro mestre AXEN. `sku_axen` é a PRIMARY KEY (chave natural, ex.
    'DRIFT-185-AZE') — é o Thiago quem define/confirma esse valor (na
    planilha, ou aceitando a sugestão automática do scripts/map_listings.py).
    Uma linha por produto "canônico" — modelo comercial + tamanho + cor —
    independente de em quantas plataformas ele é vendido.

  product_listings
    Uma linha por (plataforma, item_id, variation_id) — anúncio ou variação
    de anúncio. `sku_axen` referencia products(sku_axen) e fica NULL até o
    Thiago mapear (staging) — o coletor de vendas (Fase 1) nunca adivinha:
    venda com listing não mapeado grava sku_axen=NULL e alerta no relatório
    D-1 (D10/§12 do escopo). `title`/`variation_label`/`status` são um
    snapshot do que veio da API no momento do mapeamento — não ficam
    sincronizados automaticamente; rodar scripts/map_listings.py de novo
    atualiza.
"""

from __future__ import annotations

import sqlite3


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """Version 3 (PROPOSTA) — cadastro mestre de produto + mapeamento de anúncios."""
    with conn:
        conn.executescript("""
            -- ── products ───────────────────────────────────────────────────────
            -- Cadastro mestre AXEN. sku_axen é a chave natural (ex. 'DRIFT-185-AZE'),
            -- definida pelo Thiago. Campos conforme escopo §5 + adicionais pedidos.
            CREATE TABLE IF NOT EXISTS products (
                sku_axen                    TEXT    PRIMARY KEY,        -- chave AXEN, ex. 'DRIFT-185-AZE'
                model                       TEXT    NOT NULL,           -- modelo comercial (Drift, Clip, Anchor…)
                ali_name                    TEXT,                       -- nome/título do produto no fornecedor
                category                    TEXT,                       -- categoria (ex. 'pulseira', 'colar')
                size                        TEXT,                       -- tamanho (ex. '18cm', '21cm')
                color                       TEXT,
                material                    TEXT,
                supplier                    TEXT,                       -- fornecedor (ex. 'AliExpress', nome da loja)
                barcode                     TEXT,                       -- código de barras / EAN
                cost_price                  REAL,                       -- custo unitário atual (R$)
                sale_price                  REAL,                       -- preço de venda de referência (R$)
                lead_time_purchase_days     INTEGER,                    -- compra/fornecedor (padrão 20)
                lead_time_processing_days   INTEGER,                    -- acabamento/gravação a laser (padrão 5)
                lead_time_fulfillment_days  INTEGER,                    -- envio ao Full (padrão 3)
                target_coverage_days        INTEGER,                    -- cobertura alvo em dias (padrão 45 — usado no ROP, §8.2)
                active                      INTEGER NOT NULL DEFAULT 1, -- 0/1
                notes                       TEXT,
                created_at                  TEXT    NOT NULL,
                updated_at                  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_products_model
                ON products (model);
            CREATE INDEX IF NOT EXISTS idx_products_active
                ON products (active);
            CREATE INDEX IF NOT EXISTS idx_products_barcode
                ON products (barcode);

            -- ── product_listings ──────────────────────────────────────────────
            -- Mapeamento anúncio (ou variação de anúncio) ↔ produto mestre.
            -- Gerada/atualizada por scripts/map_listings.py + import manual do
            -- sku_axen confirmado/ajustado pelo Thiago na planilha.
            CREATE TABLE IF NOT EXISTS product_listings (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sku_axen        TEXT    REFERENCES products(sku_axen),  -- NULL até mapear
                platform        TEXT    NOT NULL,          -- 'mercadolivre' | 'nuvemshop' | ...
                item_id         TEXT    NOT NULL,           -- 'MLB123456789' ou id da outra plataforma
                variation_id    TEXT    NOT NULL DEFAULT '',-- '' quando o anúncio não tem variação
                title           TEXT    NOT NULL DEFAULT '',      -- snapshot do título do anúncio
                variation_label TEXT    NOT NULL DEFAULT '',      -- snapshot do texto da variação (ex. "Cor: Preto")
                status          TEXT,                       -- status do anúncio na plataforma (active/paused/closed…)
                inventory_id    TEXT,                       -- inventory_id Full/Fulfillment, se houver
                imported_at     TEXT    NOT NULL,            -- quando este snapshot foi gerado
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                UNIQUE (platform, item_id, variation_id)
            );

            CREATE INDEX IF NOT EXISTS idx_product_listings_sku_axen
                ON product_listings (sku_axen);
            CREATE INDEX IF NOT EXISTS idx_product_listings_platform_item
                ON product_listings (platform, item_id);
            CREATE INDEX IF NOT EXISTS idx_product_listings_status
                ON product_listings (status);

            PRAGMA user_version = 3;
        """)
