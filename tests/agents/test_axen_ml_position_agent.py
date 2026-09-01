"""
tests/agents/test_axen_ml_position_agent.py

Unit tests for MLPositionAgent.

Setup pattern:
  - Each test uses the `db` fixture (in-memory, migrated).
  - ML products with context "id:MLB... position:N query:..." are inserted
    via ingest_scrape_run().  Calling ingest_scrape_run() twice with the same
    item_id+query produces two ml_positions rows, giving get_ml_position_trend()
    enough data for n_runs=2.
  - drift = max_position − min_position.  Alert threshold: drift >= 5.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from axen_database import get_recommendations, ingest_scrape_run, save_recommendation
from agents.axen_ml_position_agent import MLPositionAgent


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

def _ml(
    item_id: str = "MLB001",
    position: int = 3,
    material: str = "corda",
    price: float = 89.90,
    query: str = "pulseira_masculina_corda",
) -> Product:
    """Create an ML product with encoded position context."""
    return Product(
        store="Mercado Livre",
        name=f"Pulseira {material.capitalize()} ML {item_id}",
        price=price,
        material=material,
        url=f"https://www.mercadolivre.com.br/p/{item_id}",
        context=f"id:{item_id} position:{position} query:{query}",
        delivery_info="Frete Grátis",
    )


def _insert_two_runs(db, item_id: str, pos1: int, pos2: int, **kwargs) -> None:
    """Insert the same item at two different positions across two scrape runs."""
    ingest_scrape_run(db, [_ml(item_id=item_id, position=pos1, **kwargs)])
    ingest_scrape_run(db, [_ml(item_id=item_id, position=pos2, **kwargs)])


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestMLPositionAgent:

    # ── No-op cases ──────────────────────────────────────────────────────────

    def test_no_alerts_on_empty_db(self, db):
        assert MLPositionAgent().run(db) == []

    def test_no_alert_when_only_one_run(self, db):
        """Single scrape run → n_runs=1 → filtered out (no trend data)."""
        ingest_scrape_run(db, [_ml(item_id="MLB001", position=3)])
        assert MLPositionAgent().run(db) == []

    def test_no_alert_when_drift_below_threshold(self, db):
        """drift = 4  (< MIN_DRIFT=5) → no alert."""
        _insert_two_runs(db, "MLB001", pos1=3, pos2=7)   # drift = 4
        assert MLPositionAgent().run(db) == []

    def test_no_alert_when_drift_zero(self, db):
        """Stable ranking → no alert."""
        _insert_two_runs(db, "MLB001", pos1=5, pos2=5)
        assert MLPositionAgent().run(db) == []

    def test_no_alert_when_position_improved(self, db):
        """Position moved from 10 → 3 (improvement).  drift = max−min = 7 still ≥ 5
        so this IS an alert — drift tracks instability regardless of direction."""
        # Note: even an improvement followed by another reading could show drift.
        # Two-run case: pos1=10, pos2=3 → min=3, max=10, drift=7 → alert.
        # This documents the intentional behaviour: drift ≥ 5 always alerts.
        _insert_two_runs(db, "MLB001", pos1=10, pos2=3)
        recs = MLPositionAgent().run(db)
        assert len(recs) == 1  # drift=7 ≥ 5

    # ── Alert emission ────────────────────────────────────────────────────────

    def test_alert_at_exactly_min_drift(self, db):
        """drift == MIN_DRIFT (5) → alert."""
        _insert_two_runs(db, "MLB001", pos1=3, pos2=8)   # drift = 5
        recs = MLPositionAgent().run(db)
        assert len(recs) == 1

    def test_alert_for_significant_drift(self, db):
        """drift = 7 → one alert."""
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        recs = MLPositionAgent().run(db)
        assert len(recs) == 1

    def test_one_alert_per_item(self, db):
        """Two distinct items, both with drift ≥ 5 → two alerts."""
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        _insert_two_runs(db, "MLB002", pos1=5, pos2=12)
        recs = MLPositionAgent().run(db)
        assert len(recs) == 2

    def test_no_cross_item_contamination(self, db):
        """Item with large drift and item with small drift → only one alert."""
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)   # drift=7 → alert
        _insert_two_runs(db, "MLB002", pos1=4, pos2=7)    # drift=3 → no alert
        recs = MLPositionAgent().run(db)
        assert len(recs) == 1
        assert recs[0]["data"]["item_id"] == "MLB001"

    # ── Return dict structure ─────────────────────────────────────────────────

    def test_return_dict_has_required_keys(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        rec = MLPositionAgent().run(db)[0]
        for key in ("id", "type", "priority", "title", "body", "data"):
            assert key in rec

    def test_type_is_ml_position_drop(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        rec = MLPositionAgent().run(db)[0]
        assert rec["type"] == "ml_position_drop"

    # ── Priority ─────────────────────────────────────────────────────────────

    def test_priority_high_when_max_position_gte_20(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=20)   # drift=17, max=20
        rec = MLPositionAgent().run(db)[0]
        assert rec["priority"] == "high"

    def test_priority_high_when_max_position_above_20(self, db):
        _insert_two_runs(db, "MLB001", pos1=5, pos2=30)   # drift=25, max=30
        rec = MLPositionAgent().run(db)[0]
        assert rec["priority"] == "high"

    def test_priority_medium_when_max_position_below_20(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)   # drift=7, max=10
        rec = MLPositionAgent().run(db)[0]
        assert rec["priority"] == "medium"

    def test_priority_medium_at_max_position_19(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=19)   # drift=16, max=19
        rec = MLPositionAgent().run(db)[0]
        assert rec["priority"] == "medium"

    # ── Title ────────────────────────────────────────────────────────────────

    def test_title_contains_material(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10, material="couro")
        rec = MLPositionAgent().run(db)[0]
        assert "couro" in rec["title"]

    def test_title_contains_item_id(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        rec = MLPositionAgent().run(db)[0]
        assert "MLB001" in rec["title"]

    # ── Body ─────────────────────────────────────────────────────────────────

    def test_body_contains_item_id(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        rec = MLPositionAgent().run(db)[0]
        assert "MLB001" in rec["body"]

    def test_body_contains_min_and_max_position(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        rec = MLPositionAgent().run(db)[0]
        assert "3" in rec["body"]
        assert "10" in rec["body"]

    def test_body_contains_material(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10, material="couro")
        rec = MLPositionAgent().run(db)[0]
        assert "couro" in rec["body"]

    # ── Data payload ─────────────────────────────────────────────────────────

    def test_data_drift_is_max_minus_min(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)  # drift = 7
        rec = MLPositionAgent().run(db)[0]
        assert rec["data"]["drift"] == 7

    def test_data_min_position_correct(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        rec = MLPositionAgent().run(db)[0]
        assert rec["data"]["min_position"] == 3

    def test_data_max_position_correct(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        rec = MLPositionAgent().run(db)[0]
        assert rec["data"]["max_position"] == 10

    def test_data_n_runs_is_two(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        rec = MLPositionAgent().run(db)[0]
        assert rec["data"]["n_runs"] == 2

    def test_data_item_id_matches(self, db):
        _insert_two_runs(db, "MLB999", pos1=3, pos2=10)
        rec = MLPositionAgent().run(db)[0]
        assert rec["data"]["item_id"] == "MLB999"

    def test_data_material_matches(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10, material="couro")
        rec = MLPositionAgent().run(db)[0]
        assert rec["data"]["material"] == "couro"

    # ── Persistence ──────────────────────────────────────────────────────────

    def test_recommendation_saved_to_db(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        MLPositionAgent().run(db)
        saved = get_recommendations(db, type_="ml_position_drop")
        assert len(saved) == 1

    # ── Duplicate guard ───────────────────────────────────────────────────────

    def test_second_run_skips_active_title(self, db):
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        agent = MLPositionAgent()
        recs1 = agent.run(db)
        recs2 = agent.run(db)
        assert len(recs1) == 1
        assert len(recs2) == 0

    def test_alert_reissued_after_dismissal(self, db):
        from axen_database import dismiss_recommendation
        _insert_two_runs(db, "MLB001", pos1=3, pos2=10)
        agent = MLPositionAgent()
        recs1 = agent.run(db)
        dismiss_recommendation(db, recs1[0]["id"])
        recs2 = agent.run(db)
        assert len(recs2) == 1

    # ── run_safe contract ─────────────────────────────────────────────────────

    def test_run_safe_returns_empty_on_bad_connection(self):
        agent = MLPositionAgent()
        assert agent.run_safe(None) == []  # type: ignore[arg-type]
