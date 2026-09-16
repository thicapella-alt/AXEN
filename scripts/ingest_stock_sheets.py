#!/usr/bin/env python3
"""
scripts/ingest_stock_sheets.py — lê as abas Movimentos/Contagem (via
integrations/axen_sheets.py) e grava em stock_movements (S2 Parte 1).

Uso:
    cd /var/www/axen && source venv/bin/activate
    set -a; source .env; set +a
    python scripts/ingest_stock_sheets.py                # aplica de verdade
    python scripts/ingest_stock_sheets.py --dry-run       # só mostra o que faria

Idempotência
────────────
  - Movimentos: cada linha vira no máximo 1 stock_movement. A chave de
    dedupe é a própria posição da linha na planilha
    (`planilha_movimentos:<spreadsheet_id>:<aba>:<row_number>`) —
    reexecutar não duplica, mesmo se você rodar o job 3x/dia (item 0.7)
    contra a mesma planilha sem nada novo.
  - Contagem: cada linha gera no máximo 1 `ajuste_contagem`, calculado
    como delta entre a contagem física e o saldo do ledger até aquela
    data (não o valor absoluto — ver docs/movimentos-contagem-sheets.md
    §"Como uma linha de Contagem vira ajuste_contagem"). Se o delta for
    zero (contagem bateu com o ledger), nenhuma linha é gravada — e
    reexecutar continua não gravando nada pra essa linha, pelo mesmo
    cálculo. Contagens de um mesmo (sku_axen, local) são processadas em
    ordem cronológica, não na ordem da planilha.

Este script NUNCA edita/apaga uma linha de stock_movements já gravada —
só insere linhas novas (ou registra erro/skip, nunca aborta o lote todo
por causa de uma linha ruim).
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("axen.ingest_stock_sheets")


@dataclass
class IngestSummary:
    """Resultado de uma chamada a ingest_movimentos()/ingest_contagem()."""
    total_rows: int = 0
    inserted: int = 0
    skipped_duplicate: int = 0
    skipped_delta_zero: int = 0  # só Contagem — contagem bateu com o ledger
    sheet_errors: int = 0        # linhas que já vieram inválidas do axen_sheets (tipo/local/etc.)
    db_errors: list[str] = field(default_factory=list)  # ex. sku_axen desconhecido


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedupe_exists(conn: sqlite3.Connection, dedupe_key: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM stock_movements WHERE dedupe_key=?", (dedupe_key,)
    ).fetchone() is not None


def _insert_movement(conn: sqlite3.Connection, **fields) -> None:
    """INSERT direto (sem `with conn:`) — cada linha tem seu próprio
    commit/rollback, então uma linha ruim nunca desfaz linhas boas já
    gravadas no mesmo lote (ver módulo docstring)."""
    conn.execute(
        """
        INSERT INTO stock_movements (
            data, sku_axen, tipo, quantidade, local_origem, local_destino,
            referencia, observacao, fonte, dedupe_key, ingested_at
        ) VALUES (:data, :sku_axen, :tipo, :quantidade, :local_origem, :local_destino,
                  :referencia, :observacao, :fonte, :dedupe_key, :ingested_at)
        """,
        fields,
    )


# ── Movimentos ────────────────────────────────────────────────────────────────

def ingest_movimentos(
    conn: sqlite3.Connection,
    sheets_client,
    spreadsheet_id: str,
    worksheet_name: str | None = None,
    dry_run: bool = False,
) -> IngestSummary:
    """
    Lê a aba Movimentos e grava cada linha válida em stock_movements
    (1 linha da planilha = no máximo 1 stock_movement).
    """
    from integrations.axen_sheets import DEFAULT_MOVIMENTOS_SHEET

    worksheet_name = worksheet_name or DEFAULT_MOVIMENTOS_SHEET
    result = sheets_client.read_movimentos(spreadsheet_id, worksheet_name)
    summary = IngestSummary(total_rows=len(result.rows) + len(result.errors))
    summary.sheet_errors = len(result.errors)
    for e in result.errors:
        summary.db_errors.append(f"[Movimentos] linha {e.row_number}: {e.message}")

    for row in result.rows:
        dedupe_key = f"planilha_movimentos:{spreadsheet_id}:{worksheet_name}:{row.row_number}"
        if _dedupe_exists(conn, dedupe_key):
            summary.skipped_duplicate += 1
            continue

        if dry_run:
            log.info("[dry-run] gravaria movimento: %s", dedupe_key)
            summary.inserted += 1
            continue

        try:
            _insert_movement(
                conn,
                data=row.data,
                sku_axen=row.sku_axen,
                tipo=row.tipo,
                quantidade=row.quantidade,
                local_origem=row.local_origem,
                local_destino=row.local_destino,
                referencia=row.referencia,
                observacao=row.observacao,
                fonte="planilha_movimentos",
                dedupe_key=dedupe_key,
                ingested_at=_now_iso(),
            )
            conn.commit()
            summary.inserted += 1
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            summary.db_errors.append(
                f"[Movimentos] linha {row.row_number} (sku_axen={row.sku_axen}): {exc}"
            )

    return summary


# ── Contagem ──────────────────────────────────────────────────────────────────

def _saldo_ate(conn: sqlite3.Connection, sku_axen: str, local: str, data_limite: str) -> int:
    """Saldo do ledger pra (sku_axen, local) considerando só movimentos com
    data <= data_limite — inclui ajustes de contagens anteriores já gravados."""
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN local_destino=? THEN quantidade ELSE 0 END), 0)
          - COALESCE(SUM(CASE WHEN local_origem=? THEN quantidade ELSE 0 END), 0)
        FROM stock_movements
        WHERE sku_axen=? AND data<=?
        """,
        (local, local, sku_axen, data_limite),
    ).fetchone()
    return row[0]


def ingest_contagem(
    conn: sqlite3.Connection,
    sheets_client,
    spreadsheet_id: str,
    worksheet_name: str | None = None,
    dry_run: bool = False,
) -> IngestSummary:
    """
    Lê a aba Contagem e gera um `ajuste_contagem` por linha, igual ao
    delta entre a contagem física e o saldo do ledger até aquela data.
    Processa cada (sku_axen, local) em ordem cronológica — necessário
    porque o delta de uma contagem depende dos ajustes das contagens
    anteriores do mesmo par já terem sido considerados.
    """
    from integrations.axen_sheets import DEFAULT_CONTAGEM_SHEET

    worksheet_name = worksheet_name or DEFAULT_CONTAGEM_SHEET
    result = sheets_client.read_contagem(spreadsheet_id, worksheet_name)
    summary = IngestSummary(total_rows=len(result.rows) + len(result.errors))
    summary.sheet_errors = len(result.errors)
    for e in result.errors:
        summary.db_errors.append(f"[Contagem] linha {e.row_number}: {e.message}")

    # Agrupa por (sku_axen, local) e ordena por data_contagem — a ordem na
    # planilha não importa, a ordem cronológica por par sim.
    groups: dict[tuple[str, str], list] = {}
    for row in result.rows:
        groups.setdefault((row.sku_axen, row.local), []).append(row)
    for key in groups:
        groups[key].sort(key=lambda r: (r.data_contagem, r.row_number))

    for (sku_axen, local), rows in groups.items():
        for row in rows:
            dedupe_key = f"planilha_contagem:{spreadsheet_id}:{worksheet_name}:{row.row_number}"
            if _dedupe_exists(conn, dedupe_key):
                summary.skipped_duplicate += 1
                continue

            saldo = _saldo_ate(conn, sku_axen, local, row.data_contagem)
            delta = row.quantidade_contada - saldo
            if delta == 0:
                summary.skipped_delta_zero += 1
                continue

            if delta > 0:
                local_origem, local_destino, quantidade = "ajuste", local, delta
            else:
                local_origem, local_destino, quantidade = local, "ajuste", -delta

            obs = f"[auto] delta vs. saldo do ledger ({saldo} -> {row.quantidade_contada})"
            if row.observacao:
                obs += f" — {row.observacao}"

            if dry_run:
                log.info(
                    "[dry-run] gravaria ajuste_contagem: %s (delta=%+d, saldo_antes=%d)",
                    dedupe_key, delta, saldo,
                )
                summary.inserted += 1
                continue

            try:
                _insert_movement(
                    conn,
                    data=row.data_contagem,
                    sku_axen=sku_axen,
                    tipo="ajuste_contagem",
                    quantidade=quantidade,
                    local_origem=local_origem,
                    local_destino=local_destino,
                    referencia=f"contagem:{worksheet_name}:linha {row.row_number}",
                    observacao=obs,
                    fonte="planilha_contagem",
                    dedupe_key=dedupe_key,
                    ingested_at=_now_iso(),
                )
                conn.commit()
                summary.inserted += 1
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                summary.db_errors.append(
                    f"[Contagem] linha {row.row_number} (sku_axen={sku_axen}): {exc}"
                )

    return summary


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_summary(label: str, s: IngestSummary) -> None:
    print(f"\n== {label} ==")
    print(f"  linhas lidas:          {s.total_rows}")
    print(f"  gravadas:              {s.inserted}")
    print(f"  já existiam (dedupe):  {s.skipped_duplicate}")
    if label == "Contagem":
        print(f"  contagem bateu (delta=0): {s.skipped_delta_zero}")
    print(f"  inválidas na planilha: {s.sheet_errors}")
    if s.db_errors:
        print(f"  erros ({len(s.db_errors)}):")
        for err in s.db_errors:
            print(f"    - {err}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="mostra o que gravaria, sem tocar no banco")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from axen_database import get_connection, migrate
    from integrations.axen_sheets import AxenSheetsClient

    dre_id = os.environ.get("GOOGLE_SHEETS_DRE_ID")
    if not dre_id:
        print("GOOGLE_SHEETS_DRE_ID não definido no ambiente.", file=sys.stderr)
        return 1

    conn = get_connection(os.getenv("DB_PATH", "axen.db"))
    migrate(conn)

    client = AxenSheetsClient()

    s_mov = ingest_movimentos(conn, client, dre_id, dry_run=args.dry_run)
    s_cont = ingest_contagem(conn, client, dre_id, dry_run=args.dry_run)

    conn.close()

    _print_summary("Movimentos", s_mov)
    _print_summary("Contagem", s_cont)

    return 0


if __name__ == "__main__":
    sys.exit(main())
