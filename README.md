# AXEN — Competitive Accessory Price Scraper

Raspagem de preços competitivos para os e-commerces **Key Design**, **W. Buscatti** e **Beroc**, segmentada por material (couro, metal, corda, pedra).

## Estrutura do projeto

```
AXEN/
├── main.py               ← Ponto de entrada CLI
├── requirements.txt
├── scraper/
│   ├── beroc.py          ← Shopify /products.json API (sem autenticação)
│   ├── wbuscatti.py      ← HTML + BeautifulSoup (Loja Integrada)
│   ├── keydesign.py      ← VTEX API REST + fallback Playwright
│   ├── analysis.py       ← Estatísticas e resumo executivo
│   ├── models.py         ← Dataclass Product
│   └── utils.py          ← Headers, parse_price, polite_delay
```

## Instalação

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Dependências opcionais (Key Design — Cloudflare WAF)

```bash
pip install playwright playwright-stealth
playwright install chromium
```

## Uso

```bash
# Raspar todas as lojas
python main.py

# Apenas Beroc e W. Buscatti
python main.py --stores beroc wbuscatti

# Ativar cloudscraper para bypass Cloudflare
python main.py --cloudscraper

# Desabilitar Playwright (mais rápido, pode perder Key Design)
python main.py --no-playwright

# Exportar produtos individuais + JSON
python main.py --output-products produtos.csv --output-json stats.json
```

### Todos os argumentos

| Argumento | Padrão | Descrição |
|---|---|---|
| `--stores` | todas | `keydesign` `wbuscatti` `beroc` |
| `--cloudscraper` | off | Bypass Cloudflare via TLS fingerprint |
| `--no-playwright` | off | Desativa fallback headless browser |
| `--output-csv` | `axen_stats_<ts>.csv` | Tabela de estatísticas |
| `--output-products` | — | CSV de produtos individuais |
| `--output-json` | — | JSON com estatísticas completas |

## Saída esperada

### Tabela no terminal

```
           Análise Competitiva de Preços — Acessórios Masculinos
┌─────────────┬──────────┬─────┬──────────┬─────────────┬──────────┬──────────┐
│ Loja        │ Material │ Qtd │ Mín (R$) │ Médiana (R$)│ Média    │ Máx (R$) │
├─────────────┼──────────┼─────┼──────────┼─────────────┼──────────┼──────────┤
│ Key Design  │ Couro    │  42 │   189.90 │      249.90 │   261.30 │   489.90 │
│ W. Buscatti │ Couro    │  35 │   159.90 │      219.90 │   228.40 │   419.90 │
│ Beroc       │ Corda    │  19 │    89.90 │      119.90 │   124.70 │   219.90 │
└─────────────┴──────────┴─────┴──────────┴─────────────┴──────────┴──────────┘
```

### CSV de estatísticas (`axen_stats_*.csv`)

| Loja | Material | Qtd | Preço Mínimo | Preço Médio | Preço Mediano | Preço Máximo |
|---|---|---|---|---|---|---|
| Key Design | Couro | 42 | 189.90 | 261.30 | 249.90 | 489.90 |

## Detalhes técnicos por loja

### Beroc (Shopify) — mais fácil
- API pública `/collections/{handle}/products.json?limit=250&page=N`
- Nenhuma autenticação necessária; `robots.txt` permite o endpoint
- Material: handle da collection → tags → `product_type` → título

### W. Buscatti (Loja Integrada) — dificuldade média
- Material codificado no path: `/masculino/pulseiras/couro`
- BeautifulSoup com múltiplos seletores CSS por variação de tema
- Paginação `?pg=N`; fallback via `/loja/busca.php?loja=488685&query=...`

### Key Design (VTEX IO) — mais difícil
1. **VTEX Catalog REST API** — `/api/catalog_system/pub/products/search/{path}`
2. **cloudscraper** — bypass de JS challenge (`--cloudscraper`)
3. **Playwright + stealth** — headless Chromium como último recurso

**Proxy residencial** (se Cloudflare bloquear Playwright):
```bash
export HTTPS_PROXY=http://user:pass@proxy.example.com:8080
python main.py --stores keydesign --cloudscraper
```

## Conformidade e ética

- Delays de 1–3 s entre requisições para não sobrecarregar servidores
- Coleta apenas preços públicos exibidos a qualquer visitante
- Sem extração de dados de clientes ou informações sensíveis
- Nenhuma rota proibida pelo `robots.txt` é acessada
