# AXEN Intelligence

Plataforma de inteligência comercial para a marca AXEN (pulseiras masculinas).  
Coleta preços de concorrentes, rastreia posição no Mercado Livre, analisa ROAS de campanhas e gera recomendações acionáveis via agentes autônomos.

---

## Visão geral da arquitetura

```
axen-intelligence/
├── axen_database.py          # camada de dados: SQLite + migrações + queries
├── axen_price_scraper.py     # scraper de preços dos concorrentes
├── axen_scheduler.py         # daemon diário (APScheduler, 07:00 BRT)
│
├── agents/                   # agentes de análise
│   ├── axen_pricing_agent.py
│   ├── axen_ml_position_agent.py
│   └── axen_promotions_agent.py
│
├── integrations/             # integrações com plataformas externas
│   ├── axen_mercadolivre.py
│   ├── axen_nuvemshop.py
│   └── axen_shopee.py
│
├── api/                      # API REST (FastAPI)
│   ├── axen_main.py
│   ├── axen_deps.py
│   └── routers/
│       ├── prices.py
│       ├── competitors.py
│       ├── recommendations.py
│       ├── agents.py
│       ├── integrations.py
│       ├── sales.py
│       └── roas.py
│
├── frontend/                 # dashboard React/Vite
│   └── src/
│       ├── Dashboard.jsx
│       ├── Competitividade.jsx
│       ├── GestorPrecos.jsx
│       ├── Anuncios.jsx
│       ├── Vendas.jsx
│       └── Recomendacoes.jsx
│
└── tests/                    # suíte de testes (pytest)
```

---

## Pré-requisitos

| Ferramenta | Versão mínima |
|---|---|
| Python | 3.12+ |
| Node.js | 20+ |
| npm | 10+ |

---

## Instalação

### 1. Clonar / posicionar-se na raiz do projeto

```bash
cd "AXEN Inteligence"
```

### 2. Configurar variáveis de ambiente

```bash
cp .env.example .env
# Edite .env com os valores reais das credenciais
```

### 3. Instalar dependências Python

```bash
# Dependências de produção
pip install -r requirements.txt

# Dependências de desenvolvimento (testes)
pip install -r requirements-dev.txt

# Instalar os browsers do Playwright (necessário para o scraper)
playwright install chromium
```

### 4. Instalar dependências do frontend

```bash
cd frontend
npm install
cd ..
```

---

## Executar em desenvolvimento

### Backend (API FastAPI)

```bash
uvicorn api.axen_main:app --reload --port 8000
```

A API ficará disponível em `http://localhost:8000`.  
Documentação interativa (Swagger UI): `http://localhost:8000/docs`

### Frontend (React/Vite)

Em outro terminal:

```bash
cd frontend
npm run dev
```

O dashboard ficará disponível em `http://localhost:5173`.  
Todas as chamadas `/api/*` são automaticamente proxiadas para `http://localhost:8000`.

---

## Rodar os testes

```bash
pytest
```

Para rodar com saída detalhada e cobertura:

```bash
pytest -v --tb=short
```

A suíte não depende de banco real, de rede ou de credenciais — todos os testes usam mocks e um banco SQLite em memória.

---

## Pipeline de scraping

### Execução imediata (uma vez)

```bash
python axen_scheduler.py --run-now
```

### Ver status da última execução

```bash
python axen_scheduler.py --status
```

### Iniciar o daemon (dispara todo dia às 07:00 BRT)

```bash
python axen_scheduler.py
```

Interrompa com `Ctrl+C` — o daemon aguarda a pipeline em curso concluir antes de encerrar.

---

## Agentes de análise

Os agentes podem ser disparados via API ou via linha de comando.

**Via API:**

```bash
# Rodar todos os agentes
curl -X POST http://localhost:8000/agents/run \
     -H "Content-Type: application/json" \
     -d '{"agent": "all"}'

# Rodar apenas o agente de preços
curl -X POST http://localhost:8000/agents/run \
     -d '{"agent": "pricing"}'
```

**Agentes disponíveis:**

| Nome | Descrição |
|---|---|
| `pricing` | Detecta gaps competitivos e sugere ajustes de preço |
| `ml_position` | Monitora queda de posição nos resultados do Mercado Livre |
| `promotions` | Identifica oportunidades de promoção por material |

---

## Endpoints da API

| Método | Rota | Descrição |
|---|---|---|
| GET | `/healthz` | Liveness probe |
| GET | `/prices/` | Gap competitivo por material |
| GET | `/prices/{material}` | Gap de um material específico |
| GET | `/competitors/` | Produtos dos concorrentes (filtros: material, store) |
| GET | `/competitors/history` | Histórico de variações de preço (até 365 dias) |
| GET | `/recommendations/` | Recomendações ativas dos agentes |
| POST | `/recommendations/{id}/dismiss` | Dispensar uma recomendação |
| POST | `/recommendations/{id}/apply` | Marcar recomendação como aplicada |
| POST | `/agents/run` | Disparar agentes manualmente |
| GET | `/integrations/` | Status das integrações |
| GET | `/integrations/mercadolivre/sales` | Vendas via API do ML |
| GET | `/sales/` | Resumo de vendas (top produtos + semanal) |
| GET | `/sales/by-state` | Vendas por estado |
| POST | `/sales/ingest` | Ingerir pedidos normalizados |
| GET | `/roas/` | Resumo de ROAS |
| GET | `/roas/campaigns` | ROAS por campanha |
| POST | `/roas/ingest` | Ingerir dados de campanha |

---

## Build de produção

### Frontend

```bash
cd frontend
npm run build
```

Os arquivos estáticos são gerados em `frontend/dist/`.  
Sirva-os com qualquer servidor HTTP estático (nginx, caddy, etc.) ou configure o FastAPI para servir a pasta `dist/` via `StaticFiles`.

### Backend

Em produção, remova `--reload` e defina `workers` conforme a carga:

```bash
uvicorn api.axen_main:app --host 0.0.0.0 --port 8000 --workers 2
```

---

## Integrações externas

Todas as integrações são desabilitadas por padrão (`*_ENABLED=false`).  
Ative individualmente em `.env` apenas quando as credenciais estiverem configuradas.

Consulte `.env.example` para a lista completa de variáveis por plataforma.

---

## Variáveis de ambiente — referência rápida

| Variável | Padrão | Descrição |
|---|---|---|
| `DB_PATH` | `axen.db` | Caminho do arquivo SQLite |
| `MERCADOLIVRE_ENABLED` | `false` | Liga a integração com ML |
| `ML_CLIENT_ID` | — | Client ID do app ML |
| `ML_CLIENT_SECRET` | — | Client Secret do app ML |
| `ML_SELLER_ID` | — | Seller ID no ML |
| `NUVEMSHOP_ENABLED` | `false` | Liga a integração com Nuvemshop |
| `NUVEMSHOP_ACCESS_TOKEN` | — | Token de acesso Nuvemshop |
| `NUVEMSHOP_USER_ID` | — | User ID da loja Nuvemshop |
| `SHOPEE_ENABLED` | `false` | Liga a integração com Shopee |
| `SHOPEE_PARTNER_ID` | — | Partner ID do app Shopee |
| `SHOPEE_PARTNER_KEY` | — | Partner Key do app Shopee |
| `SHOPEE_ACCESS_TOKEN` | — | Access Token da loja Shopee |
| `SHOPEE_SHOP_ID` | — | Shop ID da loja Shopee |
