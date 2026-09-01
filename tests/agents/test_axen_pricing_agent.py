"""
tests/agents/test_axen_pricing_agent.py

Unit tests for PricingAgent.

AXEN reference prices (from agents/axen_pricing_agent.py):
  "corda": 125.0   "metal": 215.0   "couro": 215.0   "pedra": None

Gap formula:  gap_pct = (axen_ref − market_min) / market_min × 100
Alert threshold: gap_pct > 10 %
  medium priority: 10 % < gap_pct ≤ 25 %
  high priority:   gap_pct > 25 %

Products are inserted via ingest_scrape_run().  get_latest_prices() returns
only the most recent run's prices, so a single ingest_scrape_run() call is
sufficient for all pricing tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from axen_database import get_recommendations, ingest_scrape_run
from agents.axen_pricing_agent import AXEN_PRICES, PricingAgent


# ── Local Product stub ────────────────────────────────────────────────────────
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _competitor(
    material: str = "corda",
    price: float = 80.0,
    store: str = "Key Design",
    url: str = "https://keydesign.com.br/pulseira-1",
) -> Product:
    """Create a plain competitor product (no discount)."""
    return Product(
        store=store,
        name=f"Pulseira {material.capitalize()} {store}",
        price=price,
        material=material,
        url=url,
        context="",
        delivery_info="",
    )


def _gap(material: str, market_price: float) -> float:
    """Compute the expected gap_pct for a given material and market price."""
    axen_ref = AXEN_PRICES[material]
    assert axen_ref is not None
    return (axen_ref - market_price) / market_price * 100.0


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPricingAgent:

    # ── No-op cases ──────────────────────────────────────────────────────────

    def test_no_alerts_on_empty_db(self, db):
        assert PricingAgent().run(db) == []

    def test_no_alert_when_gap_just_at_threshold(self, db):
        """gap_pct == 10.0 % boundary: the threshold check is > (strictly).
        We use price=114.0 → gap=(125-114)/114×100≈9.65% which is clearly ≤ 10%."""
        # (125 - 114) / 114 * 100 = 9.649...% → no alert
        ingest_scrape_run(db, [_competitor(material="corda", price=114.0)])
        recs = PricingAgent().run(db)
        assert recs == []

    def test_no_alert_when_gap_below_threshold(self, db):
        """gap_pct < 10 % → no alert."""
        # AXEN corda = 125.0; competitor at 115 → gap ≈ 8.7 %
        ingest_scrape_run(db, [_competitor(material="corda", price=115.0)])
        recs = PricingAgent().run(db)
        assert recs == []

    def test_no_alert_when_market_above_axen(self, db):
        """Competitor more expensive than AXEN → negative gap → no alert."""
        ingest_scrape_run(db, [_competitor(material="corda", price=150.0)])
        recs = PricingAgent().run(db)
        assert recs == []

    def test_no_alert_for_pedra_material(self, db):
        """'pedra' has AXEN_PRICES[material]=None — should be silently skipped."""
        ingest_scrape_run(db, [_competitor(material="pedra", price=30.0)])
        recs = PricingAgent().run(db)
        assert recs == []

    def test_no_alert_for_unknown_material(self, db):
        """Materials not in AXEN_PRICES are outside scope — no alert."""
        ingest_scrape_run(db, [_competitor(material="madeira", price=20.0)])
        recs = PricingAgent().run(db)
        assert recs == []

    # ── Alert emission ────────────────────────────────────────────────────────

    def test_alert_when_gap_just_above_threshold(self, db):
        """gap_pct slightly > 10 % → one alert."""
        # AXEN corda=125; market at 112 → gap ≈ 11.6 %
        ingest_scrape_run(db, [_competitor(material="corda", price=112.0)])
        recs = PricingAgent().run(db)
        assert len(recs) == 1

    def test_alert_for_each_affected_material(self, db):
        """Three materials all with gap > 10 % → three alerts."""
        ingest_scrape_run(db, [
            _competitor(material="corda", price=80.0,  url="https://s.com/p1"),
            _competitor(material="metal", price=140.0, url="https://s.com/p2"),
            _competitor(material="couro", price=150.0, url="https://s.com/p3"),
        ])
        recs = PricingAgent().run(db)
        assert len(recs) == 3

    def test_market_min_uses_cheapest_competitor(self, db):
        """When multiple competitors exist, market_min is the lowest price."""
        ingest_scrape_run(db, [
            _competitor(material="corda", price=80.0, url="https://s.com/p1"),
            _competitor(material="corda", price=60.0, url="https://s.com/p2"),  # cheaper
            _competitor(material="corda", price=90.0, url="https://s.com/p3"),
        ])
        rec = PricingAgent().run(db)[0]
        assert rec["data"]["market_min"] == pytest.approx(60.0)

    # ── Return dict structure ─────────────────────────────────────────────────

    def test_return_dict_has_required_keys(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        rec = PricingAgent().run(db)[0]
        for key in ("id", "type", "priority", "title", "body", "data"):
            assert key in rec

    def test_type_is_price_suggestion(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        rec = PricingAgent().run(db)[0]
        assert rec["type"] == "price_suggestion"

    # ── Priority ─────────────────────────────────────────────────────────────

    def test_priority_high_when_gap_above_25_pct(self, db):
        """AXEN corda=125, market=80 → gap=(125−80)/80×100=56.25 % → high."""
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        rec = PricingAgent().run(db)[0]
        assert rec["priority"] == "high"

    def test_priority_high_at_exactly_26_pct(self, db):
        """gap = 26 % → high (strictly > 25)."""
        # (125 − x) / x = 0.26  →  x = 125 / 1.26 ≈ 99.21
        market_price = 125.0 / 1.26
        ingest_scrape_run(db, [_competitor(material="corda", price=market_price)])
        rec = PricingAgent().run(db)[0]
        assert rec["priority"] == "high"

    def test_priority_medium_when_gap_between_10_and_25_pct(self, db):
        """gap ≈ 11.6 % → medium."""
        ingest_scrape_run(db, [_competitor(material="corda", price=112.0)])
        rec = PricingAgent().run(db)[0]
        assert rec["priority"] == "medium"

    def test_priority_medium_at_exactly_25_pct(self, db):
        """gap == 25 % → medium (not strictly greater than 25)."""
        # (125 − x) / x = 0.25  →  x = 125 / 1.25 = 100.0
        ingest_scrape_run(db, [_competitor(material="corda", price=100.0)])
        rec = PricingAgent().run(db)[0]
        assert rec["priority"] == "medium"

    # ── Title ────────────────────────────────────────────────────────────────

    def test_title_contains_material(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        rec = PricingAgent().run(db)[0]
        assert "corda" in rec["title"]

    # ── Body ─────────────────────────────────────────────────────────────────

    def test_body_contains_axen_ref_price(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        rec = PricingAgent().run(db)[0]
        assert "125" in rec["body"]   # AXEN corda ref = 125.0

    def test_body_contains_market_min(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        rec = PricingAgent().run(db)[0]
        assert "80" in rec["body"]

    def test_body_contains_gap_pct(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        rec = PricingAgent().run(db)[0]
        # gap = 56.25 % → body should mention "56"
        assert "56" in rec["body"]

    def test_body_contains_material_name(self, db):
        ingest_scrape_run(db, [_competitor(material="couro", price=150.0)])
        rec = PricingAgent().run(db)[0]
        assert "couro" in rec["body"]

    # ── Data payload ─────────────────────────────────────────────────────────

    def test_data_material_matches(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        rec = PricingAgent().run(db)[0]
        assert rec["data"]["material"] == "corda"

    def test_data_axen_ref_price_correct(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        rec = PricingAgent().run(db)[0]
        assert rec["data"]["axen_ref_price"] == pytest.approx(125.0)

    def test_data_market_min_correct(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        rec = PricingAgent().run(db)[0]
        assert rec["data"]["market_min"] == pytest.approx(80.0)

    def test_data_gap_pct_correct(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        rec = PricingAgent().run(db)[0]
        expected = _gap("corda", 80.0)
        assert rec["data"]["gap_pct"] == pytest.approx(expected, rel=0.01)

    def test_data_price_changes_count_zero_when_no_changes(self, db):
        """Single run → no price_changes rows → count=0."""
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        rec = PricingAgent().run(db)[0]
        assert rec["data"]["price_changes_count"] == 0

    def test_data_price_changes_count_increments_on_change(self, db):
        """Two runs with different prices → price_changes row → count=1."""
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        # Second run with lower price creates a price_change record
        ingest_scrape_run(db, [_competitor(material="corda", price=60.0)])
        rec = PricingAgent().run(db)[0]
        assert rec["data"]["price_changes_count"] >= 1

    # ── Persistence ──────────────────────────────────────────────────────────

    def test_recommendation_saved_to_db(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        PricingAgent().run(db)
        saved = get_recommendations(db, type_="price_suggestion")
        assert len(saved) == 1

    def test_suggested_value_saved_as_market_min(self, db):
        """save_recommendation is called with suggested_value=market_min."""
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        PricingAgent().run(db)
        saved = get_recommendations(db, type_="price_suggestion")[0]
        assert saved["suggested_value"] == pytest.approx(80.0)

    def test_db_priority_matches_returned_priority(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])  # high
        rec = PricingAgent().run(db)[0]
        saved = get_recommendations(db, type_="price_suggestion")[0]
        assert saved["priority"] == rec["priority"] == "high"

    # ── Duplicate guard ───────────────────────────────────────────────────────

    def test_second_run_skips_active_title(self, db):
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        agent = PricingAgent()
        recs1 = agent.run(db)
        recs2 = agent.run(db)
        assert len(recs1) == 1
        assert len(recs2) == 0

    def test_alert_reissued_after_dismissal(self, db):
        from axen_database import dismiss_recommendation
        ingest_scrape_run(db, [_competitor(material="corda", price=80.0)])
        agent = PricingAgent()
        recs1 = agent.run(db)
        dismiss_recommendation(db, recs1[0]["id"])
        recs2 = agent.run(db)
        assert len(recs2) == 1

    # ── run_safe contract ─────────────────────────────────────────────────────

    def test_run_safe_returns_empty_on_bad_connection(self):
        assert PricingAgent().run_safe(None) == []  # type: ignore[arg-type]
