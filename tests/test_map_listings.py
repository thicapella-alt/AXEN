"""
tests/test_map_listings.py — regressão para scripts/map_listings.py.

Cobre dois bugs reais encontrados na 1ª e 2ª rodadas de --write-db em
produção (12/09/2026):
  1. Itens sem variação cadastrada no ML (tamanho/cor só no título) geravam
     o mesmo sku_axen para produtos diferentes (ex. Forge 19cm == Forge 21cm).
  2. A AXEN não usa um único nome de atributo para tamanho — o Drift usa
     "Comprimento"/"Diâmetro" (id LENGTH/DIAMETER), não "Tamanho" (id SIZE)
     — o Drift inteiro (12 variações, 3 tamanhos × 4 cores) colapsava pra
     4 SKUs (só a cor).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.map_listings import resolve_size_color, suggest_sku


def _variation(attrs: dict[str, str]) -> dict:
    """attrs: {nome_visível: valor} — monta attribute_combinations no formato do ML."""
    ids = {"tamanho": "SIZE", "comprimento": "LENGTH", "diâmetro": "DIAMETER", "cor": "COLOR"}
    return {
        "attribute_combinations": [
            {"name": name.capitalize(), "id": ids.get(name.lower(), name.upper()), "value_name": value}
            for name, value in attrs.items()
        ]
    }


class TestSuggestSkuWithoutVariation:
    """Bug #1 — item sem variação, tamanho/cor só no texto do título."""

    def test_same_model_different_size_no_variation(self):
        t19 = "Pulseira Masculina Aço Inox Corrente Trançada Axen Forge Cinza 19 Cm"
        t21 = "Pulseira Masculina Aço Inox Corrente Trançada Axen Forge Cinza 21 Cm"
        assert suggest_sku(t19, {}) != suggest_sku(t21, {})
        assert suggest_sku(t19, {}) == "FORGE-19-CIN"
        assert suggest_sku(t21, {}) == "FORGE-21-CIN"

    def test_same_model_multiple_colors_and_sizes_all_unique(self):
        titles = [
            "Pulseira Masculina Couro Fecho Magnético Axen Tag Marrom-escuro 21 Cm",
            "Pulseira Masculina Couro Fecho Magnético Axen Tag Marrom-escuro 19 Cm",
            "Pulseira Masculina Couro Fecho Magnético Axen Tag Preto 19 Cm",
            "Pulseira Masculina Couro Fecho Magnético Axen Tag Preto 21 Cm",
        ]
        skus = [suggest_sku(t, {}) for t in titles]
        assert len(skus) == len(set(skus)), f"colisão: {skus}"


class TestSuggestSkuVariationAttributeNames:
    """Bug #2 — a AXEN usa nomes de atributo diferentes pra tamanho entre produtos."""

    def test_size_attr_named_tamanho(self):
        v = _variation({"tamanho": "19", "cor": "Preto"})
        size, color = resolve_size_color("Pulseira Axen Shackle", v)
        assert size == "19"
        assert color == "Preto"

    def test_size_attr_named_comprimento_diametro(self):
        """O Drift real usa 'Comprimento'/'Diâmetro' (id LENGTH/DIAMETER), não 'Tamanho'."""
        v = _variation({"comprimento": "19.5", "cor": "Preto", "diâmetro": "19.5"})
        size, color = resolve_size_color("Pulseira Axen Drift", v)
        assert size == "19.5"
        assert color == "Preto"

    def test_drift_all_12_variations_unique(self):
        """Reprodução do bug real: Drift, 3 tamanhos × 4 cores, atributo 'Comprimento'."""
        combos = [
            ("19.5", "Preto"), ("18.5", "Azul-escuro"), ("18.5", "Bege"), ("18.5", "Preto"),
            ("18.5", "Cinza"), ("19.5", "Azul-escuro"), ("19.5", "Bege"), ("19.5", "Cinza"),
            ("20.5", "Azul-escuro"), ("20.5", "Bege"), ("20.5", "Cinza"), ("20.5", "Preto"),
        ]
        title = "Pulseira Masculina Corda Dupla Fecho Magnético Axen Drift"
        skus = [
            suggest_sku(title, _variation({"comprimento": size, "cor": color, "diâmetro": size}))
            for size, color in combos
        ]
        assert len(skus) == len(set(skus)), f"colisão: {skus}"

    def test_variation_attr_takes_priority_over_title(self):
        """attribute_combinations sempre vence o texto do título quando os dois existem."""
        v = _variation({"tamanho": "21", "cor": "Azul"})
        sku = suggest_sku("Pulseira Axen Forge Cinza 19 Cm", v)
        assert "21" in sku  # tamanho da variação (21), não do título (19)


class TestSizeDecimalHandling:
    """Confirma que '19' e '19,5'/'19.5' nunca colidem (ponto/vírgula é só removido, não truncado)."""

    def test_integer_and_decimal_size_are_distinct(self):
        v_int = _variation({"tamanho": "19", "cor": "Preto"})
        v_dec = _variation({"tamanho": "19.5", "cor": "Preto"})
        sku_int = suggest_sku("Pulseira Axen Modelo", v_int)
        sku_dec = suggest_sku("Pulseira Axen Modelo", v_dec)
        assert sku_int != sku_dec

    def test_comma_decimal_from_title_normalizes_like_dot(self):
        t_dot = "Pulseira Axen Modelo Preto 19.5 Cm"
        t_comma = "Pulseira Axen Modelo Preto 19,5 Cm"
        assert suggest_sku(t_dot, {}) == suggest_sku(t_comma, {})


class TestSizeValueWithEmbeddedUnit:
    """Comprimento/Diâmetro vêm do ML com a unidade embutida ('20.5 cm'),
    diferente de Tamanho ('19' puro) — achado na 1ª rodada real em produção:
    sem limpar, o sku saía 'DRIFT-205C-PTO' (a unidade cortada no meio)."""

    def test_comprimento_value_with_unit_does_not_leak_into_sku(self):
        v = _variation({"comprimento": "20.5 cm", "cor": "Preto"})
        sku = suggest_sku("Pulseira Axen Anchor", v)
        assert sku == "ANCHOR-205-PTO"
        assert "C-" not in sku and not sku.endswith("C")
