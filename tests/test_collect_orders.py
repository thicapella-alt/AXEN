"""
tests/test_collect_orders.py — regressão para scripts/collect_orders.py.

Testa as funções puras de extração contra os fixtures reais do spike da
Fase 0 (tests/fixtures/ml/*.redacted.json), e o coletor completo contra
um fake do client ML (sem rede real) + banco in-memory. Cobre
especificamente a idempotência exigida pelo S2 (Parte 2.5): rodar o
coletor 2x com os mesmos fixtures não deve duplicar orders, order_items
nem stock_movements.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.collect_orders import (
    CollectSummary,
    collect_orders,
    extract_item_fields,
    extract_order_fields,
    resolve_sku_axen,
    resolve_tipo_envio,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ml"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


ORDER_DETAIL = _load("order_detail.redacted.json")
SHIPMENT_DETAIL = _load("shipment_detail.redacted.json")


# ── extract_order_fields / extract_item_fields / resolve_tipo_envio ────────────

class TestResolveTipoEnvio:
    def test_fulfillment_maps_to_full(self):
        assert resolve_tipo_envio(SHIPMENT_DETAIL) == "full"

    def test_other_logistic_type_maps_to_proprio(self):
        assert resolve_tipo_envio({"logistic_type": "self_service"}) == "proprio"

    def test_none_shipment_detail_is_undetermined(self):
        """Sem shipment_detail, não adivinha — fica None (coletor não gera venda_ml nesse caso)."""
        assert resolve_tipo_envio(None) is None


class TestExtractOrderFields:
    def test_against_real_fixture(self):
        fields = extract_order_fields(ORDER_DETAIL, SHIPMENT_DETAIL)
        assert fields["order_id"] == "2000018415699106"
        assert fields["status"] == "paid"
        assert fields["shipment_id"] == "47994433273"
        assert fields["tipo_envio"] == "full"
        assert fields["receita_bruta"] == 155.0
        assert fields["tarifa_ml"] == 23.25       # sale_fee do único item
        assert fields["custo_frete"] == 20.45      # shipping_option.list_cost
        assert fields["descontos"] == 60.0         # gross_price(215) - unit_price(155)
        assert fields["cancelado"] == 0
        assert fields["reembolsado"] == 0
        assert fields["receita_liquida"] == pytest.approx(155.0 - 23.25 - 20.45)
        assert '"order_has_discount"' in fields["tags"]

    def test_without_shipment_detail_custo_frete_and_tipo_envio_are_none(self):
        fields = extract_order_fields(ORDER_DETAIL, None)
        assert fields["custo_frete"] is None
        assert fields["tipo_envio"] is None
        # receita_liquida ainda calculável (custo_frete tratado como 0 no cálculo)
        assert fields["receita_liquida"] == pytest.approx(155.0 - 23.25)

    def test_cancelled_status_sets_cancelado_flag(self):
        order = {**ORDER_DETAIL, "status": "cancelled"}
        fields = extract_order_fields(order, None)
        assert fields["cancelado"] == 1


class TestExtractItemFields:
    def test_against_real_fixture(self):
        items = extract_item_fields(ORDER_DETAIL)
        assert len(items) == 1
        item = items[0]
        assert item["order_id"] == "2000018415699106"
        assert item["item_id"] == "MLB7318432106"
        assert item["variation_id"] == ""  # null no fixture -> '' (mesmo padrão de product_listings)
        assert item["quantidade"] == 1
        assert item["preco_unitario"] == 155.0


# ── resolve_sku_axen ─────────────────────────────────────────────────────────

class TestResolveSkuAxen:
    def test_returns_mapped_sku(self, db):
        db.execute(
            "INSERT INTO products (sku_axen, model, created_at, updated_at) VALUES ('FORGE-19-CIN', 'Forge', 't', 't')"
        )
        db.execute(
            "INSERT INTO product_listings (sku_axen, platform, item_id, variation_id, imported_at, created_at, updated_at) "
            "VALUES ('FORGE-19-CIN', 'mercadolivre', 'MLB7318432106', '', 't', 't', 't')"
        )
        assert resolve_sku_axen(db, "MLB7318432106", "") == "FORGE-19-CIN"

    def test_returns_none_when_unmapped(self, db):
        assert resolve_sku_axen(db, "MLB-DESCONHECIDO", "") is None

    def test_returns_none_when_listing_exists_but_unmapped(self, db):
        """Listing existe (já visto pelo map_listings.py) mas sku_axen=NULL — não inventa."""
        db.execute(
            "INSERT INTO product_listings (platform, item_id, variation_id, imported_at, created_at, updated_at) "
            "VALUES ('mercadolivre', 'MLB999', '', 't', 't', 't')"
        )
        assert resolve_sku_axen(db, "MLB999", "") is None


# ── Fake ML client ───────────────────────────────────────────────────────────

class _FakeMlClient:
    def __init__(self, order_summaries, order_detail=None, shipment_detail=None):
        self._order_summaries = order_summaries
        self._order_detail = order_detail or ORDER_DETAIL
        self._shipment_detail = shipment_detail or SHIPMENT_DETAIL

    def get_paginated_orders_search(self, days=7):
        return self._order_summaries

    def get_order_detail(self, order_id):
        return self._order_detail

    def get_shipment_detail(self, shipment_id):
        return self._shipment_detail


def _seed_product_and_listing(db, sku_axen="FORGE-19-CIN", item_id="MLB7318432106", variation_id=""):
    db.execute(
        "INSERT INTO products (sku_axen, model, created_at, updated_at) VALUES (?, 'Forge', 't', 't')",
        (sku_axen,),
    )
    db.execute(
        "INSERT INTO product_listings (sku_axen, platform, item_id, variation_id, imported_at, created_at, updated_at) "
        "VALUES (?, 'mercadolivre', ?, ?, 't', 't', 't')",
        (sku_axen, item_id, variation_id),
    )
    db.commit()


# ── collect_orders — fim a fim contra o fixture real ────────────────────────────

class TestCollectOrders:
    def test_happy_path_creates_order_item_and_movement(self, db):
        _seed_product_and_listing(db)
        client = _FakeMlClient(order_summaries=[{"id": ORDER_DETAIL["id"]}])

        summary = collect_orders(db, client, days=7)

        assert summary.orders_seen == 1
        assert summary.orders_upserted == 1
        assert summary.items_upserted == 1
        assert summary.movements_created == 1
        assert summary.unresolved_sku_alerts == []
        assert summary.errors == []

        order = db.execute("SELECT * FROM orders WHERE order_id='2000018415699106'").fetchone()
        assert order["status"] == "paid"
        assert order["tipo_envio"] == "full"

        item = db.execute("SELECT * FROM order_items WHERE order_id='2000018415699106'").fetchone()
        assert item["sku_axen"] == "FORGE-19-CIN"

        movement = db.execute("SELECT * FROM stock_movements WHERE fonte='api'").fetchone()
        assert movement["tipo"] == "venda_ml"
        assert movement["sku_axen"] == "FORGE-19-CIN"
        assert movement["quantidade"] == 1
        assert movement["local_origem"] == "full"   # tipo_envio == 'full'
        assert movement["local_destino"] == "cliente"
        assert movement["referencia"] == "2000018415699106"

    def test_running_twice_does_not_duplicate(self, db):
        """S2 Parte 2.5 — idempotência exigida explicitamente."""
        _seed_product_and_listing(db)
        client = _FakeMlClient(order_summaries=[{"id": ORDER_DETAIL["id"]}])

        s1 = collect_orders(db, client, days=7)
        s2 = collect_orders(db, client, days=7)

        assert s1.movements_created == 1
        assert s2.movements_created == 0
        assert s2.movements_already_existed == 1

        assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] == 1

    def test_unmapped_sku_generates_alert_not_movement(self, db):
        """Item sem mapeamento em product_listings: order_item gravado com
        sku_axen=NULL, alerta registrado, NENHUM stock_movement (a FK nem
        deixaria, mas o coletor já não tenta)."""
        client = _FakeMlClient(order_summaries=[{"id": ORDER_DETAIL["id"]}])

        summary = collect_orders(db, client, days=7)

        assert summary.orders_upserted == 1
        assert summary.items_upserted == 1
        assert summary.movements_created == 0
        assert len(summary.unresolved_sku_alerts) == 1
        assert "MLB7318432106" in summary.unresolved_sku_alerts[0]

        item = db.execute("SELECT sku_axen FROM order_items").fetchone()
        assert item["sku_axen"] is None
        assert db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] == 0

    def test_non_paid_order_does_not_generate_movement(self, db):
        """Critério de venda_ml é status='paid' — outro status não gera movimento,
        mas o pedido/item ainda são gravados normalmente."""
        _seed_product_and_listing(db)
        pending_order = {**ORDER_DETAIL, "status": "confirmed"}
        client = _FakeMlClient(order_summaries=[{"id": ORDER_DETAIL["id"]}], order_detail=pending_order)

        summary = collect_orders(db, client, days=7)

        assert summary.orders_upserted == 1
        assert summary.movements_created == 0
        order = db.execute("SELECT status FROM orders").fetchone()
        assert order["status"] == "confirmed"

    def test_rerun_updates_order_status_without_reversing_movement(self, db):
        """Pedido cancelado DEPOIS de pago: orders.status/cancelado atualiza,
        mas o venda_ml já gravado não é revertido (gap documentado)."""
        _seed_product_and_listing(db)
        client_paid = _FakeMlClient(order_summaries=[{"id": ORDER_DETAIL["id"]}])
        collect_orders(db, client_paid, days=7)

        cancelled_order = {**ORDER_DETAIL, "status": "cancelled"}
        client_cancelled = _FakeMlClient(order_summaries=[{"id": ORDER_DETAIL["id"]}], order_detail=cancelled_order)
        summary2 = collect_orders(db, client_cancelled, days=7)

        order = db.execute("SELECT status, cancelado FROM orders").fetchone()
        assert order["status"] == "cancelled"
        assert order["cancelado"] == 1
        # o movimento da 1ª rodada continua lá, intocado:
        assert db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] == 1
        assert summary2.movements_created == 0

    def test_missing_shipment_skips_movement_but_keeps_order(self, db):
        """Sem shipment_detail (tipo_envio indeterminado), o coletor não
        adivinha a origem — não gera venda_ml, mas pedido/item são gravados."""
        _seed_product_and_listing(db)
        client = _FakeMlClient(order_summaries=[{"id": ORDER_DETAIL["id"]}], shipment_detail=None)

        class _NoShipmentClient(_FakeMlClient):
            def get_shipment_detail(self, shipment_id):
                raise RuntimeError("sem envio associado")

        summary = collect_orders(db, _NoShipmentClient(order_summaries=[{"id": ORDER_DETAIL["id"]}]), days=7)
        assert summary.orders_upserted == 1
        assert len(summary.errors) == 1
        order = db.execute("SELECT tipo_envio FROM orders").fetchone()
        assert order["tipo_envio"] is None

    def test_dry_run_does_not_write(self, db):
        _seed_product_and_listing(db)
        client = _FakeMlClient(order_summaries=[{"id": ORDER_DETAIL["id"]}])
        summary = collect_orders(db, client, days=7, dry_run=True)
        assert summary.orders_upserted == 1
        assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] == 0

    def test_order_detail_failure_is_reported_not_fatal(self, db):
        """1 pedido com erro de rede não derruba o processamento dos demais."""
        class _FlakyClient(_FakeMlClient):
            def get_order_detail(self, order_id):
                if order_id == "999":
                    raise RuntimeError("timeout")
                return super().get_order_detail(order_id)

        _seed_product_and_listing(db)
        client = _FlakyClient(order_summaries=[{"id": 999}, {"id": ORDER_DETAIL["id"]}])
        summary = collect_orders(db, client, days=7)

        assert summary.orders_seen == 2
        assert summary.orders_upserted == 1  # só o que não falhou
        assert len(summary.errors) == 1
        assert "999" in summary.errors[0]
