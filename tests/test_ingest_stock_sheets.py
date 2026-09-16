"""
tests/test_ingest_stock_sheets.py — regressão para scripts/ingest_stock_sheets.py.

Cobre especificamente a idempotência exigida pelo S2 (Parte 1.4): rodar o
job duas vezes contra a mesma planilha mock não deve duplicar nada em
stock_movements. Nenhuma chamada de rede — o fake abaixo implementa só a
interface que o job usa (`read_movimentos`/`read_contagem`), sem passar
por gspread.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from integrations.axen_sheets import ContagemRow, MovimentoRow, ReadResult, RowError
from scripts.ingest_stock_sheets import ingest_contagem, ingest_movimentos


# ── Fake ──────────────────────────────────────────────────────────────────────

class _FakeSheetsClient:
    """Implementa só read_movimentos/read_contagem — o que o job chama."""

    def __init__(self, movimentos: ReadResult | None = None, contagem: ReadResult | None = None) -> None:
        self._movimentos = movimentos or ReadResult(rows=[], errors=[])
        self._contagem = contagem or ReadResult(rows=[], errors=[])

    def read_movimentos(self, spreadsheet_id, worksheet_name=None):
        return self._movimentos

    def read_contagem(self, spreadsheet_id, worksheet_name=None):
        return self._contagem


def _movimento(row_number, **overrides) -> MovimentoRow:
    fields = dict(
        row_number=row_number, data="2026-09-15", sku_axen="DRIFT-185-AZU",
        tipo="compra_recebida", quantidade=10,
        local_origem="fornecedor", local_destino="estoque_bruto",
        referencia="ped-123", observacao="",
    )
    fields.update(overrides)
    return MovimentoRow(**fields)


def _contagem(row_number, **overrides) -> ContagemRow:
    fields = dict(
        row_number=row_number, data_contagem="2026-09-15", sku_axen="DRIFT-185-AZU",
        local="estoque_pronto", quantidade_contada=6,
        quantidade_sistema=None, responsavel="Thiago", observacao="",
    )
    fields.update(overrides)
    return ContagemRow(**fields)


def _insert_product(db, sku_axen="DRIFT-185-AZU"):
    db.execute(
        "INSERT INTO products (sku_axen, model, created_at, updated_at) VALUES (?, ?, 't', 't')",
        (sku_axen, "Drift"),
    )
    db.commit()


# ── ingest_movimentos ───────────────────────────────────────────────────────────

class TestIngestMovimentos:
    def test_inserts_valid_rows(self, db):
        _insert_product(db)
        client = _FakeSheetsClient(movimentos=ReadResult(rows=[_movimento(1)], errors=[]))
        summary = ingest_movimentos(db, client, "sheet-id")
        assert summary.inserted == 1
        assert summary.skipped_duplicate == 0
        row = db.execute("SELECT * FROM stock_movements").fetchone()
        assert row["sku_axen"] == "DRIFT-185-AZU"
        assert row["fonte"] == "planilha_movimentos"
        assert row["dedupe_key"] == "planilha_movimentos:sheet-id:Movimentos:1"

    def test_running_twice_does_not_duplicate(self, db):
        """S2 Parte 1.4 — idempotência exigida explicitamente."""
        _insert_product(db)
        client = _FakeSheetsClient(movimentos=ReadResult(
            rows=[_movimento(1), _movimento(2, sku_axen="DRIFT-185-AZU")], errors=[]
        ))
        s1 = ingest_movimentos(db, client, "sheet-id")
        s2 = ingest_movimentos(db, client, "sheet-id")

        assert s1.inserted == 2
        assert s2.inserted == 0
        assert s2.skipped_duplicate == 2
        total = db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
        assert total == 2  # não 4

    def test_unknown_sku_reported_not_fatal(self, db):
        """Um sku_axen que não existe em products vira erro reportado, mas
        não derruba o lote inteiro — as outras linhas ainda são gravadas."""
        _insert_product(db, sku_axen="DRIFT-185-AZU")
        client = _FakeSheetsClient(movimentos=ReadResult(rows=[
            _movimento(1, sku_axen="SKU-INEXISTENTE"),
            _movimento(2, sku_axen="DRIFT-185-AZU"),
        ], errors=[]))
        summary = ingest_movimentos(db, client, "sheet-id")
        assert summary.inserted == 1
        assert len(summary.db_errors) == 1
        assert "SKU-INEXISTENTE" in summary.db_errors[0]
        total = db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
        assert total == 1

    def test_sheet_level_errors_counted_and_reported(self, db):
        """Erros já detectados pelo axen_sheets (tipo/local inválido etc.)
        aparecem no resumo sem precisar tocar o banco."""
        client = _FakeSheetsClient(movimentos=ReadResult(
            rows=[], errors=[RowError(row_number=3, message="tipo 'lixo' não reconhecido", raw={})]
        ))
        summary = ingest_movimentos(db, client, "sheet-id")
        assert summary.sheet_errors == 1
        assert summary.inserted == 0
        assert "linha 3" in summary.db_errors[0]

    def test_dry_run_does_not_write(self, db):
        _insert_product(db)
        client = _FakeSheetsClient(movimentos=ReadResult(rows=[_movimento(1)], errors=[]))
        summary = ingest_movimentos(db, client, "sheet-id", dry_run=True)
        assert summary.inserted == 1  # reportado como "faria"
        total = db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
        assert total == 0  # mas nada foi gravado


# ── ingest_contagem ──────────────────────────────────────────────────────────────

class TestIngestContagem:
    def test_first_count_seeds_ledger_as_ajuste_contagem(self, db):
        """Primeira contagem de um (sku, local): saldo antes é 0, delta = valor
        contado inteiro — é isso que 'zera' o ledger no início."""
        _insert_product(db)
        client = _FakeSheetsClient(contagem=ReadResult(
            rows=[_contagem(1, quantidade_contada=6)], errors=[]
        ))
        summary = ingest_contagem(db, client, "sheet-id")
        assert summary.inserted == 1
        row = db.execute("SELECT * FROM stock_movements").fetchone()
        assert row["tipo"] == "ajuste_contagem"
        assert row["quantidade"] == 6
        assert row["local_origem"] == "ajuste"
        assert row["local_destino"] == "estoque_pronto"
        assert row["fonte"] == "planilha_contagem"

    def test_second_count_generates_delta_not_absolute_value(self, db):
        """Segunda contagem do mesmo (sku, local): NÃO deve regravar o valor
        absoluto — só a diferença contra o saldo já existente."""
        _insert_product(db)
        client = _FakeSheetsClient(contagem=ReadResult(rows=[
            _contagem(1, data_contagem="2026-09-01", quantidade_contada=6),
            _contagem(2, data_contagem="2026-09-15", quantidade_contada=9),
        ], errors=[]))
        summary = ingest_contagem(db, client, "sheet-id")
        assert summary.inserted == 2

        rows = db.execute(
            "SELECT quantidade, local_origem, local_destino FROM stock_movements ORDER BY data"
        ).fetchall()
        assert rows[0]["quantidade"] == 6   # seed: 0 -> 6
        assert rows[1]["quantidade"] == 3   # delta: 6 -> 9, não 9 de novo
        assert rows[1]["local_origem"] == "ajuste"
        assert rows[1]["local_destino"] == "estoque_pronto"

    def test_downward_adjustment_direction(self, db):
        """Contagem física MENOR que o saldo calculado: local_origem vira o
        local real, local_destino vira 'ajuste' (Q2)."""
        _insert_product(db)
        client = _FakeSheetsClient(contagem=ReadResult(rows=[
            _contagem(1, data_contagem="2026-09-01", quantidade_contada=10),
            _contagem(2, data_contagem="2026-09-15", quantidade_contada=7),
        ], errors=[]))
        ingest_contagem(db, client, "sheet-id")
        row = db.execute(
            "SELECT * FROM stock_movements WHERE data='2026-09-15'"
        ).fetchone()
        assert row["quantidade"] == 3
        assert row["local_origem"] == "estoque_pronto"
        assert row["local_destino"] == "ajuste"

    def test_matching_count_generates_no_movement(self, db):
        """Contagem que bate exatamente com o saldo do ledger: delta=0, nada
        é gravado (mas a linha ainda conta no resumo)."""
        _insert_product(db)
        client = _FakeSheetsClient(contagem=ReadResult(rows=[
            _contagem(1, data_contagem="2026-09-01", quantidade_contada=6),
            _contagem(2, data_contagem="2026-09-15", quantidade_contada=6),
        ], errors=[]))
        summary = ingest_contagem(db, client, "sheet-id")
        assert summary.inserted == 1
        assert summary.skipped_delta_zero == 1
        total = db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
        assert total == 1

    def test_running_twice_does_not_duplicate(self, db):
        """S2 Parte 1.4 — idempotência, agora pro caminho de Contagem."""
        _insert_product(db)
        client = _FakeSheetsClient(contagem=ReadResult(rows=[
            _contagem(1, data_contagem="2026-09-01", quantidade_contada=6),
            _contagem(2, data_contagem="2026-09-15", quantidade_contada=9),
        ], errors=[]))
        s1 = ingest_contagem(db, client, "sheet-id")
        s2 = ingest_contagem(db, client, "sheet-id")

        assert s1.inserted == 2
        assert s2.inserted == 0
        assert s2.skipped_duplicate == 2
        total = db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
        assert total == 2

    def test_out_of_order_rows_processed_chronologically(self, db):
        """A linha mais recente vem primeiro na planilha — o job deve
        reordenar por data_contagem, não pela ordem das linhas."""
        _insert_product(db)
        client = _FakeSheetsClient(contagem=ReadResult(rows=[
            _contagem(1, data_contagem="2026-09-15", quantidade_contada=9),  # linha 1, mas é a mais recente
            _contagem(2, data_contagem="2026-09-01", quantidade_contada=6),  # linha 2, mas é a mais antiga
        ], errors=[]))
        summary = ingest_contagem(db, client, "sheet-id")
        assert summary.inserted == 2
        rows = db.execute(
            "SELECT data, quantidade FROM stock_movements ORDER BY data"
        ).fetchall()
        assert rows[0]["data"] == "2026-09-01" and rows[0]["quantidade"] == 6
        assert rows[1]["data"] == "2026-09-15" and rows[1]["quantidade"] == 3

    def test_different_local_same_sku_tracked_independently(self, db):
        """(sku, full) e (sku, estoque_pronto) têm saldos independentes —
        contagem de um não deve afetar o delta do outro."""
        _insert_product(db)
        client = _FakeSheetsClient(contagem=ReadResult(rows=[
            _contagem(1, local="full", quantidade_contada=20),
            _contagem(2, local="estoque_pronto", quantidade_contada=6),
        ], errors=[]))
        summary = ingest_contagem(db, client, "sheet-id")
        assert summary.inserted == 2
        full_row = db.execute("SELECT quantidade FROM stock_movements WHERE local_destino='full'").fetchone()
        pronto_row = db.execute("SELECT quantidade FROM stock_movements WHERE local_destino='estoque_pronto'").fetchone()
        assert full_row["quantidade"] == 20
        assert pronto_row["quantidade"] == 6

    def test_dry_run_does_not_write(self, db):
        _insert_product(db)
        client = _FakeSheetsClient(contagem=ReadResult(rows=[_contagem(1)], errors=[]))
        summary = ingest_contagem(db, client, "sheet-id", dry_run=True)
        assert summary.inserted == 1
        total = db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
        assert total == 0
