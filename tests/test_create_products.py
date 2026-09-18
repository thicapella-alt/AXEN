"""
tests/test_create_products.py — regressão para scripts/create_products.py.

Cobre as duas regras que importam: nunca sobrescreve um sku_axen existente
(diferente de import_products.py, este script só cria) e nunca roda com
sku_axen duplicado dentro da própria lista embutida.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.create_products import _NEW_PRODUCTS, _INSERT_SQL


def _insert_existing_product(db, sku_axen: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """INSERT INTO products (sku_axen, model, active, created_at, updated_at)
           VALUES (?, 'Placeholder', 1, ?, ?)""",
        (sku_axen, now, now),
    )
    db.commit()


class TestNewProductsList:
    def test_no_duplicate_skus(self):
        skus = [row[0] for row in _NEW_PRODUCTS]
        assert len(skus) == len(set(skus))

    def test_all_have_four_fields(self):
        for row in _NEW_PRODUCTS:
            assert len(row) == 4

    def test_loom_sku_matches_confirmed_size(self):
        """Planilha tinha SKU=LOOM-19-PTO com Tamanho=21 — Thiago confirmou
        que o tamanho está certo, então o sku_axen precisa refletir 21cm."""
        loom_rows = [row for row in _NEW_PRODUCTS if row[1] == "Loom"]
        assert loom_rows == [("LOOM-21-PTO", "Loom", "21cm", "Preto")]

    def test_lucky_ver_is_vermelho(self):
        lucky_ver = [row for row in _NEW_PRODUCTS if row[0] == "LUCKY-VAR-VER"]
        assert lucky_ver == [("LUCKY-VAR-VER", "Lucky", "Variado", "Vermelho")]


class TestInsertBehavior:
    def test_inserts_all_new_products(self, db):
        now = datetime.now(timezone.utc).isoformat()
        for sku, model, size, color in _NEW_PRODUCTS:
            db.execute(
                _INSERT_SQL,
                (sku, model, size, color, 20, 5, 3, 45, now, now),
            )
        db.commit()

        count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        assert count == len(_NEW_PRODUCTS)

        row = db.execute(
            "SELECT * FROM products WHERE sku_axen='BARON-185-PTO'"
        ).fetchone()
        assert row["brand"] == "AXEN"
        assert row["model"] == "Baron"
        assert row["size"] == "18,5cm"
        assert row["color"] == "Preto"
        assert row["active"] == 0
        assert row["lead_time_purchase_days"] == 20
        assert row["target_coverage_days"] == 45

    def test_existing_sku_is_never_overwritten_by_insert(self, db):
        """INSERT sem ON CONFLICT — se o script tentasse inserir um sku_axen
        já existente, teria que estourar (IntegrityError), nunca sobrescrever
        silenciosamente. O script real evita isso checando antes (ver
        main()); aqui garantimos que o INSERT puro falha como esperado."""
        _insert_existing_product(db, "BARON-185-PTO")

        import sqlite3
        import pytest

        now = datetime.now(timezone.utc).isoformat()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                _INSERT_SQL,
                ("BARON-185-PTO", "Baron", "18,5cm", "Preto", 20, 5, 3, 45, now, now),
            )
