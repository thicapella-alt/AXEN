"""
tests/test_import_products.py — regressão para scripts/import_products.py.

_parse_numeric precisa aceitar tanto '18.50' quanto '18,50' (vírgula é o
separador decimal comum em planilha BR/Excel) — bug real encontrado e
corrigido antes do 1º uso: a versão inicial silenciosamente descartava
valores com vírgula, virando None em vez do número.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.import_products import _parse_active, _parse_numeric


class TestParseNumeric:
    def test_dot_decimal(self):
        assert _parse_numeric("18.50") == 18.5

    def test_comma_decimal(self):
        assert _parse_numeric("18,50") == 18.5

    def test_plain_integer(self):
        assert _parse_numeric("20") == 20
        assert isinstance(_parse_numeric("20"), int)

    def test_empty_string_is_none(self):
        assert _parse_numeric("") is None
        assert _parse_numeric("   ") is None

    def test_garbage_is_none(self):
        assert _parse_numeric("abc") is None


class TestParseActive:
    def test_truthy_values(self):
        for v in ["1", "true", "TRUE", "sim", "Sim", "ativo"]:
            assert _parse_active(v, default=0) == 1

    def test_falsy_values(self):
        for v in ["0", "false", "nao", "não", "inativo"]:
            assert _parse_active(v, default=1) == 0

    def test_unrecognized_value_keeps_default(self):
        assert _parse_active("???", default=1) == 1
        assert _parse_active("", default=0) == 0
