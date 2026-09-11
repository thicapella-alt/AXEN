# tests/fixtures/ml/

Respostas cruas da API do Mercado Livre (Fase 0, item 0.2) ainda não foram
coletadas neste branch — este ambiente de dev não alcança
`api.mercadolibre.com` nem tem o `ml_tokens.json` de produção.

Para gerar os fixtures + este README de verdade, rode no VPS de produção:

```
cd /var/www/axen
source venv/bin/activate   # ou o venv em uso
python scripts/ml_spike.py
```

Isso chama cada endpoint listado no item 0.2 do escopo uma vez, salva a
resposta crua em `tests/fixtures/ml/<nome>.json` e substitui este arquivo por
um resumo real (status HTTP, 401/403 encontrados, divergências entre doc e
resposta, e a checagem do campo de "venda por publicidade" em order_detail
vs. o painel de Vendas).
