#!/usr/bin/env python3
"""
scripts/redact_ml_fixtures.py — Fase 0: gera cópias .redacted.json dos
fixtures do spike de API que têm dado real de cliente (nome, endereço,
telefone), pra dar pra documentar a estrutura da resposta da API no
repositório sem expor PII.

Uso (no VPS, depois de rodar scripts/ml_spike.py):
    python scripts/redact_ml_fixtures.py

Lê tests/fixtures/ml/{orders_search,order_detail,shipment_detail}.json (os
que existirem) e escreve as versões *.redacted.json ao lado — essas sim são
seguras pra `git add`. Os arquivos originais (com PII de verdade) continuam
de fora do git (ver .gitignore) e ficam só no servidor.

Como decide o que mascarar: percorre o JSON recursivamente e troca o VALOR
de qualquer campo cuja CHAVE bata com a lista abaixo (case-insensitive,
substring) — mantém a estrutura (tipos, presença dos campos) intacta, só
troca o conteúdo. Isso é heurístico, não uma lista oficial da API do ML —
dá uma conferida rápida no .redacted.json gerado antes de commitar, só por
garantia.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ml"

# Nomes de campo (substring, case-insensitive) tratados como PII — o valor
# é substituído, a chave e a estrutura ficam iguais.
PII_KEY_SUBSTRINGS = [
    "nickname", "first_name", "last_name", "email", "phone",
    "receiver_name", "receiver_phone", "address_line", "street_name",
    "street_number", "zip_code", "comment", "latitude", "longitude",
    "cpf", "cnpj", "document_number", "billing_info", "tax_id",
]

REDACTED_VALUE = "[REDACTED]"


def _is_pii_key(key: str) -> bool:
    k = key.lower()
    return any(sub in k for sub in PII_KEY_SUBSTRINGS)


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if _is_pii_key(k):
                result[k] = REDACTED_VALUE if v is not None else None
            else:
                result[k] = redact(v)
        return result
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def main() -> int:
    targets = ["orders_search", "order_detail", "shipment_detail"]
    any_done = False
    for name in targets:
        src = FIXTURES_DIR / f"{name}.json"
        if not src.exists():
            print(f"# {src.name} não existe, pulando.")
            continue
        data = json.loads(src.read_text(encoding="utf-8"))
        redacted = redact(data)
        dst = FIXTURES_DIR / f"{name}.redacted.json"
        dst.write_text(json.dumps(redacted, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✅ {dst.relative_to(REPO_ROOT)}")
        any_done = True

    if not any_done:
        print("Nada pra redigir — rode primeiro scripts/ml_spike.py")
        return 1

    print("\nConfere rapidamente os .redacted.json antes de dar `git add` — "
          "a lista de campos mascarados é heurística, não oficial da API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
