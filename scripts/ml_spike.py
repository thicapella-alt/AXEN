#!/usr/bin/env python3
"""
scripts/ml_spike.py — Fase 0 / item 0.2: spike de endpoints da API do Mercado Livre.

O QUE FAZ
─────────
Chama, uma vez cada, os endpoints listados no escopo (item 0.2), usando o
access_token de usuário real que já está salvo em ml_tokens.json em produção
(via api.routers.auth_ml.refresh_token_if_needed() — NÃO gera token novo, só
lê e renova o existente se precisar). Salva a resposta crua de cada chamada
em tests/fixtures/ml/<nome>.json e escreve um resumo em
tests/fixtures/ml/README.md (status HTTP, sucesso/erro, achados).

Não deve ser rodado neste ambiente de dev — não há rota de rede até
api.mercadolibre.com nem ml_tokens.json aqui. Rode no VPS de produção, onde
o token real existe:

    cd /var/www/axen
    source venv/bin/activate   # ou o venv que estiver em uso
    python scripts/ml_spike.py

Requer no ambiente: MERCADOLIVRE_ENABLED=true, ML_CLIENT_ID, ML_CLIENT_SECRET,
ML_SELLER_ID (as mesmas variáveis que a API em produção já usa) e
/var/www/axen/ml_tokens.json existente e com refresh_token válido.

Idempotente/seguro: só faz GETs. Não escreve nada no Mercado Livre, não
gera nem revoga tokens — só lê o token existente via refresh_token_if_needed().
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ml"


class StepResult:
    def __init__(self, name: str, endpoint: str):
        self.name = name
        self.endpoint = endpoint
        self.ok = False
        self.status_code: int | None = None
        self.error: str | None = None
        self.notes: list[str] = []
        self.saved_to: str | None = None

    def as_row(self) -> str:
        status = "✅ OK" if self.ok else "❌ ERRO"
        code = str(self.status_code) if self.status_code is not None else "—"
        notes = "; ".join(self.notes) if self.notes else ""
        return f"| `{self.name}` | `{self.endpoint}` | {status} | {code} | {notes} |"


def save_fixture(name: str, data: Any) -> str:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path.relative_to(REPO_ROOT))


def run_step(
    results: list[StepResult],
    name: str,
    endpoint: str,
    fn: Callable[[], Any],
    save: bool = True,
) -> Any:
    """Run one spike call, record outcome, optionally persist the raw JSON."""
    step = StepResult(name, endpoint)
    results.append(step)
    print(f"→ {name}  ({endpoint})")
    try:
        data = fn()
    except Exception as exc:  # noqa: BLE001 — spike script: capture everything, keep going
        step.error = str(exc)
        # httpx.HTTPStatusError carries the real status code — surface it.
        resp = getattr(exc, "response", None)
        if resp is not None:
            step.status_code = getattr(resp, "status_code", None)
            body_preview = ""
            try:
                body_preview = resp.text[:300]
            except Exception:
                pass
            step.notes.append(f"body: {body_preview}")
        print(f"  ❌ {exc}")
        traceback.print_exc(limit=1)
        return None

    step.ok = True
    step.status_code = 200
    if save:
        step.saved_to = save_fixture(name, data)
        print(f"  ✅ salvo em {step.saved_to}")
    return data


def _first(lst: Any) -> Any:
    return lst[0] if isinstance(lst, list) and lst else None


def main() -> int:
    from integrations.axen_mercadolivre import MercadoLivreIntegration

    ml = MercadoLivreIntegration()
    results: list[StepResult] = []

    # 1) orders_search.json — GET /orders/search
    orders_raw = run_step(
        results, "orders_search", "GET /orders/search",
        lambda: ml.get_orders_search_raw(days=30),
    )
    orders = (orders_raw or {}).get("results", []) if isinstance(orders_raw, dict) else []
    order_id = (_first(orders) or {}).get("id")

    # 2) order_detail.json — GET /orders/{id}
    order_detail = None
    if order_id:
        order_detail = run_step(
            results, "order_detail", f"GET /orders/{order_id}",
            lambda: ml.get_order_detail(order_id),
        )
    else:
        results.append(_skip("order_detail", "GET /orders/{id}", "sem order_id (orders_search vazio)"))

    # 3) shipment_detail.json — GET /shipments/{id}
    shipment_id = None
    if isinstance(order_detail, dict):
        shipment_id = (order_detail.get("shipping") or {}).get("id")
    if shipment_id:
        run_step(
            results, "shipment_detail", f"GET /shipments/{shipment_id}",
            lambda: ml.get_shipment_detail(shipment_id),
        )
    else:
        results.append(_skip("shipment_detail", "GET /shipments/{id}", "sem shipping.id no order_detail"))

    # 4) items_search.json — GET /users/{seller_id}/items/search
    items_raw = run_step(
        results, "items_search", "GET /users/{seller_id}/items/search",
        lambda: ml.get_items_search_raw(limit=100),
    )
    item_ids = (items_raw or {}).get("results", []) if isinstance(items_raw, dict) else []
    first_item_id = _first(item_ids)

    # 5) item_detail.json — GET /items/{id}?attributes=id,title,pictures,variations
    item_detail = None
    if first_item_id:
        item_detail = run_step(
            results, "item_detail", f"GET /items/{first_item_id}?attributes=id,title,pictures,variations",
            lambda: ml.get_item_detail(first_item_id),
        )
    else:
        results.append(_skip("item_detail", "GET /items/{id}", "sem item_id (items_search vazio)"))

    # 6) item_variations.json — confirma se `variations` já vem dentro do item_detail.
    #    Não existe endpoint dedicado separado para variações no catálogo padrão do ML;
    #    o campo `variations` faz parte do próprio /items/{id}. Este passo só registra
    #    a conclusão (não é uma chamada HTTP extra) para o README.
    variations_step = StepResult("item_variations", "(campo dentro de GET /items/{id})")
    results.append(variations_step)
    if isinstance(item_detail, dict):
        variations = item_detail.get("variations")
        variations_step.ok = True
        variations_step.status_code = 200
        if variations:
            variations_step.notes.append(f"{len(variations)} variação(ões) já vêm em item_detail.variations")
        else:
            variations_step.notes.append(
                "item_detail.variations veio vazio/ausente — item sem variações OU "
                "confirmar manualmente se o endpoint /items/{id}/variations é necessário"
            )
    else:
        variations_step.notes.append("não avaliado — item_detail falhou")

    # 7) inventory_stock.json — GET /inventories/{inventory_id}/stock/fulfillment
    #    inventory_id pode vir em item_detail.inventory_id (item sem variação) ou em
    #    cada variations[].inventory_id (item Full com variação). attributes=id,title,
    #    pictures,variations não pede inventory_id no nível do item explicitamente — se
    #    não aparecer em nenhum lugar, tentamos de novo pedindo esse atributo à parte.
    inventory_id = None
    if isinstance(item_detail, dict):
        inventory_id = item_detail.get("inventory_id")
        if not inventory_id:
            for v in item_detail.get("variations") or []:
                if isinstance(v, dict) and v.get("inventory_id"):
                    inventory_id = v["inventory_id"]
                    break
    if not inventory_id and first_item_id:
        # Segunda tentativa: pedir o atributo inventory_id explicitamente (fora do
        # escopo exato do fixture item_detail.json, então salva separado).
        extra = run_step(
            results, "item_detail_inventory_probe",
            f"GET /items/{first_item_id}?attributes=id,inventory_id,variations",
            lambda: ml._get_authed(f"/items/{first_item_id}", attributes="id,inventory_id,variations"),
        )
        if isinstance(extra, dict):
            inventory_id = extra.get("inventory_id")
            if not inventory_id:
                for v in extra.get("variations") or []:
                    if isinstance(v, dict) and v.get("inventory_id"):
                        inventory_id = v["inventory_id"]
                        break

    if inventory_id:
        run_step(
            results, "inventory_stock", f"GET /inventories/{inventory_id}/stock/fulfillment",
            lambda: ml.get_inventory_stock(inventory_id),
        )
    else:
        results.append(_skip(
            "inventory_stock", "GET /inventories/{inventory_id}/stock/fulfillment",
            "nenhum inventory_id encontrado no 1º item (provavelmente não é Full) — "
            "rode de novo trocando first_item_id por um MLB que você sabe ser Full",
        ))

    # 8) visits_time_window.json — GET /items/{id}/visits/time_window
    if first_item_id:
        run_step(
            results, "visits_time_window", f"GET /items/{first_item_id}/visits/time_window",
            lambda: ml.get_item_visits_time_window(first_item_id, days=30),
        )
    else:
        results.append(_skip("visits_time_window", "GET /items/{id}/visits/time_window", "sem item_id"))

    # 9) claims_search.json — GET /post-purchase/v1/claims/search
    run_step(
        results, "claims_search", "GET /post-purchase/v1/claims/search",
        lambda: ml.get_claims_search(),
    )

    # 10) questions_search.json — GET /questions/search
    run_step(
        results, "questions_search", "GET /questions/search",
        lambda: ml.get_questions_search(),
    )

    # 11) advertising_campaigns.json — GET /advertising/advertisers/{id}/product_ads/campaigns/search
    run_step(
        results, "advertising_campaigns",
        "GET /advertising/advertisers/{seller_id}/product_ads/campaigns/search",
        lambda: ml.get_ad_campaigns(),
    )

    # ── "Venda por publicidade" — divergência já registrada no projeto ─────────
    # (claude/diagnostico-meli-vendas-ads-2026-08-31.md §11, claude/checkpoint-meli-09-09.md)
    ads_probe_note = _probe_ads_fields(order_detail)

    write_readme(results, order_id, shipment_id, first_item_id, inventory_id, ads_probe_note)

    n_ok = sum(1 for r in results if r.ok)
    print(f"\n{n_ok}/{len(results)} passos OK. Ver tests/fixtures/ml/README.md para o detalhe.")
    return 0


def _skip(name: str, endpoint: str, reason: str) -> StepResult:
    step = StepResult(name, endpoint)
    step.notes.append(f"pulado: {reason}")
    print(f"→ {name}  ({endpoint})\n  ⏭️  pulado: {reason}")
    return step


def _probe_ads_fields(order_detail: dict | None) -> str:
    """
    Lista, no order_detail, todos os campos/valores cujo nome ou conteúdo
    sugira publicidade/ads/catálogo, para comparação manual com o painel de
    Vendas do Mercado Livre (ver escopo — divergência já registrada).
    Não afirma qual campo é "o" campo certo — só reduz a busca manual.
    """
    if not isinstance(order_detail, dict):
        return "order_detail indisponível — não deu para inspecionar."

    keywords = ("ad", "ads", "advertis", "catalog", "channel", "tag")
    hits: list[str] = []

    def walk(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_path = f"{path}.{k}" if path else k
                if any(kw in k.lower() for kw in keywords):
                    hits.append(f"{new_path} = {json.dumps(v, ensure_ascii=False)[:200]}")
                walk(v, new_path)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")

    walk(order_detail, "")
    if not hits:
        return "Nenhum campo com nome sugerindo ads/catálogo/canal encontrado em order_detail — inspecionar manualmente o JSON completo."
    return "Campos candidatos encontrados em order_detail (comparar com o painel de Vendas):\n\n" + "\n".join(
        f"  - `{h}`" for h in hits
    )


def write_readme(
    results: list[StepResult],
    order_id, shipment_id, item_id, inventory_id,
    ads_probe_note: str,
) -> None:
    lines = [
        "# tests/fixtures/ml/ — Spike de API do Mercado Livre (Fase 0, item 0.2)",
        "",
        f"Gerado por `scripts/ml_spike.py` em {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}.",
        "",
        f"IDs usados nesta rodada: order_id=`{order_id}` · shipment_id=`{shipment_id}` · "
        f"item_id=`{item_id}` · inventory_id=`{inventory_id}`",
        "",
        "## Resultado por endpoint",
        "",
        "| Fixture | Endpoint | Status | HTTP | Notas |",
        "|---|---|---|---|---|",
    ]
    lines += [r.as_row() for r in results]

    lines += [
        "",
        "## Erros de autorização (401/403)",
        "",
    ]
    auth_errors = [r for r in results if r.status_code in (401, 403)]
    if auth_errors:
        for r in auth_errors:
            lines.append(f"- **{r.name}** (`{r.endpoint}`) → HTTP {r.status_code}: provável escopo OAuth "
                          f"faltando na aplicação ML — revisar em https://developers.mercadolivre.com.br "
                          f"(painel da aplicação) se o escopo necessário está habilitado.")
    else:
        lines.append("Nenhum 401/403 registrado nesta rodada (ou os passos que falharam falharam por outro motivo — ver tabela acima).")

    lines += [
        "",
        "## Divergência 'venda por publicidade' (order_detail vs. painel de Vendas)",
        "",
        "Ver claude/diagnostico-meli-vendas-ads-2026-08-31.md §11 e claude/checkpoint-meli-09-09.md",
        "para o contexto completo da divergência já registrada no projeto.",
        "",
        ads_probe_note,
        "",
        "**Ação manual pendente:** abrir order_detail.json, achar o pedido correspondente no "
        "painel de Vendas do Mercado Livre, e confirmar se algum dos campos acima (ou outro) "
        "indica 'venda por publicidade' de forma consistente com o que o painel mostra.",
        "",
        "## Diferenças notáveis entre documentação e resposta real",
        "",
        "_Preencher manualmente após revisar os JSONs salvos — este script não compara contra a doc._",
        "",
    ]

    (FIXTURES_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n📄 README escrito em {(FIXTURES_DIR / 'README.md').relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
