"""
tests/integrations/test_axen_sheets.py

Unit tests for AxenSheetsClient and the Movimentos/Contagem row parsers.

All Sheets calls are intercepted via client dependency injection:
  AxenSheetsClient(client=<fake gspread client>)

No network calls, no real credentials, no dependency on gspread being
importable for the tests that only exercise the client-injected path
(the lazy `import gspread` in _get_client() is only reached when no
client is injected and Sheets is enabled).
"""

from __future__ import annotations

import pytest

from integrations.axen_sheets import (
    ALLOWED_LOCATIONS,
    ALLOWED_MOVEMENT_TYPES,
    AxenSheetsClient,
    ContagemRow,
    MovimentoRow,
    SheetsDisabledError,
    is_enabled,
    parse_contagem_row,
    parse_movimento_row,
)


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeWorksheet:
    def __init__(self, records: list[dict]) -> None:
        self._records = records

    def get_all_records(self) -> list[dict]:
        return self._records


class _FakeSpreadsheet:
    def __init__(self, worksheets: dict[str, _FakeWorksheet]) -> None:
        self._worksheets = worksheets

    def worksheet(self, name: str) -> _FakeWorksheet:
        return self._worksheets[name]


class _FakeClient:
    """Fake gspread.Client — supports only what AxenSheetsClient calls."""

    def __init__(self, spreadsheets: dict[str, _FakeSpreadsheet]) -> None:
        self._spreadsheets = spreadsheets

    def open_by_key(self, spreadsheet_id: str) -> _FakeSpreadsheet:
        return self._spreadsheets[spreadsheet_id]


def _build_client(sheet_id: str, worksheet_name: str, records: list[dict]) -> _FakeClient:
    return _FakeClient({sheet_id: _FakeSpreadsheet({worksheet_name: _FakeWorksheet(records)})})


# ── Fixture rows ──────────────────────────────────────────────────────────────

def _movimento_raw(**overrides) -> dict:
    row = {
        "data": "2026-09-15 10:30",
        "sku_axen": "DRIFT-185-AZU",
        "tipo": "compra_recebida",
        "quantidade": 10,
        "local_origem": "fornecedor",
        "local_destino": "estoque_bruto",
        "referencia": "8213826502636903",
        "observacao": "Lote recebido via Correios",
    }
    row.update(overrides)
    return row


def _contagem_raw(**overrides) -> dict:
    row = {
        "data_contagem": "2026-09-15",
        "sku_axen": "DRIFT-185-AZU",
        "local": "estoque_pronto",
        "quantidade_contada": 6,
        "quantidade_sistema": "",
        "responsavel": "Thiago",
        "observacao": "",
    }
    row.update(overrides)
    return row


# ── Vocabulary sanity ──────────────────────────────────────────────────────────

class TestVocabulary:
    def test_allowed_movement_types_match_scope(self):
        assert ALLOWED_MOVEMENT_TYPES == {
            "compra_recebida", "para_laser", "de_laser", "envio_full",
            "recebido_full", "venda_ml", "devolucao", "ajuste_contagem",
            "brinde", "perda",
        }

    def test_allowed_locations_nonempty(self):
        assert "estoque_bruto" in ALLOWED_LOCATIONS
        assert "full" in ALLOWED_LOCATIONS


# ── is_enabled / feature flag ──────────────────────────────────────────────────

class TestIsEnabled:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_SHEETS_ENABLED", raising=False)
        assert is_enabled() is False

    def test_enabled_when_true(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_SHEETS_ENABLED", "true")
        assert is_enabled() is True

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_SHEETS_ENABLED", "TRUE")
        assert is_enabled() is True

    def test_check_enabled_raises_without_injected_client(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_SHEETS_ENABLED", "false")
        client = AxenSheetsClient()
        with pytest.raises(SheetsDisabledError):
            client.read_movimentos("sheet-id")

    def test_injected_client_bypasses_flag(self, monkeypatch):
        """An injected client is used as-is — the enabled check only gates
        the lazy real-gspread-client path, so tests never need the flag."""
        monkeypatch.delenv("GOOGLE_SHEETS_ENABLED", raising=False)
        fake = _build_client("sheet-id", "Movimentos", [_movimento_raw()])
        client = AxenSheetsClient(client=fake)
        result = client.read_movimentos("sheet-id")
        assert len(result.rows) == 1


# ── parse_movimento_row ─────────────────────────────────────────────────────────

class TestParseMovimentoRow:
    def test_valid_row(self):
        row = parse_movimento_row(_movimento_raw())
        assert row == MovimentoRow(
            data="2026-09-15 10:30",
            sku_axen="DRIFT-185-AZU",
            tipo="compra_recebida",
            quantidade=10,
            local_origem="fornecedor",
            local_destino="estoque_bruto",
            referencia="8213826502636903",
            observacao="Lote recebido via Correios",
        )

    def test_missing_sku_raises(self):
        with pytest.raises(ValueError, match="sku_axen"):
            parse_movimento_row(_movimento_raw(sku_axen=""))

    def test_unknown_tipo_raises(self):
        with pytest.raises(ValueError, match="tipo"):
            parse_movimento_row(_movimento_raw(tipo="venda_magica"))

    def test_unknown_local_origem_raises(self):
        with pytest.raises(ValueError, match="local_origem"):
            parse_movimento_row(_movimento_raw(local_origem="garagem"))

    def test_unknown_local_destino_raises(self):
        with pytest.raises(ValueError, match="local_destino"):
            parse_movimento_row(_movimento_raw(local_destino="garagem"))

    def test_zero_quantidade_raises(self):
        with pytest.raises(ValueError, match="quantidade"):
            parse_movimento_row(_movimento_raw(quantidade=0))

    def test_negative_quantidade_raises(self):
        with pytest.raises(ValueError, match="quantidade"):
            parse_movimento_row(_movimento_raw(quantidade=-5))

    def test_non_numeric_quantidade_raises(self):
        with pytest.raises(ValueError, match="quantidade"):
            parse_movimento_row(_movimento_raw(quantidade="dez"))

    def test_non_integer_quantidade_raises(self):
        with pytest.raises(ValueError, match="quantidade"):
            parse_movimento_row(_movimento_raw(quantidade="2.5"))

    def test_quantidade_as_br_comma_string(self):
        row = parse_movimento_row(_movimento_raw(quantidade="10,0"))
        assert row.quantidade == 10

    def test_tipo_is_lowercased(self):
        row = parse_movimento_row(_movimento_raw(tipo="COMPRA_RECEBIDA"))
        assert row.tipo == "compra_recebida"

    def test_empty_data_raises(self):
        with pytest.raises(ValueError, match="data"):
            parse_movimento_row(_movimento_raw(data=""))


# ── parse_contagem_row ───────────────────────────────────────────────────────────

class TestParseContagemRow:
    def test_valid_row(self):
        row = parse_contagem_row(_contagem_raw())
        assert row == ContagemRow(
            data_contagem="2026-09-15",
            sku_axen="DRIFT-185-AZU",
            local="estoque_pronto",
            quantidade_contada=6,
            quantidade_sistema=None,
            responsavel="Thiago",
            observacao="",
        )

    def test_quantidade_sistema_optional_blank(self):
        row = parse_contagem_row(_contagem_raw(quantidade_sistema=""))
        assert row.quantidade_sistema is None

    def test_quantidade_sistema_filled(self):
        row = parse_contagem_row(_contagem_raw(quantidade_sistema=8))
        assert row.quantidade_sistema == 8

    def test_zero_quantidade_contada_is_valid(self):
        """Unlike Movimentos.quantidade, a physical count of zero is legitimate."""
        row = parse_contagem_row(_contagem_raw(quantidade_contada=0))
        assert row.quantidade_contada == 0

    def test_negative_quantidade_contada_raises(self):
        with pytest.raises(ValueError, match="quantidade_contada"):
            parse_contagem_row(_contagem_raw(quantidade_contada=-1))

    def test_unknown_local_raises(self):
        with pytest.raises(ValueError, match="local"):
            parse_contagem_row(_contagem_raw(local="depósito da vizinha"))

    def test_missing_sku_raises(self):
        with pytest.raises(ValueError, match="sku_axen"):
            parse_contagem_row(_contagem_raw(sku_axen=""))

    def test_empty_data_contagem_raises(self):
        with pytest.raises(ValueError, match="data_contagem"):
            parse_contagem_row(_contagem_raw(data_contagem=""))


# ── AxenSheetsClient.read_movimentos / read_contagem ────────────────────────────

class TestReadMovimentos:
    def test_reads_valid_rows(self):
        fake = _build_client("sheet-id", "Movimentos", [_movimento_raw(), _movimento_raw(sku_axen="TAG-19-PTO")])
        client = AxenSheetsClient(client=fake)
        result = client.read_movimentos("sheet-id")
        assert len(result.rows) == 2
        assert result.errors == []

    def test_bad_row_reported_not_raised(self):
        rows = [_movimento_raw(), _movimento_raw(tipo="lixo")]
        fake = _build_client("sheet-id", "Movimentos", rows)
        client = AxenSheetsClient(client=fake)
        result = client.read_movimentos("sheet-id")
        assert len(result.rows) == 1
        assert len(result.errors) == 1
        assert result.errors[0].row_number == 2
        assert "tipo" in result.errors[0].message

    def test_custom_worksheet_name(self):
        fake = _build_client("sheet-id", "MovimentosTeste", [_movimento_raw()])
        client = AxenSheetsClient(client=fake)
        result = client.read_movimentos("sheet-id", worksheet_name="MovimentosTeste")
        assert len(result.rows) == 1

    def test_empty_sheet_returns_empty_result(self):
        fake = _build_client("sheet-id", "Movimentos", [])
        client = AxenSheetsClient(client=fake)
        result = client.read_movimentos("sheet-id")
        assert result.rows == []
        assert result.errors == []


class TestReadContagem:
    def test_reads_valid_rows(self):
        fake = _build_client("sheet-id", "Contagem", [_contagem_raw()])
        client = AxenSheetsClient(client=fake)
        result = client.read_contagem("sheet-id")
        assert len(result.rows) == 1
        assert result.errors == []

    def test_bad_row_reported_not_raised(self):
        rows = [_contagem_raw(), _contagem_raw(local="nave-mãe")]
        fake = _build_client("sheet-id", "Contagem", rows)
        client = AxenSheetsClient(client=fake)
        result = client.read_contagem("sheet-id")
        assert len(result.rows) == 1
        assert len(result.errors) == 1
        assert result.errors[0].row_number == 2
