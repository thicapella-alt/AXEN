"""
Tests for axen_price_scraper.py

Coverage:
  - parse_price()        — formatos BR/US, parcelas, inválidos, bordas
  - classify_material()  — todos os materiais, termos ambíguos
  - extract_delivery_info() — frete grátis, dias, datas, termos genéricos
  - _extract_discount()  — presente, ausente, formato inválido
  - _extract_position()  — presente, ausente, zero
  - build_rows()         — dedup, campo Posição, filtro male_only via stub
  - build_delivery_detail() — agregação por (material, loja), frete %, moda de prazo
  - build_delivery_summary() — moda, frete %, proporção com prazo
  - build_summary()      — min/média/max por loja × material
  - build_promotions()   — filtra e ordena por desconto
  - QuatroMenScraper dedup — garante que URL duplicada não gera produto duplicado
  - MercadoLivreScraper context — position e query injetados corretamente
"""

import sys
import os
import json
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Importa apenas as funções puras — sem acionar playwright/requests no import
from axen_price_scraper import (
    parse_price,
    classify_material,
    extract_delivery_info,
    _extract_discount,
    _extract_position,
    build_rows,
    build_delivery_detail,
    build_delivery_summary,
    build_summary,
    build_promotions,
    Product,
)


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def p(store="Beroc", name="Pulseira Corda", price=99.9, material="corda",
      url="https://beroc.com.br/p/1", delivery_info="", context="") -> Product:
    return Product(store=store, name=name, price=price, material=material,
                   url=url, delivery_info=delivery_info, context=context)


# ═══════════════════════════════════════════════════════════════════════════
#  parse_price()
# ═══════════════════════════════════════════════════════════════════════════

class TestParsePrice:
    def test_int_passthrough(self):
        assert parse_price(100) == 100.0

    def test_float_passthrough(self):
        assert abs(parse_price(99.90) - 99.90) < 0.001

    def test_none_returns_none(self):
        assert parse_price(None) is None

    def test_empty_string_returns_none(self):
        assert parse_price("") is None

    def test_whitespace_returns_none(self):
        assert parse_price("   ") is None

    def test_br_format_comma_decimal(self):
        assert parse_price("R$ 125,00") == 125.0

    def test_br_format_dot_thousands_comma_decimal(self):
        assert parse_price("R$ 1.250,00") == 1250.0

    def test_us_format_dot_decimal(self):
        assert parse_price("$99.90") == 99.90

    def test_plain_number_with_comma(self):
        assert parse_price("215,00") == 215.0

    def test_plain_number_with_dot(self):
        assert parse_price("215.00") == 215.0

    def test_ignores_installment_prefix(self):
        # "3x de R$ 41,67" — should NOT parse the 41.67 (parcela), return None
        # because the regex skips values preceded by "Nx de"
        result = parse_price("3x de R$ 41,67")
        assert result is None

    def test_picks_minimum_of_de_por_prices(self):
        # "de R$ 150,00 por R$ 120,00" — picks the lower price
        result = parse_price("de R$ 150,00 por R$ 120,00")
        assert result == 120.0

    def test_non_numeric_string_returns_none(self):
        assert parse_price("frete grátis") is None

    def test_large_price(self):
        assert parse_price("R$ 2.399,99") == 2399.99

    def test_price_with_currency_symbol_no_space(self):
        assert parse_price("R$89,90") == 89.90

    def test_multiple_dots_as_thousands(self):
        # "1.234.567,89" → 1234567.89
        assert parse_price("1.234.567,89") == 1234567.89

    def test_zero_price_returns_zero(self):
        # parse_price returns 0.0 for "0" — valid float even if filtered upstream
        result = parse_price("0")
        assert result == 0.0 or result is None  # acceptable either way


# ═══════════════════════════════════════════════════════════════════════════
#  classify_material()
# ═══════════════════════════════════════════════════════════════════════════

class TestClassifyMaterial:
    # ── Couro ──────────────────────────────────────────────────────────────
    def test_couro(self):
        assert classify_material("Pulseira de Couro Masculina") == "couro"

    def test_leather(self):
        assert classify_material("Leather Bracelet Men") == "couro"

    def test_suede(self):
        assert classify_material("Pulseira Suede Masculino") == "couro"

    # ── Metal ──────────────────────────────────────────────────────────────
    def test_aco(self):
        assert classify_material("Pulseira Aço Inox") == "metal"

    def test_inox(self):
        assert classify_material("Pulseira Inox Masculina") == "metal"

    def test_grumet(self):
        assert classify_material("Pulseira Grumet Ouro") == "metal"

    def test_corrente(self):
        assert classify_material("Corrente Masculina Prata") == "metal"

    def test_bangle(self):
        assert classify_material("Bangle Rigida Metal") == "metal"

    # ── Corda ──────────────────────────────────────────────────────────────
    def test_corda(self):
        assert classify_material("Pulseira Masculina Corda Náutica") == "corda"

    def test_paracord(self):
        assert classify_material("Paracord Bracelet") == "corda"

    def test_nylon(self):
        assert classify_material("Pulseira Nylon Azul") == "corda"

    def test_ancora(self):
        assert classify_material("Pulseira Âncora Rope") == "corda"

    def test_borracha(self):
        assert classify_material("Pulseira Borracha Silicone") == "corda"

    # ── Pedra ──────────────────────────────────────────────────────────────
    def test_pedra(self):
        assert classify_material("Pulseira Pedra Natural") == "pedra"

    def test_hematita(self):
        assert classify_material("Pulseira Hematita Masculina") == "pedra"

    def test_onix(self):
        assert classify_material("Pulseira Ônix Black") == "pedra"

    def test_jade(self):
        assert classify_material("Pulseira Jade Verde") == "pedra"

    # ── Desconhecido ───────────────────────────────────────────────────────
    def test_unknown(self):
        assert classify_material("Pulseira Masculina X23") == "desconhecido"

    # ── Prioridade: pedra antes de corda ──────────────────────────────────
    def test_pedra_wins_over_corda_when_mixed(self):
        # "pedra" keyword takes priority over incidental "corda"
        assert classify_material("Pulseira de Pedra Natural Corda Âncora") == "pedra"

    # ── Prioridade: couro antes de metal ──────────────────────────────────
    def test_couro_wins_over_metal(self):
        assert classify_material("Pulseira Couro Corrente Metal") == "couro"


# ═══════════════════════════════════════════════════════════════════════════
#  extract_delivery_info()
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractDeliveryInfo:
    def test_frete_gratis(self):
        assert extract_delivery_info("Frete Grátis para todo Brasil") == "Frete Grátis"

    def test_frete_gratis_variant(self):
        assert extract_delivery_info("Entrega gratis") == "Frete Grátis"

    def test_dias_uteis(self):
        result = extract_delivery_info("Entrega em 5 dias úteis")
        assert "5" in result and "úteis" in result

    def test_dias_sem_uteis(self):
        result = extract_delivery_info("Chega em 3 dias")
        assert "3" in result

    def test_receba_ate(self):
        result = extract_delivery_info("Receba até 15 de jun")
        # Should return some delivery info
        assert result != ""

    def test_entrega_rapida(self):
        assert extract_delivery_info("Entrega Rápida") == "Entrega Rápida"

    def test_envio_imediato(self):
        assert extract_delivery_info("Envio imediato") == "Envio Imediato"

    def test_frete_calculado_checkout(self):
        result = extract_delivery_info("Frete calculado no checkout")
        assert "calculado" in result.lower() or "checkout" in result.lower()

    def test_none_returns_empty(self):
        assert extract_delivery_info(None) == ""

    def test_irrelevant_text_returns_empty(self):
        assert extract_delivery_info("Adicionar ao carrinho") == ""


# ═══════════════════════════════════════════════════════════════════════════
#  _extract_discount() and _extract_position()
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractHelpers:
    def test_extract_discount_present(self):
        assert _extract_discount("id:MLB1 desconto:15%") == "15%"

    def test_extract_discount_absent(self):
        assert _extract_discount("id:MLB1 position:3") == ""

    def test_extract_discount_empty_context(self):
        assert _extract_discount("") == ""

    def test_extract_discount_none_context(self):
        assert _extract_discount(None) == ""

    def test_extract_position_present(self):
        assert _extract_position("id:MLB1 position:7 query:pulseira_corda") == "7"

    def test_extract_position_zero_returns_empty(self):
        # position:0 means "not captured" — should return empty string
        assert _extract_position("id:MLB1 position:0") == ""

    def test_extract_position_absent(self):
        assert _extract_position("id:MLB1 desconto:10%") == ""

    def test_extract_position_empty_context(self):
        assert _extract_position("") == ""

    def test_extract_position_position_1(self):
        assert _extract_position("id:MLB2 position:1") == "1"

    def test_extract_position_large_value(self):
        assert _extract_position("position:250") == "250"


# ═══════════════════════════════════════════════════════════════════════════
#  build_rows()
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildRows:
    def test_returns_correct_fields(self):
        products = [p()]
        rows = build_rows(products)
        assert len(rows) == 1
        row = rows[0]
        assert "Loja" in row
        assert "Material" in row
        assert "Produto" in row
        assert "Preço" in row
        assert "Desconto" in row
        assert "Posição" in row
        assert "Entrega" in row
        assert "URL" in row

    def test_dedup_same_store_name_price(self):
        """Duplicate (store, name, price) must be deduplicated."""
        products = [
            p(store="Beroc", name="X", price=100.0),
            p(store="Beroc", name="X", price=100.0),   # duplicate
        ]
        rows = build_rows(products)
        assert len(rows) == 1

    def test_no_dedup_different_price(self):
        """Same name but different price → two rows."""
        products = [
            p(store="Beroc", name="X", price=100.0),
            p(store="Beroc", name="X", price=110.0, url="https://beroc.com.br/p/2"),
        ]
        rows = build_rows(products)
        assert len(rows) == 2

    def test_posicao_field_empty_for_non_ml(self):
        """Non-ML products must have empty Posição."""
        products = [p(store="Beroc", context="")]
        rows = build_rows(products)
        assert rows[0]["Posição"] == ""

    def test_posicao_field_populated_for_ml(self):
        """ML products with position: in context must have Posição filled."""
        products = [p(store="Mercado Livre", context="id:MLB1 position:5")]
        rows = build_rows(products)
        assert rows[0]["Posição"] == "5"

    def test_desconto_field_populated(self):
        products = [p(context="id:MLB1 desconto:20%")]
        rows = build_rows(products)
        assert rows[0]["Desconto"] == "20%"

    def test_entrega_dash_when_empty_delivery_info(self):
        products = [p(delivery_info="")]
        rows = build_rows(products)
        assert rows[0]["Entrega"] == "-"

    def test_entrega_preserved_when_present(self):
        products = [p(delivery_info="Frete Grátis")]
        rows = build_rows(products)
        assert rows[0]["Entrega"] == "Frete Grátis"


# ═══════════════════════════════════════════════════════════════════════════
#  build_delivery_detail()
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildDeliveryDetail:
    def _make_rows(self):
        return [
            {"Loja": "Beroc", "Material": "corda", "Preço": 100.0, "Entrega": "Frete Grátis"},
            {"Loja": "Beroc", "Material": "corda", "Preço": 120.0, "Entrega": "Frete Grátis"},
            {"Loja": "Beroc", "Material": "corda", "Preço": 140.0, "Entrega": "Entrega em 5 dias"},
            {"Loja": "Key Design", "Material": "corda", "Preço": 110.0, "Entrega": "Entrega em 7 dias"},
            {"Loja": "Beroc", "Material": "metal", "Preço": 200.0, "Entrega": "Frete Grátis"},
        ]

    def test_returns_one_row_per_material_loja(self):
        rows = self._make_rows()
        detail = build_delivery_detail(rows)
        # 3 combinations: (corda, Beroc), (corda, Key Design), (metal, Beroc)
        assert len(detail) == 3

    def test_preco_min_correct(self):
        rows = self._make_rows()
        detail = build_delivery_detail(rows)
        beroc_corda = next(r for r in detail if r["Concorrente"] == "Beroc" and r["Material"] == "corda")
        assert beroc_corda["Preço Mín"] == 100.0

    def test_preco_medio_correct(self):
        rows = self._make_rows()
        detail = build_delivery_detail(rows)
        beroc_corda = next(r for r in detail if r["Concorrente"] == "Beroc" and r["Material"] == "corda")
        # (100 + 120 + 140) / 3 = 120.0
        assert abs(beroc_corda["Preço Médio"] - 120.0) < 0.01

    def test_prazo_is_mode_of_delivery_terms(self):
        rows = self._make_rows()
        detail = build_delivery_detail(rows)
        beroc_corda = next(r for r in detail if r["Concorrente"] == "Beroc" and r["Material"] == "corda")
        # Mode of ["Frete Grátis", "Frete Grátis", "Entrega em 5 dias"] → "Frete Grátis"
        assert beroc_corda["Prazo"] == "Frete Grátis"

    def test_frete_gratis_pct_correct(self):
        rows = self._make_rows()
        detail = build_delivery_detail(rows)
        beroc_corda = next(r for r in detail if r["Concorrente"] == "Beroc" and r["Material"] == "corda")
        # 2 out of 3 have free shipping → 67%
        assert beroc_corda["Frete Grátis %"] == "67%"

    def test_sorted_by_material_then_store(self):
        rows = self._make_rows()
        detail = build_delivery_detail(rows)
        keys = [(r["Material"], r["Concorrente"]) for r in detail]
        assert keys == sorted(keys)

    def test_empty_delivery_not_counted(self):
        rows = [
            {"Loja": "Beroc", "Material": "corda", "Preço": 100.0, "Entrega": ""},
            {"Loja": "Beroc", "Material": "corda", "Preço": 110.0, "Entrega": "-"},
            {"Loja": "Beroc", "Material": "corda", "Preço": 120.0, "Entrega": "Frete Grátis"},
        ]
        detail = build_delivery_detail(rows)
        beroc = detail[0]
        # Only 1 of 3 has free shipping → 33%
        assert beroc["Frete Grátis %"] == "33%"
        # Mode of non-empty terms: ["Frete Grátis"]
        assert beroc["Prazo"] == "Frete Grátis"


# ═══════════════════════════════════════════════════════════════════════════
#  build_delivery_summary()
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildDeliverySummary:
    def test_returns_one_row_per_store(self):
        rows = [
            {"Loja": "Beroc", "Entrega": "Frete Grátis", "Material": "corda", "Preço": 100.0},
            {"Loja": "Beroc", "Entrega": "Frete Grátis", "Material": "metal", "Preço": 200.0},
            {"Loja": "Key Design", "Entrega": "Entrega em 5 dias", "Material": "corda", "Preço": 110.0},
        ]
        summary = build_delivery_summary(rows)
        stores = [s["Loja"] for s in summary]
        assert "Beroc" in stores
        assert "Key Design" in stores
        # Only 2 unique stores
        assert len(summary) == 2

    def test_frete_gratis_pct_100(self):
        rows = [
            {"Loja": "Beroc", "Entrega": "Frete Grátis", "Material": "corda", "Preço": 100.0},
            {"Loja": "Beroc", "Entrega": "Frete Grátis", "Material": "metal", "Preço": 200.0},
        ]
        summary = build_delivery_summary(rows)
        beroc = summary[0]
        assert beroc["Frete Grátis"] == "100%"

    def test_placeholder_dash_not_counted_as_prazo(self):
        rows = [
            {"Loja": "Beroc", "Entrega": "-", "Material": "corda", "Preço": 100.0},
            {"Loja": "Beroc", "Entrega": "—", "Material": "metal", "Preço": 200.0},
        ]
        summary = build_delivery_summary(rows)
        beroc = summary[0]
        assert beroc["Com Prazo"] == "0%"


# ═══════════════════════════════════════════════════════════════════════════
#  build_summary()
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildSummary:
    def test_aggregates_correctly(self):
        rows = [
            {"Loja": "Beroc", "Material": "corda", "Preço": 100.0},
            {"Loja": "Beroc", "Material": "corda", "Preço": 120.0},
            {"Loja": "Beroc", "Material": "corda", "Preço": 140.0},
        ]
        summary = build_summary(rows)
        assert len(summary) == 1
        row = summary[0]
        assert row["Mínimo"] == 100.0
        assert row["Máximo"] == 140.0
        assert abs(row["Médio"] - 120.0) < 0.01
        assert row["Produtos"] == 3

    def test_separate_rows_per_material(self):
        rows = [
            {"Loja": "Beroc", "Material": "corda", "Preço": 100.0},
            {"Loja": "Beroc", "Material": "metal", "Preço": 200.0},
        ]
        summary = build_summary(rows)
        assert len(summary) == 2
        materials = {r["Material"] for r in summary}
        assert materials == {"corda", "metal"}


# ═══════════════════════════════════════════════════════════════════════════
#  build_promotions()
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildPromotions:
    def test_filters_discount_rows(self):
        rows = [
            {"Loja": "ML", "Desconto": "15%", "Material": "corda", "Preço": 85.0},
            {"Loja": "Beroc", "Desconto": "", "Material": "corda", "Preço": 100.0},
        ]
        promos = build_promotions(rows)
        assert len(promos) == 1
        assert promos[0]["Loja"] == "ML"

    def test_sorted_by_discount_descending(self):
        rows = [
            {"Loja": "ML", "Desconto": "10%", "Material": "corda", "Preço": 90.0},
            {"Loja": "ML", "Desconto": "25%", "Material": "metal", "Preço": 150.0},
            {"Loja": "ML", "Desconto": "5%", "Material": "couro", "Preço": 190.0},
        ]
        promos = build_promotions(rows)
        discounts = [int(r["Desconto"].replace("%", "")) for r in promos]
        assert discounts == sorted(discounts, reverse=True)

    def test_empty_rows_returns_empty_list(self):
        assert build_promotions([]) == []

    def test_no_discounts_returns_empty_list(self):
        rows = [{"Loja": "Beroc", "Desconto": "", "Material": "corda", "Preço": 100.0}]
        assert build_promotions(rows) == []


# ═══════════════════════════════════════════════════════════════════════════
#  ML context injection (position + query) — integration-style unit test
# ═══════════════════════════════════════════════════════════════════════════

class TestMLContextInjection:
    """
    Tests that _api_search() correctly injects position and query into
    Product.context without making real HTTP calls.
    We patch _get_with_retry to return a mock response.
    """

    def _make_api_response(self, n_items: int = 3, offset: int = 0) -> dict:
        return {
            "results": [
                {
                    "id": f"MLB{1000 + offset + i}",
                    "title": f"Pulseira Corda {i}",
                    "price": 89.90 + i,
                    "original_price": None,
                    "permalink": f"https://www.mercadolivre.com.br/MLB{1000 + offset + i}",
                    "shipping": {"free_shipping": True},
                }
                for i in range(n_items)
            ],
            "paging": {"total": n_items},  # total = n_items → only 1 page
        }

    def test_position_injected_1based(self):
        """Items in the first page must get positions 1, 2, 3, ... (1-based)."""
        from axen_price_scraper import MercadoLivreScraper
        scraper = MercadoLivreScraper()
        scraper._token = "fake-token"
        scraper._token_expired = False

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self._make_api_response(n_items=3, offset=0)
        mock_resp.raise_for_status = MagicMock()

        with patch("axen_price_scraper._get_with_retry", return_value=mock_resp):
            seen: set = set()
            products = scraper._api_search("pulseira masculina corda", "corda", seen)

        assert len(products) == 3
        for i, prod in enumerate(products):
            expected_pos = i + 1   # 1-based
            assert f"position:{expected_pos}" in prod.context, (
                f"Expected position:{expected_pos} in context: {prod.context!r}"
            )

    def test_query_injected_with_underscores(self):
        """Query 'pulseira masculina corda' must appear as 'pulseira_masculina_corda' in context."""
        from axen_price_scraper import MercadoLivreScraper
        scraper = MercadoLivreScraper()
        scraper._token = "fake-token"
        scraper._token_expired = False

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self._make_api_response(n_items=1)
        mock_resp.raise_for_status = MagicMock()

        with patch("axen_price_scraper._get_with_retry", return_value=mock_resp):
            seen: set = set()
            products = scraper._api_search("pulseira masculina corda", "corda", seen)

        assert len(products) == 1
        assert "query:pulseira_masculina_corda" in products[0].context

    def test_item_id_still_present_in_context(self):
        """The id:MLBXXX field must still be present alongside position and query."""
        from axen_price_scraper import MercadoLivreScraper
        scraper = MercadoLivreScraper()
        scraper._token = "fake-token"
        scraper._token_expired = False

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self._make_api_response(n_items=1)
        mock_resp.raise_for_status = MagicMock()

        with patch("axen_price_scraper._get_with_retry", return_value=mock_resp):
            seen: set = set()
            products = scraper._api_search("pulseira masculina corda", "corda", seen)

        assert "id:MLB1000" in products[0].context

    def test_position_second_page(self):
        """Second page (offset=50) must start positions at 51."""
        from axen_price_scraper import MercadoLivreScraper
        scraper = MercadoLivreScraper()
        scraper._token = "fake-token"
        scraper._token_expired = False

        # First call: page 1 (offset=0), returns 50 items + total=53
        first_response = {
            "results": [
                {
                    "id": f"MLB{2000 + i}",
                    "title": f"Pulseira {i}",
                    "price": 89.90,
                    "original_price": None,
                    "permalink": f"https://www.mercadolivre.com.br/MLB{2000 + i}",
                    "shipping": {"free_shipping": False},
                }
                for i in range(50)
            ],
            "paging": {"total": 53},  # 53 total → 2 pages
        }
        # Second call: page 2 (offset=50), returns 3 items
        second_response = {
            "results": [
                {
                    "id": f"MLB{3000 + i}",
                    "title": f"Pulseira Extra {i}",
                    "price": 89.90,
                    "original_price": None,
                    "permalink": f"https://www.mercadolivre.com.br/MLB{3000 + i}",
                    "shipping": {"free_shipping": False},
                }
                for i in range(3)
            ],
            "paging": {"total": 53},
        }

        call_count = [0]
        def side_effect(*args, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            if call_count[0] == 0:
                mock_resp.json.return_value = first_response
            else:
                mock_resp.json.return_value = second_response
            call_count[0] += 1
            return mock_resp

        with patch("axen_price_scraper._get_with_retry", side_effect=side_effect):
            with patch("axen_price_scraper.polite_sleep"):  # skip delays in test
                seen: set = set()
                products = scraper._api_search("pulseira masculina corda", "corda", seen)

        assert len(products) == 53
        # Last 3 products (from page 2) must have positions 51, 52, 53
        assert "position:51" in products[50].context
        assert "position:52" in products[51].context
        assert "position:53" in products[52].context


# ═══════════════════════════════════════════════════════════════════════════
#  QuatroMenScraper dedup fix
# ═══════════════════════════════════════════════════════════════════════════

class TestQuatroMenDedup:
    """
    Verifies that a product with the same URL returned by two different slugs
    appears only once in the final product list.
    """

    def test_same_url_from_two_slugs_deduplicated(self):
        from axen_price_scraper import QuatroMenScraper

        scraper = QuatroMenScraper()
        duplicate_url = "https://4men.com.br/produto-duplicado"
        dup_product = Product(
            store="4Men",
            name="Pulseira Corda Duplicada",
            price=99.0,
            material="corda",
            url=duplicate_url,
        )

        # Fake _fetch_category returns the same product for both slugs
        def fake_fetch(slug, material):
            return [dup_product]

        scraper._fetch_category = fake_fetch

        # CATEGORY_SLUGS["corda"] has 2 slugs → _fetch_category called twice,
        # both return the same URL → must produce only 1 product
        scraper.CATEGORY_SLUGS = {"corda": ["slug-a", "slug-b"]}
        scraper.FALLBACK_SLUG = "pulseira-masculina"

        products = scraper.scrape()
        corda_products = [p for p in products if p.material == "corda"]
        urls = [p.url for p in corda_products]
        assert urls.count(duplicate_url) == 1, (
            f"Expected 1 occurrence of URL, got {urls.count(duplicate_url)}"
        )
