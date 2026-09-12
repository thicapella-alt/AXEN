"""
tests/test_axen_scheduler.py

Unit tests for axen_scheduler.py utility functions and the run_pipeline()
core pipeline.  All tests are offline — no HTTP, no real scraper, no real
APScheduler daemon.  External collaborators (axen_price_scraper.main,
get_connection, ingest_scrape_run, …) are replaced with monkeypatches or
MagicMock objects.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import axen_scheduler
from axen_scheduler import (
    BRT,
    _next_run_time,
    archive_csvs,
    rotate_logs,
    run_pipeline,
    update_latest_link,
)


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _fake_product(store: str = "Beroc") -> MagicMock:
    """Return a minimal Product-like MagicMock with a .store attribute."""
    p = MagicMock()
    p.store = store
    return p


def _patch_pipeline(
    monkeypatch,
    *,
    products=None,
    run_id: int = 1,
    ingest_exc=None,
):
    """
    Convenience: patch all external collaborators used by run_pipeline().

    products   : list returned by the fake scraper (default: one Beroc product)
    run_id     : value returned by fake ingest_scrape_run
    ingest_exc : if set, fake ingest_scrape_run raises this instead of returning
    """
    if products is None:
        products = [_fake_product()]

    monkeypatch.setattr("axen_price_scraper.main", lambda **kw: products)
    monkeypatch.setattr("axen_scheduler.get_connection", lambda p=None: MagicMock())
    monkeypatch.setattr("axen_scheduler.migrate", lambda c: None)

    if ingest_exc is not None:
        def _bad_ingest(conn, prods, stores_scraped=None):
            raise ingest_exc
        monkeypatch.setattr("axen_scheduler.ingest_scrape_run", _bad_ingest)
    else:
        monkeypatch.setattr(
            "axen_scheduler.ingest_scrape_run",
            lambda c, p, stores_scraped=None: run_id,
        )

    monkeypatch.setattr("axen_scheduler.fail_run", lambda *a: None)
    monkeypatch.setattr("axen_scheduler.archive_csvs", lambda *a, **kw: [])
    monkeypatch.setattr("axen_scheduler.update_latest_link", lambda *a: True)
    monkeypatch.setattr("axen_scheduler.rotate_logs", lambda *a, **kw: 0)


# ─────────────────────────────────────────────
#  TestRotateLogs
# ─────────────────────────────────────────────

class TestRotateLogs:
    def test_deletes_oldest_files_when_over_limit(self, tmp_path):
        """35 files, keep=30 → 5 oldest deleted."""
        for i in range(35):
            (tmp_path / f"2024-01-{i + 1:02d}.log").write_text("x")
        deleted = rotate_logs(tmp_path, keep=30)
        assert deleted == 5
        assert len(list(tmp_path.glob("*.log"))) == 30

    def test_keeps_lexicographically_newest_files(self, tmp_path):
        """After rotation, the 30 latest (by name) files survive."""
        names = [f"2024-01-{i + 1:02d}.log" for i in range(35)]
        for n in names:
            (tmp_path / n).write_text("x")
        rotate_logs(tmp_path, keep=30)
        remaining = sorted(p.name for p in tmp_path.glob("*.log"))
        assert remaining == sorted(names)[-30:]

    def test_no_deletion_when_at_exact_limit(self, tmp_path):
        """Exactly 30 files → nothing deleted."""
        for i in range(30):
            (tmp_path / f"2024-01-{i + 1:02d}.log").write_text("x")
        assert rotate_logs(tmp_path, keep=30) == 0

    def test_no_deletion_when_under_limit(self, tmp_path):
        """10 files, keep=30 → nothing deleted."""
        for i in range(10):
            (tmp_path / f"2024-01-{i + 1:02d}.log").write_text("x")
        assert rotate_logs(tmp_path, keep=30) == 0

    def test_empty_directory_returns_zero(self, tmp_path):
        assert rotate_logs(tmp_path, keep=30) == 0

    def test_nonexistent_directory_returns_zero(self, tmp_path):
        assert rotate_logs(tmp_path / "no_such_dir", keep=30) == 0

    def test_only_log_files_are_counted(self, tmp_path):
        """Non-.log files are ignored; they do not count toward keep limit."""
        for i in range(35):
            (tmp_path / f"2024-01-{i + 1:02d}.log").write_text("x")
        (tmp_path / "readme.txt").write_text("not a log")
        rotate_logs(tmp_path, keep=30)
        # .txt file must survive; only .log files were rotated
        assert (tmp_path / "readme.txt").exists()
        assert len(list(tmp_path.glob("*.log"))) == 30


# ─────────────────────────────────────────────
#  TestArchiveCsvs
# ─────────────────────────────────────────────

class TestArchiveCsvs:
    def test_copies_prices_csv_with_dated_name(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "axen_competitive_prices.csv").write_text("header\nrow1")
        dest = tmp_path / "dest"
        created = archive_csvs(src, dest, "2024-01-15")
        assert "axen_prices_2024-01-15.csv" in created
        assert (dest / "axen_prices_2024-01-15.csv").read_text() == "header\nrow1"

    def test_copies_delivery_detail_csv(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "axen_delivery_detail.csv").write_text("detail_data")
        dest = tmp_path / "dest"
        created = archive_csvs(src, dest, "2024-01-15")
        assert "axen_delivery_detail_2024-01-15.csv" in created

    def test_skips_missing_source_files(self, tmp_path):
        """No source files → nothing copied, returns empty list."""
        src = tmp_path / "src"
        src.mkdir()
        dest = tmp_path / "dest"
        created = archive_csvs(src, dest, "2024-01-15")
        assert created == []

    def test_creates_dest_dir_if_absent(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "axen_competitive_prices.csv").write_text("x")
        dest = tmp_path / "data" / "2024-01-15"
        archive_csvs(src, dest, "2024-01-15")
        assert dest.is_dir()

    def test_promotions_csv_included_when_present(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "axen_competitive_promotions.csv").write_text("promo")
        dest = tmp_path / "dest"
        created = archive_csvs(src, dest, "2024-02-01")
        assert "axen_promotions_2024-02-01.csv" in created

    def test_promotions_csv_omitted_when_absent(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "axen_competitive_prices.csv").write_text("x")
        dest = tmp_path / "dest"
        created = archive_csvs(src, dest, "2024-02-01")
        assert not any("promotions" in n for n in created)

    def test_date_string_appears_in_every_created_filename(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for name in [
            "axen_competitive_prices.csv",
            "axen_delivery_detail.csv",
            "axen_competitive_summary.csv",
            "axen_competitive_delivery.csv",
        ]:
            (src / name).write_text("x")
        dest = tmp_path / "dest"
        created = archive_csvs(src, dest, "2024-03-22")
        assert all("2024-03-22" in n for n in created)

    def test_copies_all_five_source_files_when_present(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for name in [
            "axen_competitive_prices.csv",
            "axen_delivery_detail.csv",
            "axen_competitive_summary.csv",
            "axen_competitive_delivery.csv",
            "axen_competitive_promotions.csv",
        ]:
            (src / name).write_text("x")
        dest = tmp_path / "dest"
        created = archive_csvs(src, dest, "2024-04-10")
        assert len(created) == 5


# ─────────────────────────────────────────────
#  TestUpdateLatestLink
# ─────────────────────────────────────────────

class TestUpdateLatestLink:
    @pytest.mark.skipif(sys.platform == "win32", reason="symlink test — Unix only")
    def test_creates_symlink_on_unix(self, tmp_path):
        data_dir = tmp_path / "data"
        dated = data_dir / "2024-01-15"
        dated.mkdir(parents=True)
        result = update_latest_link(data_dir, dated)
        assert result is True
        latest = data_dir / "latest"
        assert latest.exists() or latest.is_symlink()

    @pytest.mark.skipif(sys.platform == "win32", reason="symlink test — Unix only")
    def test_updates_existing_symlink_on_unix(self, tmp_path):
        data_dir = tmp_path / "data"
        old = data_dir / "2024-01-14"
        new = data_dir / "2024-01-15"
        old.mkdir(parents=True)
        new.mkdir(parents=True)
        update_latest_link(data_dir, old)
        result = update_latest_link(data_dir, new)
        assert result is True
        assert (data_dir / "latest").resolve() == new.resolve()

    def test_returns_false_if_latest_is_real_directory(self, tmp_path):
        """Must refuse to delete a real directory (not a link)."""
        data_dir = tmp_path / "data"
        latest = data_dir / "latest"
        latest.mkdir(parents=True)
        dated = data_dir / "2024-01-15"
        dated.mkdir()
        result = update_latest_link(data_dir, dated)
        assert result is False

    def test_creates_data_dir_if_absent(self, tmp_path):
        data_dir = tmp_path / "new_data"
        dated = data_dir / "2024-01-15"
        dated.mkdir(parents=True)
        # On Windows this might fail due to permissions; just check it doesn't raise
        try:
            update_latest_link(data_dir, dated)
        except Exception as exc:
            pytest.fail(f"update_latest_link raised unexpectedly: {exc}")
        assert data_dir.exists()

    @pytest.mark.skipif(sys.platform != "win32", reason="junction test — Windows only")
    def test_calls_mklink_on_windows(self, tmp_path):
        """On Windows, update_latest_link should invoke cmd /c mklink /J."""
        data_dir = tmp_path / "data"
        dated = data_dir / "2024-01-15"
        dated.mkdir(parents=True)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = update_latest_link(data_dir, dated)
        assert result is True
        assert mock_run.called
        cmd_args = mock_run.call_args[0][0]
        assert "mklink" in cmd_args
        assert "/J" in cmd_args


# ─────────────────────────────────────────────
#  TestNextRunTime
# ─────────────────────────────────────────────

class TestNextRunTime:
    def test_result_is_always_in_the_future(self):
        from datetime import datetime
        next_run = _next_run_time()
        assert next_run > datetime.now(BRT)

    def test_result_is_at_one_of_the_scheduled_hours(self):
        next_run = _next_run_time()
        assert next_run.hour in axen_scheduler.SCRAPE_HOURS
        assert next_run.minute == 0
        assert next_run.second == 0
        assert next_run.microsecond == 0

    def test_result_has_brt_timezone(self):
        next_run = _next_run_time()
        assert next_run.tzinfo is not None

    def test_result_is_within_longest_gap(self):
        """3x/dia (07/13/21 BRT) — o maior intervalo entre execuções é
        21:00 -> 07:00 do dia seguinte, 10 h."""
        from datetime import datetime, timedelta
        next_run = _next_run_time()
        now = datetime.now(BRT)
        assert next_run - now <= timedelta(hours=10)

    def test_picks_soonest_remaining_hour_today(self, monkeypatch):
        """Logo após as 07:00 BRT, a próxima execução deve ser 13:00 hoje,
        não 07:00 de amanhã."""
        from datetime import datetime

        fixed_now = datetime(2026, 9, 20, 8, 0, tzinfo=BRT)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz else fixed_now.replace(tzinfo=None)

        monkeypatch.setattr(axen_scheduler, "datetime", _FixedDatetime)
        next_run = _next_run_time()
        assert next_run.hour == 13
        assert next_run.day == 20

    def test_rolls_over_to_tomorrow_after_last_hour(self, monkeypatch):
        """Depois das 21:00 BRT, a próxima execução é 07:00 de amanhã."""
        from datetime import datetime

        fixed_now = datetime(2026, 9, 20, 22, 0, tzinfo=BRT)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz else fixed_now.replace(tzinfo=None)

        monkeypatch.setattr(axen_scheduler, "datetime", _FixedDatetime)
        next_run = _next_run_time()
        assert next_run.hour == 7
        assert next_run.day == 21


# ─────────────────────────────────────────────
#  TestRunPipeline
# ─────────────────────────────────────────────

class TestRunPipeline:
    """
    run_pipeline() is tested by replacing all I/O-heavy collaborators:
      - axen_price_scraper.main       (monkeypatch on the module attribute)
      - axen_scheduler.get_connection (module-level import binding)
      - axen_scheduler.migrate
      - axen_scheduler.ingest_scrape_run
      - axen_scheduler.fail_run
      - axen_scheduler.archive_csvs
      - axen_scheduler.update_latest_link
      - axen_scheduler.rotate_logs
    """

    def test_status_done_on_happy_path(self, monkeypatch):
        """All steps succeed → status='done', run_id and products_count set."""
        products = [_fake_product("Beroc"), _fake_product("Key Design")]
        _patch_pipeline(monkeypatch, products=products, run_id=7)
        result = run_pipeline(date_str="2024-01-15")
        assert result["status"] == "done"
        assert result["run_id"] == 7
        assert result["products_count"] == 2
        assert result["error"] is None

    def test_status_empty_when_scraper_returns_empty_list(self, monkeypatch):
        """Empty product list → status='empty', ingest not called."""
        _patch_pipeline(monkeypatch, products=[])
        ingest_called = []
        monkeypatch.setattr(
            "axen_scheduler.ingest_scrape_run",
            lambda *a, **kw: ingest_called.append(1) or 0,
        )
        result = run_pipeline(date_str="2024-01-15")
        assert result["status"] == "empty"
        assert ingest_called == []

    def test_status_empty_when_scraper_returns_none(self, monkeypatch):
        """scraper returns None (early exit inside main()) → treated as empty."""
        monkeypatch.setattr("axen_price_scraper.main", lambda **kw: None)
        result = run_pipeline(date_str="2024-01-15")
        assert result["status"] == "empty"

    def test_status_error_when_ingest_raises(self, monkeypatch):
        """ingest_scrape_run raises → status='error', error message preserved."""
        _patch_pipeline(monkeypatch, ingest_exc=RuntimeError("DB offline"))
        result = run_pipeline(date_str="2024-01-15")
        assert result["status"] == "error"
        assert "DB offline" in result["error"]

    def test_fail_run_called_when_run_id_acquired_before_exception(self, monkeypatch):
        """
        If ingest_scrape_run succeeds (run_id known) but a later step fails,
        fail_run must be called with that run_id.
        """
        products = [_fake_product()]
        monkeypatch.setattr("axen_price_scraper.main", lambda **kw: products)
        monkeypatch.setattr("axen_scheduler.get_connection", lambda p=None: MagicMock())
        monkeypatch.setattr("axen_scheduler.migrate", lambda c: None)
        monkeypatch.setattr(
            "axen_scheduler.ingest_scrape_run",
            lambda c, p, stores_scraped=None: 99,
        )

        fail_run_calls: list[int] = []
        monkeypatch.setattr(
            "axen_scheduler.fail_run",
            lambda c, rid, msg: fail_run_calls.append(rid),
        )

        # archive_csvs raises after ingest succeeded — run_id is now 99
        monkeypatch.setattr(
            "axen_scheduler.archive_csvs",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")),
        )

        result = run_pipeline(date_str="2024-01-15")
        assert result["status"] == "error"
        assert fail_run_calls == [99]

    def test_fail_run_not_called_when_run_id_unknown(self, monkeypatch):
        """
        If the pipeline fails before ingest returns a run_id,
        fail_run must NOT be called (no run_id to mark).
        """
        products = [_fake_product()]
        monkeypatch.setattr("axen_price_scraper.main", lambda **kw: products)
        monkeypatch.setattr("axen_scheduler.get_connection", lambda p=None: MagicMock())
        monkeypatch.setattr("axen_scheduler.migrate", lambda c: None)
        # ingest raises immediately — run_id never assigned
        monkeypatch.setattr(
            "axen_scheduler.ingest_scrape_run",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("crash")),
        )
        fail_run_calls: list = []
        monkeypatch.setattr(
            "axen_scheduler.fail_run",
            lambda *a: fail_run_calls.append(a),
        )
        run_pipeline(date_str="2024-01-15")
        assert fail_run_calls == []

    def test_lock_released_after_successful_run(self, monkeypatch):
        """Lock must be released after a successful run so subsequent calls can proceed."""
        _patch_pipeline(monkeypatch)
        run_pipeline(date_str="2024-01-15")
        acquired = axen_scheduler._PIPELINE_LOCK.acquire(blocking=False)
        assert acquired, "Lock should be released after run"
        axen_scheduler._PIPELINE_LOCK.release()

    def test_lock_released_after_failed_run(self, monkeypatch):
        """Lock must be released even when the scraper raises."""
        def _raise(**kw):
            raise RuntimeError("scraper boom")
        monkeypatch.setattr("axen_price_scraper.main", _raise)
        run_pipeline(date_str="2024-01-15")
        acquired = axen_scheduler._PIPELINE_LOCK.acquire(blocking=False)
        assert acquired, "Lock should be released even after error"
        axen_scheduler._PIPELINE_LOCK.release()

    def test_status_skipped_when_lock_already_held(self, monkeypatch):
        """Concurrent call while lock is held → immediate 'skipped' return."""
        axen_scheduler._PIPELINE_LOCK.acquire()
        try:
            result = run_pipeline(date_str="2024-01-15")
            assert result["status"] == "skipped"
            assert result["error"] == "already running"
        finally:
            axen_scheduler._PIPELINE_LOCK.release()

    def test_stores_scraped_is_sorted_unique(self, monkeypatch):
        """stores_scraped passed to ingest must be sorted and de-duplicated."""
        products = [
            _fake_product("Beroc"),
            _fake_product("Key Design"),
            _fake_product("Beroc"),    # duplicate
        ]
        _patch_pipeline(monkeypatch, products=products)

        captured: dict = {}

        def _capture(conn, prods, stores_scraped=None):
            captured["stores"] = stores_scraped
            return 1

        monkeypatch.setattr("axen_scheduler.ingest_scrape_run", _capture)
        run_pipeline(date_str="2024-01-15")
        assert captured["stores"] == ["Beroc", "Key Design"]

    def test_archive_csvs_called_with_correct_date(self, monkeypatch):
        """archive_csvs must receive the same date_str that was passed to run_pipeline."""
        _patch_pipeline(monkeypatch)
        captured: dict = {}

        def _cap_archive(src, dest, date_str):
            captured["date_str"] = date_str
            return []

        monkeypatch.setattr("axen_scheduler.archive_csvs", _cap_archive)
        run_pipeline(date_str="2024-06-21")
        assert captured["date_str"] == "2024-06-21"

    def test_uses_brt_date_when_date_str_not_provided(self, monkeypatch):
        """When date_str is omitted, it should default to today's BRT date."""
        from datetime import datetime
        _patch_pipeline(monkeypatch)
        captured: dict = {}

        def _cap_archive(src, dest, date_str):
            captured["date_str"] = date_str
            return []

        monkeypatch.setattr("axen_scheduler.archive_csvs", _cap_archive)
        run_pipeline()  # no date_str
        expected = datetime.now(BRT).strftime("%Y-%m-%d")
        assert captured.get("date_str") == expected
