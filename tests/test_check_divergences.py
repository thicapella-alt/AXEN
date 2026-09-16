"""
tests/test_check_divergences.py — regressão para scripts/check_divergences.py.

Funções puras testadas contra os fixtures reais do spike
(tests/fixtures/ml/item_detail.json, inventory_stock.json). O motor
completo (reconcile()) usa um fake do client ML + banco in-memory —
sem rede real.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.check_divergences import (
    extract_available_quantity_from_inventory,
    extract_available_quantity_from_item,
    ledger_saldo,
    listing_for_sku,
    reconcile,
    skus_with_recent_movement,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ml"
ITEM_DETAIL = json.loads((FIXTURES / "item_detail.json").read_text())
INVENTORY_STOCK = json.loads((FIXTURES / "inventory_stock.json").read_text())

VARIATION_ID = "203113107597"   # a mesma variação nos dois fixtures
INVENTORY_ID = "PJVF21758"


# ── extract_available_quantity_* ────────────────────────────────────────────────

class TestExtractAvailableQuantityFromItem:
    def test_finds_matching_variation(self):
        assert extract_available_quantity_from_item(ITEM_DETAIL, VARIATION_ID) == 1

    def test_unknown_variation_returns_none(self):
        """Variação removida do anúncio — não adivinha."""
        assert extract_available_quantity_from_item(ITEM_DETAIL, "999999999") is None

    def test_no_variation_id_uses_top_level(self):
        item = {"available_quantity": 42}
        assert extract_available_quantity_from_item(item, "") == 42


class TestExtractAvailableQuantityFromInventory:
    def test_against_real_fixture(self):
        assert extract_available_quantity_from_inventory(INVENTORY_STOCK) == 1


# ── DB helpers ───────────────────────────────────────────────────────────────

def _insert_product(db, sku_axen="DRIFT-185-AZU"):
    db.execute(
        "INSERT INTO products (sku_axen, model, created_at, updated_at) VALUES (?, 'Drift', 't', 't')",
        (sku_axen,),
    )


def _insert_movement(db, sku_axen, local_origem, local_destino, quantidade, data, dedupe_key):
    db.execute(
        """INSERT INTO stock_movements
           (data, sku_axen, tipo, quantidade, local_origem, local_destino, fonte, dedupe_key, ingested_at)
           VALUES (?, ?, 'compra_recebida', ?, ?, ?, 'api', ?, 't')""",
        (data, sku_axen, quantidade, local_origem, local_destino, dedupe_key),
    )


class TestSkusWithRecentMovement:
    def test_includes_sku_within_window(self, db):
        _insert_product(db)
        _insert_movement(db, "DRIFT-185-AZU", "fornecedor", "estoque_bruto", 10, "2026-09-15", "m1")
        db.commit()
        skus = skus_with_recent_movement(db, days=7)
        assert "DRIFT-185-AZU" in skus

    def test_excludes_sku_outside_window(self, db):
        _insert_product(db)
        _insert_movement(db, "DRIFT-185-AZU", "fornecedor", "estoque_bruto", 10, "2020-01-01", "m1")
        db.commit()
        skus = skus_with_recent_movement(db, days=7)
        assert "DRIFT-185-AZU" not in skus

    def test_handles_mixed_date_formats(self, db):
        """data da planilha ('AAAA-MM-DD HH:MM') e da API (ISO com timezone) —
        os dois têm que ser reconhecidos como recentes."""
        _insert_product(db, "SHEET-SKU")
        _insert_product(db, "API-SKU")
        db.execute(
            """INSERT INTO stock_movements
               (data, sku_axen, tipo, quantidade, local_origem, local_destino, fonte, dedupe_key, ingested_at)
               VALUES ('2026-09-15 10:30', 'SHEET-SKU', 'compra_recebida', 1, 'fornecedor', 'estoque_bruto', 'planilha_movimentos', 'x1', 't')"""
        )
        db.execute(
            """INSERT INTO stock_movements
               (data, sku_axen, tipo, quantidade, local_origem, local_destino, fonte, dedupe_key, ingested_at)
               VALUES ('2026-09-15T20:19:02.000-04:00', 'API-SKU', 'venda_ml', 1, 'full', 'cliente', 'api', 'x2', 't')"""
        )
        db.commit()
        skus = skus_with_recent_movement(db, days=30)
        assert "SHEET-SKU" in skus
        assert "API-SKU" in skus


class TestLedgerSaldo:
    def test_full_bucket(self, db):
        _insert_product(db)
        _insert_movement(db, "DRIFT-185-AZU", "estoque_pronto", "full", 10, "2026-09-01", "m1")
        _insert_movement(db, "DRIFT-185-AZU", "full", "cliente", 3, "2026-09-10", "m2")
        db.commit()
        assert ledger_saldo(db, "DRIFT-185-AZU", "full") == 7

    def test_proprio_bucket_maps_to_estoque_pronto(self, db):
        """bucket 'proprio' soma contra o local real 'estoque_pronto' (Q3) —
        não contra um local literal 'proprio', que nem existe em stock_movements."""
        _insert_product(db)
        _insert_movement(db, "DRIFT-185-AZU", "laser", "estoque_pronto", 6, "2026-09-01", "m1")
        db.commit()
        assert ledger_saldo(db, "DRIFT-185-AZU", "proprio") == 6


class TestListingForSku:
    def test_finds_listing_with_inventory_id(self, db):
        _insert_product(db)
        db.execute(
            "INSERT INTO product_listings (sku_axen, platform, item_id, variation_id, inventory_id, imported_at, created_at, updated_at) "
            "VALUES ('DRIFT-185-AZU', 'mercadolivre', 'MLB1', 'V1', 'INV1', 't', 't', 't')"
        )
        db.commit()
        row = listing_for_sku(db, "DRIFT-185-AZU")
        assert row["item_id"] == "MLB1"
        assert row["inventory_id"] == "INV1"

    def test_no_listing_returns_none(self, db):
        assert listing_for_sku(db, "SEM-LISTING") is None


# ── reconcile() — fim a fim ──────────────────────────────────────────────────

class _FakeMlClient:
    def __init__(self, item_detail=None, inventory_stock=None):
        self._item_detail = item_detail if item_detail is not None else ITEM_DETAIL
        self._inventory_stock = inventory_stock if inventory_stock is not None else INVENTORY_STOCK

    def get_item_detail(self, item_id):
        return self._item_detail

    def get_inventory_stock(self, inventory_id):
        return self._inventory_stock


class TestReconcile:
    def test_full_bucket_end_to_end(self, db):
        _insert_product(db)
        db.execute(
            "INSERT INTO product_listings (sku_axen, platform, item_id, variation_id, inventory_id, imported_at, created_at, updated_at) "
            f"VALUES ('DRIFT-185-AZU', 'mercadolivre', 'MLB1', '{VARIATION_ID}', '{INVENTORY_ID}', 't', 't', 't')"
        )
        _insert_movement(db, "DRIFT-185-AZU", "estoque_pronto", "full", 5, "2026-09-15", "m1")
        db.commit()

        summary = reconcile(db, _FakeMlClient(), days=7)

        assert summary.skus_checked == 1
        assert summary.skus_written == 1
        snap = summary.snapshots[0]
        assert snap.local == "full"
        assert snap.saldo_ledger == 5
        assert snap.saldo_declarado_ml == 1     # do fixture real
        assert snap.divergencia == 1 - 5         # -4

        row = db.execute("SELECT * FROM stock_snapshots WHERE sku_axen='DRIFT-185-AZU'").fetchone()
        assert row["local"] == "full"
        assert row["divergencia"] == -4

    def test_proprio_bucket_end_to_end(self, db):
        """Listing sem inventory_id -> bucket 'proprio', consulta get_item_detail."""
        _insert_product(db)
        db.execute(
            "INSERT INTO product_listings (sku_axen, platform, item_id, variation_id, inventory_id, imported_at, created_at, updated_at) "
            f"VALUES ('DRIFT-185-AZU', 'mercadolivre', 'MLB1', '{VARIATION_ID}', NULL, 't', 't', 't')"
        )
        _insert_movement(db, "DRIFT-185-AZU", "laser", "estoque_pronto", 1, "2026-09-15", "m1")
        db.commit()

        summary = reconcile(db, _FakeMlClient(), days=7)

        snap = summary.snapshots[0]
        assert snap.local == "proprio"
        assert snap.saldo_ledger == 1
        assert snap.saldo_declarado_ml == 1
        assert snap.divergencia == 0

    def test_zero_divergence_when_ledger_matches_ml(self, db):
        _insert_product(db)
        db.execute(
            "INSERT INTO product_listings (sku_axen, platform, item_id, variation_id, inventory_id, imported_at, created_at, updated_at) "
            f"VALUES ('DRIFT-185-AZU', 'mercadolivre', 'MLB1', '{VARIATION_ID}', '{INVENTORY_ID}', 't', 't', 't')"
        )
        _insert_movement(db, "DRIFT-185-AZU", "estoque_pronto", "full", 1, "2026-09-15", "m1")
        db.commit()
        summary = reconcile(db, _FakeMlClient(), days=7)
        assert summary.snapshots[0].divergencia == 0

    def test_sku_without_listing_reported_not_fatal(self, db):
        _insert_product(db)
        _insert_movement(db, "DRIFT-185-AZU", "fornecedor", "estoque_bruto", 5, "2026-09-15", "m1")
        db.commit()
        summary = reconcile(db, _FakeMlClient(), days=7)
        assert summary.skus_checked == 1
        assert summary.skus_written == 0
        assert "DRIFT-185-AZU" in summary.skus_no_listing
        assert db.execute("SELECT COUNT(*) FROM stock_snapshots").fetchone()[0] == 0

    def test_sku_without_recent_movement_is_skipped(self, db):
        _insert_product(db)
        db.execute(
            "INSERT INTO product_listings (sku_axen, platform, item_id, variation_id, inventory_id, imported_at, created_at, updated_at) "
            f"VALUES ('DRIFT-185-AZU', 'mercadolivre', 'MLB1', '{VARIATION_ID}', '{INVENTORY_ID}', 't', 't', 't')"
        )
        db.commit()
        summary = reconcile(db, _FakeMlClient(), days=7)
        assert summary.skus_checked == 0

    def test_running_twice_same_day_updates_not_duplicates(self, db):
        _insert_product(db)
        db.execute(
            "INSERT INTO product_listings (sku_axen, platform, item_id, variation_id, inventory_id, imported_at, created_at, updated_at) "
            f"VALUES ('DRIFT-185-AZU', 'mercadolivre', 'MLB1', '{VARIATION_ID}', '{INVENTORY_ID}', 't', 't', 't')"
        )
        _insert_movement(db, "DRIFT-185-AZU", "estoque_pronto", "full", 5, "2026-09-15", "m1")
        db.commit()

        reconcile(db, _FakeMlClient(), days=7)
        _insert_movement(db, "DRIFT-185-AZU", "full", "cliente", 1, "2026-09-16", "m2")
        db.commit()
        reconcile(db, _FakeMlClient(), days=7)

        rows = db.execute("SELECT COUNT(*) FROM stock_snapshots WHERE sku_axen='DRIFT-185-AZU'").fetchone()[0]
        assert rows == 1  # atualizou a linha do dia, não criou 2ª
        row = db.execute("SELECT saldo_ledger FROM stock_snapshots WHERE sku_axen='DRIFT-185-AZU'").fetchone()
        assert row["saldo_ledger"] == 4  # 5 - 1, reflete a 2ª rodada

    def test_dry_run_does_not_write(self, db):
        _insert_product(db)
        db.execute(
            "INSERT INTO product_listings (sku_axen, platform, item_id, variation_id, inventory_id, imported_at, created_at, updated_at) "
            f"VALUES ('DRIFT-185-AZU', 'mercadolivre', 'MLB1', '{VARIATION_ID}', '{INVENTORY_ID}', 't', 't', 't')"
        )
        _insert_movement(db, "DRIFT-185-AZU", "estoque_pronto", "full", 5, "2026-09-15", "m1")
        db.commit()
        summary = reconcile(db, _FakeMlClient(), days=7, dry_run=True)
        assert len(summary.snapshots) == 1
        assert db.execute("SELECT COUNT(*) FROM stock_snapshots").fetchone()[0] == 0

    def test_ml_call_failure_reported_not_fatal(self, db):
        class _FlakyClient(_FakeMlClient):
            def get_inventory_stock(self, inventory_id):
                raise RuntimeError("timeout")

        _insert_product(db)
        db.execute(
            "INSERT INTO product_listings (sku_axen, platform, item_id, variation_id, inventory_id, imported_at, created_at, updated_at) "
            f"VALUES ('DRIFT-185-AZU', 'mercadolivre', 'MLB1', '{VARIATION_ID}', '{INVENTORY_ID}', 't', 't', 't')"
        )
        _insert_movement(db, "DRIFT-185-AZU", "estoque_pronto", "full", 5, "2026-09-15", "m1")
        db.commit()

        summary = reconcile(db, _FlakyClient(), days=7)
        assert len(summary.errors) == 1
        snap = summary.snapshots[0]
        assert snap.saldo_declarado_ml is None
        assert snap.divergencia is None
        row = db.execute("SELECT saldo_declarado_ml, divergencia FROM stock_snapshots").fetchone()
        assert row["saldo_declarado_ml"] is None
        assert row["divergencia"] is None
