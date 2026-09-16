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
| `ajuste_contagem` | `ajuste` | `ajuste` |

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
| `quantidade_sistema` | inteiro ≥ 0 | não | Deixar em branco por enquanto — cálculo automático do saldo é trabalho futuro (S2); pode ser preenchido manualmente se o Thiago quiser comparar |
| `responsavel` | texto | não | Quem contou |
| `observacao` | texto | não | Contexto — obrigatório se `quantidade_contada` ≠ `quantidade_sistema` |

### Exemplo de linha preenchida

```
data_contagem   sku_axen        local           quantidade_contada  quantidade_sistema  responsavel   observacao
2026-09-15      DRIFT-185-AZU   estoque_pronto  6                                        Thiago
```

## O que já está pronto (esta sessão)

`integrations/axen_sheets.py` sabe **ler** as duas abas acima (via
`AxenSheetsClient.read_movimentos()` / `.read_contagem()`) e validar cada
linha contra o layout descrito — `tipo`/`local` fora do vocabulário,
`quantidade` não-numérica ou ≤ 0, `sku_axen` vazio viram um erro reportado
por linha, sem derrubar a leitura das linhas válidas. Testado apenas
contra um mock (`tests/integrations/test_axen_sheets.py`) — nenhuma
gravação em `stock_movements` ou qualquer outra tabela acontece aqui;
isso é trabalho da sessão S2 (coletor/ledger).
