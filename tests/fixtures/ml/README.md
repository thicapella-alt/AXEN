# tests/fixtures/ml/ — Spike de API do Mercado Livre (Fase 0, item 0.2)

Gerado por `scripts/ml_spike.py` em 2026-09-12 01:25:59 UTC.

IDs usados nesta rodada: order_id=`2000018415699106` · shipment_id=`47994433273` · item_id=`MLB6882560628` · inventory_id=`PJVF21758`

## Resultado por endpoint

| Fixture | Endpoint | Status | HTTP | Notas |
|---|---|---|---|---|
| `orders_search` | `GET /orders/search` | ✅ OK | 200 |  |
| `order_detail` | `GET /orders/2000018415699106` | ✅ OK | 200 |  |
| `shipment_detail` | `GET /shipments/47994433273` | ✅ OK | 200 |  |
| `items_search` | `GET /users/{seller_id}/items/search` | ✅ OK | 200 |  |
| `item_detail` | `GET /items/MLB6882560628?attributes=id,title,pictures,variations` | ✅ OK | 200 |  |
| `item_variations` | `(campo dentro de GET /items/{id})` | ✅ OK | 200 | 12 variação(ões) já vêm em item_detail.variations |
| `inventory_stock` | `GET /inventories/PJVF21758/stock/fulfillment` | ✅ OK | 200 |  |
| `visits_time_window` | `GET /items/MLB6882560628/visits/time_window` | ✅ OK | 200 |  |
| `claims_search` | `GET /post-purchase/v1/claims/search` | ❌ ERRO | 400 | body: {"code":400,"error":"bad_request_error","message":"Invalid parameters. At least submit some of these filters: [resource and resource_id] [player_role and player_user_id] [player_role and player_user_id and resource]","cause":null} |
| `questions_search` | `GET /questions/search` | ✅ OK | 200 |  |
| `advertising_campaigns` | `GET /advertising/advertisers/{seller_id}/product_ads/campaigns/search` | ❌ ERRO | 404 | body: {"error":"not_found","message":"No static resource advertising/advertisers/92293099/product_ads/campaigns/search.","status":404} |

## Erros de autorização (401/403)

Nenhum 401/403 registrado nesta rodada (ou os passos que falharam falharam por outro motivo — ver tabela acima).

## Divergência 'venda por publicidade' (order_detail vs. painel de Vendas)

Ver claude/diagnostico-meli-vendas-ads-2026-08-31.md §11 e claude/checkpoint-meli-09-09.md
para o contexto completo da divergência já registrada no projeto.

Campos candidatos encontrados em order_detail (comparar com o painel de Vendas):

  - `tags = ["order_has_discount", "paid", "not_delivered"]`
  - `static_tags = []`
  - `context.channel = "marketplace"`

**Ação manual pendente:** abrir order_detail.json, achar o pedido correspondente no painel de Vendas do Mercado Livre, e confirmar se algum dos campos acima (ou outro) indica 'venda por publicidade' de forma consistente com o que o painel mostra.

## Diferenças notáveis entre documentação e resposta real

_Preencher manualmente após revisar os JSONs salvos — este script não compara contra a doc._
