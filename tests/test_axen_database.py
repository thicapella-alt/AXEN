"""
Tests for axen_database.py

Coverage:
  - migrate(): fresh DB, idempotency, user_version
  - ingest_scrape_run(): scrape_run record, prices, ML positions,
                         delivery_snapshots, price_changes
  - Query helpers: get_latest_prices, get_price_history, get_price_changes,
                   get_delivery_summary, get_promotions
  - ML position helpers: get_ml_positions, get_ml_position_trend
  - Recommendation CRUD: save, get (filters), dismiss, apply
  - Sales helpers: upsert_sale (dedup), get_top_products, get_sales_by_state,
                   get_weekly_sales
  - ROAS helpers: upsert_roas_campaign (dedup/replace), get_roas_summary
  - Edge cases: empty product list, fail_run(), threshold boundaries
"""

import json
import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

import axen_database as db_module
from axen_database import (
    get_connection,
    migrate,
    ingest_scrape_run,
    fail_run,
    get_latest_run_id,
    get_run,
    get_latest_prices,
    get_price_history,
    get_price_changes,
    get_delivery_summary,
    get_promotions,
    get_ml_positions,
    get_ml_position_trend,
    save_recommendation,
    get_recommendations,
    get_recommendation_by_id,
    dismiss_recommendation,
    apply_recommendation,
    upsert_sale,
    get_top_products,
    get_sales_by_state,
    get_weekly_sales,
    upsert_roas_campaign,
    get_roas_campaigns,
    get_roas_summary,
    _parse_context,
    _week_label,
)
from tests.conftest import make_product, make_ml_product


# ═══════════════════════════════════════════════════════════════════════════
#  MIGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestMigrate:
    def test_creates_all_tables(self, db):
        """migrate() must create all seven tables."""
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "scrape_runs", "prices", "price_changes",
            "delivery_snapshots", "ml_positions",
            "sales", "roas_campaigns", "agent_recommendations",
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    def test_user_version_set(self, db):
        """PRAGMA user_version must equal _SCHEMA_VERSION after migration."""
        version = db.execute("PRAGMA user_version").fetchone()[0]
        assert version == db_module._SCHEMA_VERSION

    def test_idempotent_on_existing_db(self, db):
        """Calling migrate() twice must not raise and must not recreate tables."""
        migrate(db)  # second call — fixture already migrated once
        version = db.execute("PRAGMA user_version").fetchone()[0]
        assert version == db_module._SCHEMA_VERSION

    def test_indices_created(self, db):
        """Key indices must exist after migration."""
        indices = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        required = {
            "idx_prices_run_store",
            "idx_prices_material_date",
            "idx_prices_store_url",
            "idx_price_changes_store_detected",
            "idx_delivery_store_material",
            "idx_ml_positions_item_date",
            "idx_ml_positions_query_date",
            "idx_recs_type_priority",
        }
        assert required.issubset(indices), f"Missing indices: {required - indices}"

    def test_fresh_db_starts_at_version_zero(self):
        """A brand-new in-memory DB has user_version=0 before migrate()."""
        conn = get_connection(":memory:")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 0
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
#  INGEST_SCRAPE_RUN TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestIngestScrapeRun:
    def test_creates_scrape_run_record(self, db):
        """ingest_scrape_run() must create a scrape_runs row with status='done'."""
        products = [make_product()]
        run_id = ingest_scrape_run(db, products)

        row = db.execute("SELECT * FROM scrape_runs WHERE id=?", (run_id,)).fetchone()
        assert row is not None
        assert row["status"] == "done"
        assert row["total_products"] == 1
        assert row["finished_at"] is not None

    def test_returns_correct_run_id(self, db):
        """Each call returns a distinct integer run_id."""
        id1 = ingest_scrape_run(db, [make_product()])
        id2 = ingest_scrape_run(db, [make_product()])
        assert isinstance(id1, int)
        assert isinstance(id2, int)
        assert id1 != id2
        assert id2 > id1

    def test_inserts_prices(self, db):
        """All products appear in the prices table with correct values."""
        p1 = make_product(store="Beroc", price=99.90, material="corda")
        p2 = make_product(store="Key Design", price=215.00, material="metal",
                          url="https://keydesign.com.br/p/pulseira-metal")
        run_id = ingest_scrape_run(db, [p1, p2])

        rows = db.execute(
            "SELECT * FROM prices WHERE run_id=? ORDER BY price", (run_id,)
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["store"] == "Beroc"
        assert abs(rows[0]["price"] - 99.90) < 0.01
        assert rows[1]["store"] == "Key Design"
        assert rows[1]["material"] == "metal"

    def test_empty_product_list(self, db):
        """ingest_scrape_run() with an empty list must succeed and record 0 products."""
        run_id = ingest_scrape_run(db, [])
        row = db.execute(
            "SELECT total_products, status FROM scrape_runs WHERE id=?", (run_id,)
        ).fetchone()
        assert row["total_products"] == 0
        assert row["status"] == "done"

    def test_stores_scraped_field(self, db):
        """stores_scraped JSON field must list all distinct store names."""
        products = [
            make_product(store="Beroc"),
            make_product(store="Beroc"),   # duplicate store — should appear only once
            make_product(store="Key Design", url="https://kd.com/p/1"),
        ]
        run_id = ingest_scrape_run(db, products)
        row = db.execute(
            "SELECT stores_scraped FROM scrape_runs WHERE id=?", (run_id,)
        ).fetchone()
        stores = json.loads(row["stores_scraped"])
        assert sorted(stores) == ["Beroc", "Key Design"]

    # ── ML Positions ──────────────────────────────────────────────────────

    def test_ml_positions_inserted(self, db):
        """ML products with item_id in context must appear in ml_positions."""
        ml = make_ml_product(item_id="MLB111", position=5, material="corda")
        run_id = ingest_scrape_run(db, [ml])

        rows = db.execute(
            "SELECT * FROM ml_positions WHERE run_id=?", (run_id,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["item_id"] == "MLB111"
        assert rows[0]["position"] == 5
        assert rows[0]["material"] == "corda"

    def test_ml_position_zero_when_not_in_context(self, db):
        """ML products without position: in context get position=0."""
        p = make_product(
            store="Mercado Livre",
            context="id:MLB999 desconto:10%",   # no position: field
        )
        run_id = ingest_scrape_run(db, [p])
        rows = db.execute(
            "SELECT position FROM ml_positions WHERE run_id=?", (run_id,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["position"] == 0

    def test_non_ml_products_not_in_ml_positions(self, db):
        """Products from non-ML stores must not appear in ml_positions."""
        run_id = ingest_scrape_run(db, [make_product(store="Beroc")])
        rows = db.execute(
            "SELECT * FROM ml_positions WHERE run_id=?", (run_id,)
        ).fetchall()
        assert len(rows) == 0

    def test_ml_discount_pct_parsed(self, db):
        """discount_pct must be extracted from context and stored in prices."""
        ml = make_ml_product(item_id="MLB222", discount_pct=20)
        run_id = ingest_scrape_run(db, [ml])
        row = db.execute(
            "SELECT discount_pct FROM prices WHERE run_id=?", (run_id,)
        ).fetchone()
        assert row["discount_pct"] == 20.0

    def test_ml_query_parsed_from_context(self, db):
        """query: field (underscores → spaces) must be stored in ml_positions."""
        ml = make_ml_product(
            item_id="MLB333",
            position=2,
            query="pulseira masculina corda",
        )
        run_id = ingest_scrape_run(db, [ml])
        row = db.execute(
            "SELECT query FROM ml_positions WHERE run_id=?", (run_id,)
        ).fetchone()
        assert row["query"] == "pulseira masculina corda"

    # ── Delivery Snapshots ────────────────────────────────────────────────

    def test_delivery_snapshots_created(self, db):
        """One delivery_snapshot per (store, material) per run."""
        products = [
            make_product(store="Beroc", material="corda", price=100.0, delivery_info="Frete Grátis"),
            make_product(store="Beroc", material="corda", price=120.0, delivery_info="Frete Grátis",
                         url="https://beroc.com.br/p/2"),
            make_product(store="Beroc", material="metal", price=200.0, delivery_info="Entrega em 5 dias",
                         url="https://beroc.com.br/p/3"),
        ]
        run_id = ingest_scrape_run(db, products)
        snaps = db.execute(
            "SELECT * FROM delivery_snapshots WHERE run_id=? ORDER BY material",
            (run_id,)
        ).fetchall()
        assert len(snaps) == 2  # corda + metal
        corda = next(s for s in snaps if s["material"] == "corda")
        assert corda["product_count"] == 2
        assert abs(corda["price_min"] - 100.0) < 0.01
        assert abs(corda["price_avg"] - 110.0) < 0.01
        assert corda["delivery_mode"] == "Frete Grátis"
        assert corda["free_shipping_pct"] == 100.0

    def test_free_shipping_pct_calculation(self, db):
        """free_shipping_pct must reflect the actual proportion of free-shipping products."""
        products = [
            make_product(price=100.0, delivery_info="Frete Grátis"),
            make_product(price=110.0, delivery_info="Entrega em 5 dias",
                         url="https://beroc.com.br/p/2"),
            make_product(price=120.0, delivery_info="Frete Grátis",
                         url="https://beroc.com.br/p/3"),
            make_product(price=130.0, delivery_info="",
                         url="https://beroc.com.br/p/4"),
        ]
        run_id = ingest_scrape_run(db, products)
        snap = db.execute(
            "SELECT free_shipping_pct FROM delivery_snapshots WHERE run_id=?",
            (run_id,)
        ).fetchone()
        # 2 out of 4 products have free shipping → 50%
        assert snap["free_shipping_pct"] == 50.0

    # ── Price Changes ─────────────────────────────────────────────────────

    def test_price_changes_detected_on_second_run(self, db):
        """A price change between run 1 and run 2 must appear in price_changes."""
        url = "https://beroc.com.br/products/pulseira-corda"
        p1 = make_product(price=100.0, url=url)
        ingest_scrape_run(db, [p1])   # run 1

        p2 = make_product(price=110.0, url=url)  # same URL, different price
        run_id2 = ingest_scrape_run(db, [p2])    # run 2

        changes = db.execute(
            "SELECT * FROM price_changes WHERE run_id=?", (run_id2,)
        ).fetchall()
        assert len(changes) == 1
        assert abs(changes[0]["price_before"] - 100.0) < 0.01
        assert abs(changes[0]["price_after"] - 110.0) < 0.01
        assert abs(changes[0]["change_pct"] - 10.0) < 0.01

    def test_no_price_changes_on_first_run(self, db):
        """No price_changes records on the very first run (no prior run to compare)."""
        run_id = ingest_scrape_run(db, [make_product()])
        changes = db.execute(
            "SELECT * FROM price_changes WHERE run_id=?", (run_id,)
        ).fetchall()
        assert len(changes) == 0

    def test_no_change_detected_when_price_same(self, db):
        """Identical prices must not generate a price_change record."""
        url = "https://beroc.com.br/products/pulseira-corda"
        p = make_product(price=100.0, url=url)
        ingest_scrape_run(db, [p])
        run_id2 = ingest_scrape_run(db, [make_product(price=100.0, url=url)])

        changes = db.execute(
            "SELECT * FROM price_changes WHERE run_id=?", (run_id2,)
        ).fetchall()
        assert len(changes) == 0

    def test_no_change_detected_when_url_empty(self, db):
        """Products with empty URL cannot be matched — no price_change generated."""
        ingest_scrape_run(db, [make_product(url="", price=100.0)])
        run_id2 = ingest_scrape_run(db, [make_product(url="", price=200.0)])

        changes = db.execute(
            "SELECT * FROM price_changes WHERE run_id=?", (run_id2,)
        ).fetchall()
        assert len(changes) == 0

    def test_negative_change_pct_on_price_drop(self, db):
        """A price decrease must produce a negative change_pct."""
        url = "https://beroc.com.br/products/metal"
        ingest_scrape_run(db, [make_product(price=200.0, url=url)])
        run_id2 = ingest_scrape_run(db, [make_product(price=180.0, url=url)])

        change = db.execute(
            "SELECT change_pct FROM price_changes WHERE run_id=?", (run_id2,)
        ).fetchone()
        assert change["change_pct"] < 0
        assert abs(change["change_pct"] - (-10.0)) < 0.1

    # ── fail_run ──────────────────────────────────────────────────────────

    def test_fail_run_sets_error_status(self, db):
        """fail_run() must set status='error' and store the message."""
        run_id = ingest_scrape_run(db, [make_product()])
        # Simulate an error on a second run attempt
        with db:
            cur = db.execute(
                "INSERT INTO scrape_runs (started_at, status) VALUES (?, 'running')",
                (db_module._now_iso(),)
            )
            bad_run_id = cur.lastrowid

        fail_run(db, bad_run_id, "Simulated network error")

        row = db.execute(
            "SELECT status, error_msg FROM scrape_runs WHERE id=?", (bad_run_id,)
        ).fetchone()
        assert row["status"] == "error"
        assert "Simulated" in row["error_msg"]

    def test_fail_run_with_none_id_is_noop(self, db):
        """fail_run(None) must not raise."""
        fail_run(db, None, "should not crash")   # no assertion needed — just must not raise


# ═══════════════════════════════════════════════════════════════════════════
#  QUERY HELPERS — PRICES
# ═══════════════════════════════════════════════════════════════════════════

class TestPriceHelpers:
    def test_get_latest_run_id_none_when_empty(self, db):
        assert get_latest_run_id(db) is None

    def test_get_latest_run_id_after_ingest(self, db):
        run_id = ingest_scrape_run(db, [make_product()])
        assert get_latest_run_id(db) == run_id

    def test_get_latest_run_id_returns_most_recent(self, db):
        ingest_scrape_run(db, [make_product()])
        run_id2 = ingest_scrape_run(db, [make_product(url="https://beroc.com.br/p/2")])
        assert get_latest_run_id(db) == run_id2

    def test_get_latest_prices_empty_when_no_runs(self, db):
        assert get_latest_prices(db) == []

    def test_get_latest_prices_returns_latest_run_only(self, db):
        url1 = "https://beroc.com.br/p/1"
        url2 = "https://beroc.com.br/p/2"
        ingest_scrape_run(db, [make_product(price=100.0, url=url1)])
        run_id2 = ingest_scrape_run(db, [make_product(price=110.0, url=url2)])

        prices = get_latest_prices(db)
        # Must only include products from the latest run
        assert all(p["run_id"] == run_id2 for p in prices)
        assert len(prices) == 1
        assert abs(prices[0]["price"] - 110.0) < 0.01

    def test_get_latest_prices_filter_material(self, db):
        products = [
            make_product(material="corda", price=100.0),
            make_product(material="metal", price=200.0, url="https://beroc.com.br/p/2"),
        ]
        ingest_scrape_run(db, products)
        corda = get_latest_prices(db, material="corda")
        assert len(corda) == 1
        assert corda[0]["material"] == "corda"

    def test_get_latest_prices_filter_store(self, db):
        products = [
            make_product(store="Beroc"),
            make_product(store="Key Design", url="https://kd.com/p/1"),
        ]
        ingest_scrape_run(db, products)
        beroc = get_latest_prices(db, store="Beroc")
        assert len(beroc) == 1
        assert beroc[0]["store"] == "Beroc"

    def test_get_price_history_aggregates_by_day(self, db):
        """get_price_history() must return one row per (date, store, material)."""
        products = [
            make_product(store="Beroc", price=100.0),
            make_product(store="Beroc", price=120.0, url="https://beroc.com.br/p/2"),
        ]
        ingest_scrape_run(db, products)
        history = get_price_history(db, days=1)
        assert len(history) == 1
        row = history[0]
        assert row["store"] == "Beroc"
        assert abs(row["price_avg"] - 110.0) < 0.01
        assert abs(row["price_min"] - 100.0) < 0.01
        assert row["product_count"] == 2

    def test_get_price_changes_min_pct_threshold(self, db):
        """Changes below min_pct threshold must be excluded."""
        url = "https://beroc.com.br/p/1"
        ingest_scrape_run(db, [make_product(price=100.0, url=url)])
        ingest_scrape_run(db, [make_product(price=104.9, url=url)])  # 4.9% change

        # 5.0% threshold — 4.9% change must NOT appear
        changes = get_price_changes(db, days=7, min_pct=5.0)
        assert len(changes) == 0

    def test_get_price_changes_above_threshold(self, db):
        """Changes at or above min_pct must appear."""
        url = "https://beroc.com.br/p/1"
        ingest_scrape_run(db, [make_product(price=100.0, url=url)])
        ingest_scrape_run(db, [make_product(price=105.1, url=url)])  # 5.1% change

        changes = get_price_changes(db, days=7, min_pct=5.0)
        assert len(changes) == 1
        assert changes[0]["change_pct"] > 5.0

    def test_get_promotions_filters_discount_products(self, db):
        """get_promotions() must return only products with discount_pct > 0."""
        ml_with_discount = make_ml_product(discount_pct=15)
        ml_no_discount = make_ml_product(item_id="MLB000", position=1, discount_pct=None)
        ingest_scrape_run(db, [ml_with_discount, ml_no_discount])

        promos = get_promotions(db, days=1)
        assert len(promos) == 1
        assert promos[0]["discount_pct"] == 15.0


# ═══════════════════════════════════════════════════════════════════════════
#  DELIVERY SUMMARY HELPERS
# ═══════════════════════════════════════════════════════════════════════════

class TestDeliveryHelpers:
    def test_get_delivery_summary_empty_when_no_runs(self, db):
        assert get_delivery_summary(db) == []

    def test_get_delivery_summary_from_latest_run(self, db):
        products = [
            make_product(store="Beroc", material="corda", price=100.0),
            make_product(store="Beroc", material="corda", price=120.0,
                         url="https://beroc.com.br/p/2"),
        ]
        ingest_scrape_run(db, products)
        summary = get_delivery_summary(db)
        assert len(summary) == 1
        assert summary[0]["store"] == "Beroc"
        assert summary[0]["material"] == "corda"
        assert summary[0]["product_count"] == 2

    def test_get_delivery_summary_filter_material(self, db):
        products = [
            make_product(material="corda"),
            make_product(material="metal", url="https://beroc.com.br/p/2"),
        ]
        ingest_scrape_run(db, products)
        summary = get_delivery_summary(db, material="corda")
        assert len(summary) == 1
        assert summary[0]["material"] == "corda"


# ═══════════════════════════════════════════════════════════════════════════
#  ML POSITION HELPERS
# ═══════════════════════════════════════════════════════════════════════════

class TestMLPositionHelpers:
    def test_get_ml_positions_returns_inserted_records(self, db):
        ml = make_ml_product(item_id="MLB100", position=3)
        ingest_scrape_run(db, [ml])
        positions = get_ml_positions(db, days=1)
        assert len(positions) == 1
        assert positions[0]["item_id"] == "MLB100"
        assert positions[0]["position"] == 3

    def test_get_ml_positions_filter_by_query(self, db):
        ml1 = make_ml_product(item_id="MLB1", query="pulseira masculina corda")
        ml2 = make_ml_product(item_id="MLB2", query="pulseira masculina couro")
        ingest_scrape_run(db, [ml1, ml2])

        filtered = get_ml_positions(db, query="pulseira masculina corda", days=1)
        assert len(filtered) == 1
        assert filtered[0]["item_id"] == "MLB1"

    def test_get_ml_position_trend_single_run(self, db):
        """With only one run, first_position and last_position must both be the same."""
        ml = make_ml_product(item_id="MLB555", position=7)
        ingest_scrape_run(db, [ml])
        trends = get_ml_position_trend(db, days=7)
        assert len(trends) == 1
        t = trends[0]
        assert t["first_position"] == 7
        assert t["last_position"] == 7
        assert t["n_runs"] == 1

    def test_get_ml_position_trend_shows_worsening(self, db):
        """Worsening trend: position increases over time (higher number = worse rank)."""
        ml_run1 = make_ml_product(item_id="MLB999", position=3)
        ingest_scrape_run(db, [ml_run1])

        ml_run2 = make_ml_product(item_id="MLB999", position=8)
        ingest_scrape_run(db, [ml_run2])

        trends = get_ml_position_trend(db, days=7)
        item = next(t for t in trends if t["item_id"] == "MLB999")
        assert item["first_position"] == 3
        assert item["last_position"] == 8
        assert item["n_runs"] == 2
        assert item["avg_position"] == 5.5


# ═══════════════════════════════════════════════════════════════════════════
#  RECOMMENDATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════

class TestRecommendationHelpers:
    def test_save_recommendation_returns_id(self, db):
        rec_id = save_recommendation(
            db,
            type_="price_suggestion",
            priority="high",
            title="Reduzir preço da corda",
            body="AXEN está 15% acima da média.",
            material="corda",
            suggested_value=109.0,
        )
        assert isinstance(rec_id, int)
        assert rec_id > 0

    def test_get_recommendation_by_id(self, db):
        rec_id = save_recommendation(
            db,
            type_="promotion_alert",
            priority="medium",
            title="Beroc em promoção",
            body="Preço caiu 10%.",
            store="Beroc",
        )
        rec = get_recommendation_by_id(db, rec_id)
        assert rec is not None
        assert rec["title"] == "Beroc em promoção"
        assert rec["store"] == "Beroc"
        assert rec["dismissed_at"] is None
        assert rec["applied_at"] is None

    def test_get_recommendation_by_id_returns_none_for_missing(self, db):
        assert get_recommendation_by_id(db, 99999) is None

    def test_get_recommendations_active_only(self, db):
        """active_only=True must exclude dismissed and applied records."""
        id1 = save_recommendation(db, type_="ads_suggestion", priority="low",
                                  title="ADS", body="Sugestão de ADS.")
        id2 = save_recommendation(db, type_="ads_suggestion", priority="low",
                                  title="ADS 2", body="Sugestão 2.")
        dismiss_recommendation(db, id2)

        active = get_recommendations(db, active_only=True)
        ids = [r["id"] for r in active]
        assert id1 in ids
        assert id2 not in ids

    def test_get_recommendations_filter_type(self, db):
        save_recommendation(db, type_="promotion_alert", priority="high",
                            title="Promoção", body=".")
        save_recommendation(db, type_="price_suggestion", priority="high",
                            title="Preço", body=".")

        promos = get_recommendations(db, type_="promotion_alert")
        assert all(r["type"] == "promotion_alert" for r in promos)

    def test_get_recommendations_filter_priority(self, db):
        save_recommendation(db, type_="price_suggestion", priority="high",
                            title="Alta", body=".")
        save_recommendation(db, type_="price_suggestion", priority="low",
                            title="Baixa", body=".")

        high = get_recommendations(db, priority="high")
        assert all(r["priority"] == "high" for r in high)
        assert len(high) == 1

    def test_get_recommendations_ordered_by_priority(self, db):
        """High-priority recommendations must appear before medium and low."""
        save_recommendation(db, type_="ads_suggestion", priority="low", title="Low", body=".")
        save_recommendation(db, type_="ads_suggestion", priority="medium", title="Med", body=".")
        save_recommendation(db, type_="ads_suggestion", priority="high", title="High", body=".")

        recs = get_recommendations(db, active_only=False)
        priorities = [r["priority"] for r in recs]
        assert priorities[0] == "high"
        assert priorities[1] == "medium"
        assert priorities[2] == "low"

    def test_data_json_round_trips(self, db):
        """data_json dict must survive a save → get cycle intact."""
        payload = {"avg_price": 109.5, "stores": ["Beroc", "Key Design"], "position": 14}
        rec_id = save_recommendation(
            db,
            type_="price_suggestion",
            priority="high",
            title="Test",
            body="Test body.",
            data_json=payload,
        )
        rec = get_recommendation_by_id(db, rec_id)
        assert rec["data_json"] == payload

    def test_dismiss_recommendation(self, db):
        rec_id = save_recommendation(db, type_="ads_suggestion", priority="low",
                                     title="T", body="B.")
        result = dismiss_recommendation(db, rec_id)
        assert result is True
        rec = get_recommendation_by_id(db, rec_id)
        assert rec["dismissed_at"] is not None

    def test_dismiss_nonexistent_recommendation_returns_false(self, db):
        result = dismiss_recommendation(db, 99999)
        assert result is False

    def test_apply_recommendation(self, db):
        rec_id = save_recommendation(db, type_="price_suggestion", priority="high",
                                     title="T", body="B.")
        result = apply_recommendation(db, rec_id)
        assert result is True
        rec = get_recommendation_by_id(db, rec_id)
        assert rec["applied_at"] is not None

    def test_apply_nonexistent_recommendation_returns_false(self, db):
        result = apply_recommendation(db, 99999)
        assert result is False

    def test_dismiss_is_idempotent(self, db):
        """Dismissing an already-dismissed rec must not raise and returns True."""
        rec_id = save_recommendation(db, type_="ads_suggestion", priority="low",
                                     title="T", body="B.")
        dismiss_recommendation(db, rec_id)
        result = dismiss_recommendation(db, rec_id)  # second dismiss
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
#  SALES HELPERS
# ═══════════════════════════════════════════════════════════════════════════

class TestSalesHelpers:
    def _sale(self, **kwargs) -> dict:
        base = {
            "platform": "mercadolivre",
            "order_id": "ORDER-001",
            "product_name": "Pulseira Corda",
            "material": "corda",
            "quantity": 2,
            "unit_price": 125.0,
            "total_value": 250.0,
            "buyer_state": "SP",
            "sold_at": datetime.now(timezone.utc).isoformat(),
        }
        base.update(kwargs)
        return base

    def test_upsert_sale_inserts_record(self, db):
        sale_id = upsert_sale(db, self._sale())
        assert sale_id > 0

    def test_upsert_sale_dedup_by_platform_order_id(self, db):
        """Inserting the same (platform, order_id) twice must not create a duplicate."""
        id1 = upsert_sale(db, self._sale())
        id2 = upsert_sale(db, self._sale())  # same order_id
        count = db.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        assert count == 1
        assert id1 == id2

    def test_upsert_sale_week_label(self, db):
        """week_label must be set automatically from sold_at."""
        sale = self._sale(sold_at="2026-05-12T10:00:00+00:00")
        sale_id = upsert_sale(db, sale)
        row = db.execute("SELECT week_label FROM sales WHERE id=?", (sale_id,)).fetchone()
        # 2026-05-12 is in ISO week 20 of 2026
        assert row["week_label"] == "2026-W20"

    def test_get_top_products_orders_by_units(self, db):
        upsert_sale(db, self._sale(order_id="O1", product_name="A", material="corda", quantity=5))
        upsert_sale(db, self._sale(order_id="O2", product_name="B", material="metal", quantity=2))

        top = get_top_products(db, days=30, limit=5)
        assert top[0]["product_name"] == "A"
        assert top[0]["total_units"] == 5
        assert top[1]["product_name"] == "B"

    def test_get_sales_by_state(self, db):
        upsert_sale(db, self._sale(order_id="O1", buyer_state="SP", quantity=3))
        upsert_sale(db, self._sale(order_id="O2", buyer_state="RJ", quantity=1))

        by_state = get_sales_by_state(db, days=30)
        sp = next((r for r in by_state if r["buyer_state"] == "SP"), None)
        assert sp is not None
        assert sp["total_units"] == 3

    def test_get_weekly_sales_aggregates_correctly(self, db):
        sold_at = "2026-05-12T10:00:00+00:00"
        upsert_sale(db, self._sale(order_id="O1", sold_at=sold_at, total_value=250.0, quantity=2))
        upsert_sale(db, self._sale(order_id="O2", sold_at=sold_at, total_value=125.0, quantity=1))

        # Both are in the same week — should aggregate into 1 row
        weeks = get_weekly_sales(db, weeks=4)
        assert len(weeks) == 1
        assert weeks[0]["total_units"] == 3
        assert abs(weeks[0]["total_revenue"] - 375.0) < 0.01


# ═══════════════════════════════════════════════════════════════════════════
#  ROAS HELPERS
# ═══════════════════════════════════════════════════════════════════════════

class TestRoasHelpers:
    def _campaign(self, **kwargs) -> dict:
        base = {
            "platform": "mercadolivre",
            "campaign_id": "CAMP-001",
            "campaign_name": "Pulseiras SP",
            "period_start": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
            "period_end": datetime.now(timezone.utc).isoformat(),
            "ad_spend": 500.0,
            "attributed_revenue": 2000.0,
        }
        base.update(kwargs)
        return base

    def test_upsert_roas_campaign_calculates_roas(self, db):
        camp_id = upsert_roas_campaign(db, self._campaign())
        row = db.execute(
            "SELECT roas FROM roas_campaigns WHERE id=?", (camp_id,)
        ).fetchone()
        # 2000 / 500 = 4.0
        assert abs(row["roas"] - 4.0) < 0.001

    def test_upsert_roas_campaign_zero_spend_gives_zero_roas(self, db):
        camp_id = upsert_roas_campaign(db, self._campaign(ad_spend=0, attributed_revenue=500.0))
        row = db.execute(
            "SELECT roas FROM roas_campaigns WHERE id=?", (camp_id,)
        ).fetchone()
        assert row["roas"] == 0.0

    def test_upsert_roas_campaign_replaces_on_duplicate(self, db):
        """Same (platform, campaign_id, period_start) must update, not duplicate."""
        camp = self._campaign()
        upsert_roas_campaign(db, camp)
        upsert_roas_campaign(db, {**camp, "ad_spend": 600.0, "attributed_revenue": 3000.0})

        count = db.execute("SELECT COUNT(*) FROM roas_campaigns").fetchone()[0]
        assert count == 1
        row = db.execute("SELECT roas FROM roas_campaigns").fetchone()
        # 3000 / 600 = 5.0
        assert abs(row["roas"] - 5.0) < 0.001

    def test_get_roas_summary_empty_db(self, db):
        summary = get_roas_summary(db, days=30)
        assert summary["best_campaign"] is None

    def test_get_roas_summary_aggregates(self, db):
        upsert_roas_campaign(db, self._campaign(ad_spend=500.0, attributed_revenue=2000.0))
        upsert_roas_campaign(db, self._campaign(
            campaign_id="CAMP-002", ad_spend=200.0, attributed_revenue=400.0
        ))

        summary = get_roas_summary(db, days=30)
        # CAMP-001 ROAS=4.0, CAMP-002 ROAS=2.0 → avg=3.0
        assert abs(summary["avg_roas"] - 3.0) < 0.1
        assert abs(summary["total_spend"] - 700.0) < 0.01
        assert abs(summary["total_revenue"] - 2400.0) < 0.01
        assert summary["campaign_count"] == 2
        assert summary["best_campaign"]["roas"] == 4.0


# ═══════════════════════════════════════════════════════════════════════════
#  INTERNAL UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

class TestInternalUtils:
    def test_parse_context_full(self):
        ctx = _parse_context("id:MLB123456789 position:5 desconto:15% query:pulseira_masculina_corda")
        assert ctx["item_id"] == "MLB123456789"
        assert ctx["position"] == 5
        assert ctx["discount_pct"] == 15.0
        assert ctx["query"] == "pulseira masculina corda"

    def test_parse_context_empty_string(self):
        ctx = _parse_context("")
        assert ctx["item_id"] == ""
        assert ctx["position"] == 0
        assert ctx["discount_pct"] is None
        assert ctx["query"] == ""

    def test_parse_context_no_position(self):
        ctx = _parse_context("id:MLB999 desconto:20%")
        assert ctx["item_id"] == "MLB999"
        assert ctx["position"] == 0
        assert ctx["discount_pct"] == 20.0

    def test_parse_context_no_discount(self):
        ctx = _parse_context("id:MLB888 position:2")
        assert ctx["discount_pct"] is None

    def test_week_label_valid_date(self):
        assert _week_label("2026-05-12") == "2026-W20"

    def test_week_label_from_datetime_string(self):
        assert _week_label("2026-05-12T10:30:00+00:00") == "2026-W20"

    def test_week_label_invalid_returns_empty(self):
        assert _week_label("not-a-date") == ""

    def test_week_label_empty_string(self):
        assert _week_label("") == ""
