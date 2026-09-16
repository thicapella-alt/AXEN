"""
integrations/axen_sheets.py — Google Sheets read-only client.

Reads the AXEN-managed Google Sheets ("DRE AXEN" and "Compras") so the VPS
can pull ledger/count data and purchasing data without depending on a
personal Google account. Read-only, plumbing-only: this module parses and
validates rows into typed dataclasses — it does not write to the database
or to Sheets, and it does not compute balances. Persisting `Movimentos`
rows into a `stock_movements` table is out of scope here (S2 session).

Environment variables (all required when GOOGLE_SHEETS_ENABLED=true)
──────────────────────────────────────────────────────────────────────
  GOOGLE_SHEETS_ENABLED            "true" to activate (default: false)
  GOOGLE_SHEETS_CREDENTIALS_PATH   Path to the service-account JSON key file
  GOOGLE_SHEETS_DRE_ID             Spreadsheet ID of "DRE AXEN"
                                    (holds the Movimentos/Contagem tabs)
  GOOGLE_SHEETS_COMPRAS_ID         Spreadsheet ID of "Compras"
                                    (may be the same ID as DRE_ID if it's
                                    a tab in the same file — both env vars
                                    are read independently either way)

Authentication
──────────────
  Service-account JSON key, scoped to Sheets (read-only) + Drive
  (metadata read-only, needed by gspread to open by key). See
  docs/movimentos-contagem-sheets.md for the exact Google Cloud Console
  steps — this module never creates the service account, only consumes
  the key once it exists.

Dependency injection
────────────────────
  AxenSheetsClient(client=...) accepts a pre-built gspread-compatible
  client. Pass a fake in tests to avoid real network calls / credentials;
  see tests/integrations/test_axen_sheets.py for the fake shape (it only
  needs `.open_by_key(id).worksheet(name).get_all_records()`).

Row validation
──────────────
  Each raw row (a dict keyed by the sheet's header row) is parsed into a
  MovimentoRow / ContagemRow dataclass. A row that fails validation
  (unknown `tipo`/`local`, non-numeric or non-positive `quantidade`,
  empty `sku_axen`) is skipped and reported in `errors` instead of
  raising — one bad hand-entered row should never block reading the rest
  of the sheet.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)

_ENV_VAR = "GOOGLE_SHEETS_ENABLED"

DEFAULT_MOVIMENTOS_SHEET = "Movimentos"
DEFAULT_CONTAGEM_SHEET = "Contagem"

# Vocabulário fechado — mantido em sincronia com docs/movimentos-contagem-sheets.md
# e com o CHECK constraint que a tabela stock_movements (S2) vai aplicar.
ALLOWED_MOVEMENT_TYPES = frozenset({
    "compra_recebida",
    "para_laser",
    "de_laser",
    "envio_full",
    "recebido_full",
    "venda_ml",
    "devolucao",
    "ajuste_contagem",
    "brinde",
    "perda",
})

ALLOWED_LOCATIONS = frozenset({
    "fornecedor",
    "estoque_bruto",
    "laser",
    "estoque_pronto",
    "full",
    "cliente",
    "perda",
    "ajuste",
})


class SheetsDisabledError(Exception):
    """Raised when AxenSheetsClient is used but GOOGLE_SHEETS_ENABLED is off."""


def is_enabled() -> bool:
    """Return True if the Google Sheets integration is enabled via env var."""
    return os.getenv(_ENV_VAR, "false").lower() == "true"


# ── Row dataclasses ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MovimentoRow:
    data: str
    sku_axen: str
    tipo: str
    quantidade: int
    local_origem: str
    local_destino: str
    referencia: str = ""
    observacao: str = ""


@dataclass(frozen=True)
class ContagemRow:
    data_contagem: str
    sku_axen: str
    local: str
    quantidade_contada: int
    quantidade_sistema: Optional[int] = None
    responsavel: str = ""
    observacao: str = ""


@dataclass(frozen=True)
class RowError:
    row_number: int  # 1-based, counting from the first data row (header excluded)
    message: str
    raw: dict


@dataclass(frozen=True)
class ReadResult:
    rows: list  # list[MovimentoRow] | list[ContagemRow]
    errors: list  # list[RowError]


# ── Parsing helpers ──────────────────────────────────────────────────────────

def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_positive_int(value: Any) -> Optional[int]:
    """Parse a quantity: accepts int, float-like string, BR comma decimal.
    Returns None if not a positive integer."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip().replace(",", ".")
    try:
        as_float = float(text)
    except ValueError:
        return None
    as_int = int(as_float)
    if as_int != as_float or as_int <= 0:
        return None
    return as_int


def _parse_optional_nonneg_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    text = str(value).strip().replace(",", ".")
    try:
        as_float = float(text)
    except ValueError:
        return None
    as_int = int(as_float)
    if as_int != as_float or as_int < 0:
        return None
    return as_int


def parse_movimento_row(raw: dict) -> MovimentoRow:
    """
    Parse+validate one raw Movimentos row (as returned by
    gspread's get_all_records — a dict keyed by header).

    Raises ValueError with a human-readable message on any invalid field.
    """
    sku_axen = _clean_str(raw.get("sku_axen"))
    if not sku_axen:
        raise ValueError("sku_axen vazio")

    tipo = _clean_str(raw.get("tipo")).lower()
    if tipo not in ALLOWED_MOVEMENT_TYPES:
        raise ValueError(
            f"tipo '{raw.get('tipo')!r}' não reconhecido "
            f"(esperado um de: {', '.join(sorted(ALLOWED_MOVEMENT_TYPES))})"
        )

    quantidade = _parse_positive_int(raw.get("quantidade"))
    if quantidade is None:
        raise ValueError(f"quantidade '{raw.get('quantidade')!r}' inválida (esperado inteiro > 0)")

    local_origem = _clean_str(raw.get("local_origem")).lower()
    local_destino = _clean_str(raw.get("local_destino")).lower()
    if local_origem not in ALLOWED_LOCATIONS:
        raise ValueError(
            f"local_origem '{raw.get('local_origem')!r}' não reconhecido "
            f"(esperado um de: {', '.join(sorted(ALLOWED_LOCATIONS))})"
        )
    if local_destino not in ALLOWED_LOCATIONS:
        raise ValueError(
            f"local_destino '{raw.get('local_destino')!r}' não reconhecido "
            f"(esperado um de: {', '.join(sorted(ALLOWED_LOCATIONS))})"
        )

    data = _clean_str(raw.get("data"))
    if not data:
        raise ValueError("data vazia")

    return MovimentoRow(
        data=data,
        sku_axen=sku_axen,
        tipo=tipo,
        quantidade=quantidade,
        local_origem=local_origem,
        local_destino=local_destino,
        referencia=_clean_str(raw.get("referencia")),
        observacao=_clean_str(raw.get("observacao")),
    )


def parse_contagem_row(raw: dict) -> ContagemRow:
    """Parse+validate one raw Contagem row. Raises ValueError on invalid fields."""
    sku_axen = _clean_str(raw.get("sku_axen"))
    if not sku_axen:
        raise ValueError("sku_axen vazio")

    local = _clean_str(raw.get("local")).lower()
    if local not in ALLOWED_LOCATIONS:
        raise ValueError(
            f"local '{raw.get('local')!r}' não reconhecido "
            f"(esperado um de: {', '.join(sorted(ALLOWED_LOCATIONS))})"
        )

    quantidade_contada = _parse_optional_nonneg_int(raw.get("quantidade_contada"))
    if quantidade_contada is None:
        raise ValueError(
            f"quantidade_contada '{raw.get('quantidade_contada')!r}' inválida (esperado inteiro >= 0)"
        )

    quantidade_sistema = _parse_optional_nonneg_int(raw.get("quantidade_sistema"))

    data_contagem = _clean_str(raw.get("data_contagem"))
    if not data_contagem:
        raise ValueError("data_contagem vazia")

    return ContagemRow(
        data_contagem=data_contagem,
        sku_axen=sku_axen,
        local=local,
        quantidade_contada=quantidade_contada,
        quantidade_sistema=quantidade_sistema,
        responsavel=_clean_str(raw.get("responsavel")),
        observacao=_clean_str(raw.get("observacao")),
    )


# ── Client ───────────────────────────────────────────────────────────────────

class AxenSheetsClient:
    """
    Read-only Google Sheets client for AXEN's ledger/count/purchasing sheets.

    Parameters
    ----------
    client:
        Optional pre-built gspread-compatible client. Must support
        `.open_by_key(spreadsheet_id).worksheet(name).get_all_records()`.
        Useful for unit tests — pass a fake to avoid real network calls
        and real credentials. When omitted, a real gspread client is
        built lazily (on first use) from GOOGLE_SHEETS_CREDENTIALS_PATH,
        so importing/instantiating this class never requires credentials
        to be present (only actually reading does).
    """

    def __init__(self, client: Any = None) -> None:
        self._client = client

    def check_enabled(self) -> None:
        if not is_enabled():
            raise SheetsDisabledError(
                f"Integração Google Sheets desabilitada. Defina {_ENV_VAR}=true para ativar."
            )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        self.check_enabled()
        creds_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH")
        if not creds_path:
            raise SheetsDisabledError(
                "GOOGLE_SHEETS_CREDENTIALS_PATH não definido — veja "
                "docs/movimentos-contagem-sheets.md para os passos de setup."
            )
        # Lazy import: gspread/google-auth are only required when this
        # module actually needs to talk to Google, not at import time.
        import gspread  # noqa: PLC0415

        self._client = gspread.service_account(filename=creds_path)
        return self._client

    def _read_worksheet_records(self, spreadsheet_id: str, worksheet_name: str) -> list[dict]:
        client = self._get_client()
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(worksheet_name)
        return worksheet.get_all_records()

    def read_movimentos(
        self,
        spreadsheet_id: str,
        worksheet_name: str = DEFAULT_MOVIMENTOS_SHEET,
    ) -> ReadResult:
        """
        Read + validate every row of the Movimentos worksheet.

        Returns ReadResult(rows=[MovimentoRow, ...], errors=[RowError, ...]).
        A row that fails validation is skipped and reported in `errors`
        instead of aborting the whole read.
        """
        raw_rows = self._read_worksheet_records(spreadsheet_id, worksheet_name)
        rows: list[MovimentoRow] = []
        errors: list[RowError] = []
        for i, raw in enumerate(raw_rows, start=1):
            try:
                rows.append(parse_movimento_row(raw))
            except ValueError as exc:
                errors.append(RowError(row_number=i, message=str(exc), raw=raw))
        if errors:
            log.warning(
                "read_movimentos: %d/%d linha(s) inválida(s) em '%s'.",
                len(errors), len(raw_rows), worksheet_name,
            )
        return ReadResult(rows=rows, errors=errors)

    def read_contagem(
        self,
        spreadsheet_id: str,
        worksheet_name: str = DEFAULT_CONTAGEM_SHEET,
    ) -> ReadResult:
        """
        Read + validate every row of the Contagem worksheet.

        Returns ReadResult(rows=[ContagemRow, ...], errors=[RowError, ...]).
        """
        raw_rows = self._read_worksheet_records(spreadsheet_id, worksheet_name)
        rows: list[ContagemRow] = []
        errors: list[RowError] = []
        for i, raw in enumerate(raw_rows, start=1):
            try:
                rows.append(parse_contagem_row(raw))
            except ValueError as exc:
                errors.append(RowError(row_number=i, message=str(exc), raw=raw))
        if errors:
            log.warning(
                "read_contagem: %d/%d linha(s) inválida(s) em '%s'.",
                len(errors), len(raw_rows), worksheet_name,
            )
        return ReadResult(rows=rows, errors=errors)
