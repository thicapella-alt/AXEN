# Abas `Movimentos` e `Contagem` — layout proposto (item 0.5)

> **Status: proposta, não aplicada.** Estas abas ainda não existem na planilha
> "DRE AXEN" — este documento é o layout para o Thiago criar manualmente.
> Nenhuma aba foi criada por este trabalho.

## Por que ledger em vez de colunas datadas

A aba `Estoque` atual representa o saldo com uma coluna por data de
snapshot (`03/08/26`, `03/10/26`, …), que cresce indefinidamente e não
registra *o que* aconteceu entre um snapshot e outro — só o resultado.

Um ledger (`Movimentos`) registra cada evento que muda o estoque
individualmente; o saldo em qualquer data é sempre recalculável somando
os movimentos até aquele ponto. `Contagem` complementa isso registrando
contagens físicas periódicas, usadas para conferir/corrigir o saldo
calculado (gerando um movimento `ajuste_contagem` quando há divergência).

Isso é compatível com o vocabulário de tipos de movimento definido pelo
projeto (ver `scripts/map_listings.py` e o escopo v1.4 §5): a leitura
implementada aqui (`integrations/axen_sheets.py`) valida cada linha
contra essa mesma lista de tipos — mas **não grava em nenhuma tabela**
ainda. A tabela `stock_movements` em si é trabalho da sessão S2
(coletor/ledger), não desta sessão de plumbing.

## Locais (`local_origem` / `local_destino`)

Vocabulário fechado usado nas duas abas — mantém a rastreabilidade do
produto pelo fluxo físico real da AXEN:

| Local | Significado |
|---|---|
| `fornecedor` | Antes de chegar no Brasil (AliExpress etc.) |
| `estoque_bruto` | Recebido, ainda sem gravação a laser |
| `laser` | Em processo de gravação/acabamento |
| `estoque_pronto` | Gravado, pronto para envio |
| `full` | Enviado ao Mercado Envios Full |
| `cliente` | Vendido / entregue |
| `perda` | Baixa por perda, quebra ou extravio |
| `ajuste` | Sem local físico real — usado só em `ajuste_contagem` |

## Aba `Movimentos`

Uma linha = um evento. Nunca editar uma linha já lançada — para corrigir,
lançar um novo movimento compensatório (`ajuste_contagem`) e explicar em
`observacao`.

| Coluna | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `data` | `AAAA-MM-DD HH:MM` | sim | Data/hora do movimento (horário de Brasília) |
| `sku_axen` | texto | sim | Deve existir em `products.sku_axen` |
| `tipo` | enum | sim | Um de: `compra_recebida`, `para_laser`, `de_laser`, `envio_full`, `recebido_full`, `venda_ml`, `devolucao`, `ajuste_contagem`, `brinde`, `perda` |
| `quantidade` | inteiro > 0 | sim | Sempre positivo — a direção do movimento vem do `tipo` + origem/destino, nunca do sinal |
| `local_origem` | enum de locais | sim* | Vazio apenas quando `tipo=ajuste_contagem` (usar `ajuste`) |
| `local_destino` | enum de locais | sim* | Idem |
| `referencia` | texto | não | Nº do pedido ML, nº do pedido de compra AliExpress, nº da contagem relacionada, etc. |
| `observacao` | texto | não | Contexto livre — obrigatório em `perda` e `ajuste_contagem` |

### Mapeamento tipo → origem/destino esperado

| `tipo` | `local_origem` | `local_destino` |
|---|---|---|
| `compra_recebida` | `fornecedor` | `estoque_bruto` |
| `para_laser` | `estoque_bruto` | `laser` |
| `de_laser` | `laser` | `estoque_pronto` |
| `envio_full` | `estoque_pronto` | `full` |
| `recebido_full` | *(uso raro — full confirma recebimento; mesmo destino)* | `full` |
| `venda_ml` | `full` | `cliente` |
| `devolucao` | `cliente` | `estoque_pronto` |
| `brinde` | `estoque_pronto` | `cliente` |
| `perda` | *(o local onde ocorreu a perda)* | `perda` |
| `ajuste_contagem` | ver nota abaixo | ver nota abaixo |

> **Correção pós-S2** (a versão original deste doc, Fase 0, descrevia os
> dois lados de `ajuste_contagem` como `ajuste`/`ajuste` — isso não
> funciona pra ajustes gerados automaticamente a partir da aba
> `Contagem`, porque nenhum local real seria afetado). A regra real,
> usada pelo job de persistência (`stock_movements`, S2):
> - Ajuste **pra cima** (soma estoque que faltava no ledger):
>   `local_origem='ajuste'`, `local_destino=<local real contado>`
> - Ajuste **pra baixo** (remove estoque que o ledger tinha a mais):
>   `local_origem=<local real contado>`, `local_destino='ajuste'`
>
> `ajuste` continua sendo uma conta virtual — nunca aparece nos dois
> lados do mesmo movimento quando gerado automaticamente. Um
> `ajuste_contagem` lançado manualmente na planilha (não pela contagem)
> segue a mesma regra: sempre um local real de um lado.

### Exemplo de linha preenchida

```
data                 sku_axen         tipo              quantidade  local_origem   local_destino   referencia          observacao
2026-09-15 10:30     DRIFT-185-AZU    compra_recebida   10          fornecedor     estoque_bruto    8213826502636903    Lote AliExpress recebido via Correios
```

## Aba `Contagem`

Uma linha = uma contagem física de um SKU em um local, em uma data.
Serve de auditoria — não é o saldo "oficial" (esse vem da soma dos
`Movimentos`), mas o ponto de verdade físico pra detectar divergência.

| Coluna | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `data_contagem` | `AAAA-MM-DD` | sim | Data em que a contagem foi feita |
| `sku_axen` | texto | sim | Deve existir em `products.sku_axen` |
| `local` | enum de locais | sim | Onde a contagem foi feita (`estoque_bruto`, `laser`, `estoque_pronto`, `full`) |
| `quantidade_contada` | inteiro ≥ 0 | sim | Quantidade física encontrada |
| `quantidade_sistema` | inteiro ≥ 0 | não | Informativo apenas — o job de persistência (S2) **não lê esta coluna**; ele calcula o saldo do ledger sozinho e compara com `quantidade_contada`. Pode ficar em branco ou ser preenchido manualmente só pra conferência visual |
| `responsavel` | texto | não | Quem contou |
| `observacao` | texto | não | Contexto — obrigatório se `quantidade_contada` ≠ `quantidade_sistema` |

### Exemplo de linha preenchida

```
data_contagem   sku_axen        local           quantidade_contada  quantidade_sistema  responsavel   observacao
2026-09-15      DRIFT-185-AZU   estoque_pronto  6                                        Thiago
```

## Como uma linha de `Contagem` vira `ajuste_contagem` (S2)

Pra cada `(sku_axen, local)`, as linhas de `Contagem` são processadas em
ordem cronológica. Cada linha gera um ajuste igual a:

```
delta = quantidade_contada - saldo_ledger_ate(data_contagem, sku_axen, local)
```

Onde `saldo_ledger_ate(...)` soma só os movimentos com `data <=
data_contagem` daquele SKU/local — **incluindo** ajustes de contagens
anteriores já processadas. Isso já cobre os dois casos com a mesma
fórmula, sem regra especial:

- **Primeira contagem** de um SKU/local: como ainda não há movimento
  nenhum, o saldo antes dela é 0 — o delta vira o próprio
  `quantidade_contada` (é isso que "zera" o ledger no início).
- **Contagens seguintes**: o saldo já reflete tudo que aconteceu até
  ali (compras, vendas, etc.) — o delta é só a diferença real, não o
  valor absoluto de novo.

Se `delta = 0`, nenhum movimento é gravado (a contagem confirmou o
ledger, nada a corrigir). Cada linha da planilha só gera **no máximo
um** `ajuste_contagem` — reexecutar o job não duplica (dedupe pela
referência da linha, ver `integrations/axen_sheets.py`).

## O que já está pronto

`integrations/axen_sheets.py` sabe **ler** as duas abas acima (via
`AxenSheetsClient.read_movimentos()` / `.read_contagem()`) e validar cada
linha contra o layout descrito — `tipo`/`local` fora do vocabulário,
`quantidade` não-numérica ou ≤ 0, `sku_axen` vazio viram um erro reportado
por linha, sem derrubar a leitura das linhas válidas (Fase 0). A partir
da sessão S2, o job de persistência grava essas linhas em
`stock_movements` de forma idempotente — ver `scripts/ingest_stock_sheets.py`.

## Reconciliação com o Mercado Livre (S2, §7.2 do escopo)

O ML só enxerga dois "buckets" de estoque por SKU: **Full**
(`GET /inventories/{id}/stock/fulfillment`) e **próprio**
(`available_quantity` do item/variação, pra quem não usa Full). O
ledger tem 8 locais, mas só `full` e `estoque_pronto` são comparáveis
contra o ML — `estoque_bruto` e `laser` são estágios internos que o ML
nunca vê (item ainda nem está publicado como disponível), e por isso
ficam de fora do cálculo de divergência em `stock_snapshots` (embora
continuem existindo normalmente em `stock_movements` — importam pra
gestão de compras/produção, não pra reconciliação).

`envio_full` continua um movimento direto (`estoque_pronto` →
`full`), sem estágio de trânsito intermediário — a janela real de ~3
dias entre a postagem e o ML confirmar o recebimento aparece como
divergência temporária em `stock_snapshots`, o mesmo padrão que já
existe em `venda_ml` (debita no pagamento, antes da saída física de
fato). Aceito como esperado; só vira um problema a resolver se mascarar
divergência real com frequência (decisão de S3+).
