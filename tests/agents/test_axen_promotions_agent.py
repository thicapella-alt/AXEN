"""
tests/agents/test_axen_promotions_agent.py

Unit tests for PromotionsAgent and the BaseAgent contract.

All tests use the `db` fixture (in-memory SQLite, migrated) from conftest.py.
Products are inserted via ingest_scrape_run() so that get_promotions() can
find them; discount_pct is parsed from Product.context (format: "desconto:N%").
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from axen_database import get_recommendations, ingest_scrape_run, save_recommendation
from agents.axen_base_agent import BaseAgent
from agents.axen_promotions_agent import PromotionsAgent


# Minimal Product stub — mirrors the real dataclass in axen_price_scraper.py.
# Defined here so we avoid importing the scraper's heavy dependencies
# (playwright, cloudscraper, etc.) during agent unit tests.
@dataclass
class Product:
    store: str
    name: str
    price: float
    material: str
    url: str = ""
    category_url: str = ""
    context: str = ""
    delivery_info: str = ""


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _discounted(
    store: str = "Key Design",
    material: str = "couro",
    price: float = 89.90,
    discount_pct: int = 25,
    url: str = "https://keydesign.com.br/pulseira-1",
) -> Product:
    """Create a Product whose context encodes a discount percentage."""
    return Product(
        store=store,
        name=f"Pulseira {material.capitalize()} {store}",
        price=price,
        material=material,
        url=url,
        context=f"desconto:{discount_pct}%",
        delivery_info="Frete Grátis",
    )


def _plain(
    store: str = "Beroc",
    material: str = "corda",
    price: float = 99.90,
    url: str = "https://beroc.com.br/pulseira-1",
) -> Product:
    """Create a Product with no discount context."""
    return Product(
        store=store,
        name=f"Pulseira {material.capitalize()}",
        price=price,
        material=material,
        url=url,
        context="",
        delivery_info="",
    )


# ─────────────────────────────────────────────
#  TestBaseAgentContract
# ─────────────────────────────────────────────

class TestBaseAgentContract:
    """Verify that BaseAgent's non-abstract behaviour is correct."""

    def test_run_safe_returns_empty_on_none_connection(self):
        """run_safe() must never raise — even with a broken connection."""
        agent = PromotionsAgent()
        result = agent.run_safe(None)  # type: ignore[arg-type]
        assert result == []

    def test_run_safe_returns_empty_on_exception(self, db):
        """run_safe() swallows any exception raised inside run()."""

        class BrokenAgent(BaseAgent):
            name = "broken"
            description = "always explodes"

            def run(self, conn):
                raise RuntimeError("intentional test error")

        agent = BrokenAgent()
        result = agent.run_safe(db)
        assert result == []

    def test_active_titles_empty_on_fresh_db(self, db):
        """No recommendations yet → _active_titles returns empty set."""
        agent = PromotionsAgent()
        assert agent._active_titles(db) == set()

    def test_active_titles_returns_titles_of_active_recs(self, db):
        """_active_titles must include titles of non-dismissed recs."""
        save_recommendation(
            db,
            type_="promotion_alert",
            priority="high",
            title="My Alert",
            body="body text",
        )
        agent = PromotionsAgent()
        assert "My Alert" in agent._active_titles(db)

    def test_active_titles_excludes_dismissed_recs(self, db):
        """Dismissed recommendations must NOT appear in _active_titles."""
        from axen_database import dismiss_recommendation

        rec_id = save_recommendation(
            db,
            type_="promotion_alert",
            priority="medium",
            title="Dismissed Alert",
            body="body",
        )
        dismiss_recommendation(db, rec_id)
        agent = PromotionsAgent()
        assert "Dismissed Alert" not in agent._active_titles(db)


# ─────────────────────────────────────────────
#  TestPromotionsAgent
# ─────────────────────────────────────────────

class TestPromotionsAgent:
    """Functional tests for PromotionsAgent.run()."""

    # ── Basic emission ────────────────────────────────────────────────────

    def test_no_recommendations_on_empty_db(self, db):
        """Empty DB → no promotions → empty list."""
        agent = PromotionsAgent()
        assert agent.run(db) == []

    def test_no_recommendations_when_no_discounts(self, db):
        """Products without discount context produce no alerts."""
        ingest_scrape_run(db, [_plain()])
        agent = PromotionsAgent()
        assert agent.run(db) == []

    def test_no_recommendation_for_discount_below_threshold(self, db):
        """discount_pct < MIN_DISCOUNT_PCT (10) → no alert."""
        ingest_scrape_run(db, [_discounted(discount_pct=5)])
        agent = PromotionsAgent()
        assert agent.run(db) == []

    def test_no_recommendation_for_discount_exactly_below_threshold(self, db):
        """discount_pct = 9 (just below 10) → no alert."""
        ingest_scrape_run(db, [_discounted(discount_pct=9)])
        agent = PromotionsAgent()
        assert agent.run(db) == []

    def test_creates_one_recommendation_at_threshold(self, db):
        """discount_pct == MIN_DISCOUNT_PCT (10) → one alert."""
        ingest_scrape_run(db, [_discounted(discount_pct=10)])
        agent = PromotionsAgent()
        recs = agent.run(db)
        assert len(recs) == 1

    def test_creates_one_recommendation_for_discount_above_threshold(self, db):
        """Happy path: one product with 25 % discount → one alert."""
        ingest_scrape_run(db, [_discounted(discount_pct=25)])
        agent = PromotionsAgent()
        recs = agent.run(db)
        assert len(recs) == 1

    # ── Return dict structure ─────────────────────────────────────────────

    def test_return_dict_has_required_keys(self, db):
        ingest_scrape_run(db, [_discounted(discount_pct=20)])
        rec = PromotionsAgent().run(db)[0]
        assert "id" in rec
        assert "type" in rec
        assert "priority" in rec
        assert "title" in rec
        assert "body" in rec
        assert "data" in rec

    def test_type_is_promotion_alert(self, db):
        ingest_scrape_run(db, [_discounted(discount_pct=20)])
        rec = PromotionsAgent().run(db)[0]
        assert rec["type"] == "promotion_alert"

    # ── Priority ──────────────────────────────────────────────────────────

    def test_priority_high_for_discount_gte_20(self, db):
        ingest_scrape_run(db, [_discounted(discount_pct=20)])
        rec = PromotionsAgent().run(db)[0]
        assert rec["priority"] == "high"

    def test_priority_high_for_discount_above_20(self, db):
        ingest_scrape_run(db, [_discounted(discount_pct=35)])
        rec = PromotionsAgent().run(db)[0]
        assert rec["priority"] == "high"

    def test_priority_medium_for_discount_between_10_and_19(self, db):
        ingest_scrape_run(db, [_discounted(discount_pct=15)])
        rec = PromotionsAgent().run(db)[0]
        assert rec["priority"] == "medium"

    def test_priority_medium_at_exactly_10(self, db):
        ingest_scrape_run(db, [_discounted(discount_pct=10)])
        rec = PromotionsAgent().run(db)[0]
        assert rec["priority"] == "medium"

    # ── One alert per material ────────────────────────────────────────────

    def test_one_alert_per_material_with_two_stores(self, db):
        """Two competing stores in the same material → single alert."""
        ingest_scrape_run(db, [
            _discounted(store="Key Design", material="couro",
                        discount_pct=25, url="https://kd.com/p1"),
            _discounted(store="Beroc", material="couro",
                        discount_pct=15, url="https://beroc.com/p1"),
        ])
        recs = PromotionsAgent().run(db)
        assert len(recs) == 1

    def test_alert_references_highest_discount_store(self, db):
        """The store with the highest discount should be the alert's focal store."""
        ingest_scrape_run(db, [
            _discounted(store="Key Design", material="couro",
                        discount_pct=25, url="https://kd.com/p1"),
            _discounted(store="Beroc", material="couro",
                        discount_pct=15, url="https://beroc.com/p1"),
        ])
        rec = PromotionsAgent().run(db)[0]
        assert rec["data"]["store"] == "Key Design"
        assert rec["data"]["max_discount_pct"] == 25

    def test_separate_alerts_for_different_materials(self, db):
        """Products in two distinct materials produce two separate alerts."""
        ingest_scrape_run(db, [
            _discounted(material="couro", discount_pct=20, url="https://kd.com/couro"),
            _discounted(material="corda", discount_pct=15, url="https://kd.com/corda"),
        ])
        recs = PromotionsAgent().run(db)
        assert len(recs) == 2
        materials = {r["data"]["material"] for r in recs}
        assert materials == {"couro", "corda"}

    # ── Title format ──────────────────────────────────────────────────────

    def test_title_contains_material(self, db):
        ingest_scrape_run(db, [_discounted(material="couro", discount_pct=20)])
        rec = PromotionsAgent().run(db)[0]
        assert "couro" in rec["title"]

    def test_title_contains_store_name(self, db):
        ingest_scrape_run(db, [_discounted(store="Key Design", discount_pct=20)])
        rec = PromotionsAgent().run(db)[0]
        assert "Key Design" in rec["title"]

    def test_title_contains_discount_pct(self, db):
        ingest_scrape_run(db, [_discounted(discount_pct=30)])
        rec = PromotionsAgent().run(db)[0]
        assert "30" in rec["title"]

    # ── Body text ─────────────────────────────────────────────────────────

    def test_body_contains_store_name(self, db):
        ingest_scrape_run(db, [_discounted(store="Key Design", discount_pct=20)])
        rec = PromotionsAgent().run(db)[0]
        assert "Key Design" in rec["body"]

    def test_body_contains_discount_pct(self, db):
        ingest_scrape_run(db, [_discounted(discount_pct=30)])
        rec = PromotionsAgent().run(db)[0]
        assert "30" in rec["body"]

    def test_body_contains_material(self, db):
        ingest_scrape_run(db, [_discounted(material="couro", discount_pct=20)])
        rec = PromotionsAgent().run(db)[0]
        assert "couro" in rec["body"]

    # ── Data payload ──────────────────────────────────────────────────────

    def test_data_material_matches_product_material(self, db):
        ingest_scrape_run(db, [_discounted(material="couro", discount_pct=20)])
        rec = PromotionsAgent().run(db)[0]
        assert rec["data"]["material"] == "couro"

    def test_data_max_discount_pct_is_highest(self, db):
        """max_discount_pct reflects the store's highest discount, not the average."""
        ingest_scrape_run(db, [
            _discounted(store="Key Design", discount_pct=30, url="https://kd.com/p1"),
            _discounted(store="Key Design", discount_pct=15, url="https://kd.com/p2"),
        ])
        rec = PromotionsAgent().run(db)[0]
        assert rec["data"]["max_discount_pct"] == 30

    def test_data_product_count_covers_all_material_group(self, db):
        """product_count is the total number of discounted products in the material."""
        ingest_scrape_run(db, [
            _discounted(store="Key Design", discount_pct=25, url="https://kd.com/p1"),
            _discounted(store="Key Design", discount_pct=20, url="https://kd.com/p2"),
            _discounted(store="Beroc", discount_pct=12, url="https://beroc.com/p1"),
        ])
        rec = PromotionsAgent().run(db)[0]
        assert rec["data"]["product_count"] == 3

    def test_data_min_price_is_lowest_across_material_group(self, db):
        """min_price is the floor across all discounted products in the material."""
        ingest_scrape_run(db, [
            _discounted(store="Key Design", price=90.0, discount_pct=25, url="https://kd.com/p1"),
            _discounted(store="Beroc", price=70.0, discount_pct=12, url="https://beroc.com/p1"),
        ])
        rec = PromotionsAgent().run(db)[0]
        assert rec["data"]["min_price"] == pytest.approx(70.0)

    def test_data_sample_urls_contains_product_url(self, db):
        ingest_scrape_run(db, [
            _discounted(url="https://keydesign.com.br/pulseira-couro-1", discount_pct=20),
        ])
        rec = PromotionsAgent().run(db)[0]
        assert "https://keydesign.com.br/pulseira-couro-1" in rec["data"]["sample_urls"]

    def test_data_sample_urls_capped_at_three(self, db):
        """At most 3 URLs are included in sample_urls."""
        products = [
            _discounted(discount_pct=20, url=f"https://kd.com/p{i}")
            for i in range(5)
        ]
        ingest_scrape_run(db, products)
        rec = PromotionsAgent().run(db)[0]
        assert len(rec["data"]["sample_urls"]) <= 3

    # ── Persistence ───────────────────────────────────────────────────────

    def test_recommendation_written_to_db(self, db):
        """Recommendations must be persisted, not just returned in-memory."""
        ingest_scrape_run(db, [_discounted(discount_pct=20)])
        PromotionsAgent().run(db)
        saved = get_recommendations(db, type_="promotion_alert")
        assert len(saved) == 1

    def test_db_rec_priority_matches_returned_priority(self, db):
        ingest_scrape_run(db, [_discounted(discount_pct=25)])  # → "high"
        rec = PromotionsAgent().run(db)[0]
        saved = get_recommendations(db, type_="promotion_alert")[0]
        assert saved["priority"] == rec["priority"] == "high"

    # ── Duplicate guard ───────────────────────────────────────────────────

    def test_second_run_skips_active_duplicate_title(self, db):
        """Running the agent twice must not insert the same alert a second time."""
        ingest_scrape_run(db, [_discounted(discount_pct=20)])
        agent = PromotionsAgent()
        recs1 = agent.run(db)
        recs2 = agent.run(db)
        assert len(recs1) == 1
        assert len(recs2) == 0  # title already active → skipped

    def test_alert_reissued_after_dismissal(self, db):
        """Once a recommendation is dismissed, the same alert may be re-created."""
        from axen_database import dismiss_recommendation

        ingest_scrape_run(db, [_discounted(discount_pct=20)])
        agent = PromotionsAgent()
        recs1 = agent.run(db)
        dismiss_recommendation(db, recs1[0]["id"])
        recs2 = agent.run(db)
        assert len(recs2) == 1  # no longer active → re-alert allowed
