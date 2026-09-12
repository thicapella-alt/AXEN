"""
tests/test_backup_axen_db.py — regressão para scripts/backup_axen_db.py.

Usa bancos SQLite reais em tmp_path (leve, sem mocks) — backup_database()
usa a API de backup online do próprio sqlite3, então vale testar contra
um arquivo de verdade em vez de mockar sqlite3.connect.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backup_axen_db import backup_database, rotate_backups


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('hello')")
    conn.commit()
    conn.close()


class TestBackupDatabase:
    def test_creates_backup_file(self, tmp_path):
        db_path = tmp_path / "axen.db"
        _make_db(db_path)
        backup_dir = tmp_path / "backups"

        dest = backup_database(db_path, backup_dir, "20260915")

        assert dest == backup_dir / "axen_db_20260915.db"
        assert dest.exists()

    def test_backup_contains_same_data(self, tmp_path):
        db_path = tmp_path / "axen.db"
        _make_db(db_path)
        backup_dir = tmp_path / "backups"

        dest = backup_database(db_path, backup_dir, "20260915")

        conn = sqlite3.connect(str(dest))
        row = conn.execute("SELECT v FROM t WHERE id=1").fetchone()
        conn.close()
        assert row == ("hello",)

    def test_missing_db_raises(self, tmp_path):
        import pytest

        with pytest.raises(FileNotFoundError):
            backup_database(tmp_path / "nope.db", tmp_path / "backups", "20260915")

    def test_rerun_same_day_overwrites(self, tmp_path):
        db_path = tmp_path / "axen.db"
        _make_db(db_path)
        backup_dir = tmp_path / "backups"

        backup_database(db_path, backup_dir, "20260915")

        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO t (v) VALUES ('world')")
        conn.commit()
        conn.close()

        dest = backup_database(db_path, backup_dir, "20260915")
        conn = sqlite3.connect(str(dest))
        count = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        conn.close()
        assert count == 2  # backup atualizado, não um segundo arquivo

    def test_dry_run_does_not_write(self, tmp_path):
        db_path = tmp_path / "axen.db"
        _make_db(db_path)
        backup_dir = tmp_path / "backups"

        dest = backup_database(db_path, backup_dir, "20260915", dry_run=True)
        assert not dest.exists()


class TestRotateBackups:
    def _touch_backup(self, backup_dir: Path, date_str: str) -> Path:
        backup_dir.mkdir(parents=True, exist_ok=True)
        p = backup_dir / f"axen_db_{date_str}.db"
        p.write_bytes(b"fake")
        return p

    def test_missing_dir_returns_zero(self, tmp_path):
        assert rotate_backups(tmp_path / "nope") == 0

    def test_keeps_recent_deletes_old(self, tmp_path):
        backup_dir = tmp_path / "backups"
        recent = self._touch_backup(backup_dir, "20260910")
        old = self._touch_backup(backup_dir, "20260101")
        today = datetime(2026, 9, 15)

        deleted = rotate_backups(backup_dir, keep_days=30, today=today)

        assert deleted == 1
        assert recent.exists()
        assert not old.exists()

    def test_exactly_at_cutoff_is_kept(self, tmp_path):
        """keep_days=30 a partir de 2026-09-15 -> cutoff = 2026-08-16;
        um backup exatamente no cutoff não deve ser apagado (< cutoff, não <=)."""
        backup_dir = tmp_path / "backups"
        at_cutoff = self._touch_backup(backup_dir, "20260816")
        today = datetime(2026, 9, 15)

        deleted = rotate_backups(backup_dir, keep_days=30, today=today)

        assert deleted == 0
        assert at_cutoff.exists()

    def test_ignores_files_not_matching_pattern(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        stray = backup_dir / "notes.txt"
        stray.write_text("não mexer")
        today = datetime(2026, 9, 15)

        deleted = rotate_backups(backup_dir, keep_days=30, today=today)

        assert deleted == 0
        assert stray.exists()

    def test_dry_run_does_not_delete(self, tmp_path):
        backup_dir = tmp_path / "backups"
        old = self._touch_backup(backup_dir, "20260101")
        today = datetime(2026, 9, 15)

        deleted = rotate_backups(backup_dir, keep_days=30, today=today, dry_run=True)

        assert deleted == 1  # reported as "would delete"
        assert old.exists()  # but not actually removed

    def test_custom_keep_days(self, tmp_path):
        backup_dir = tmp_path / "backups"
        b = self._touch_backup(backup_dir, "20260901")
        today = datetime(2026, 9, 15)

        # 14 dias atrás (2026-09-01) ainda dentro de keep_days=20
        assert rotate_backups(backup_dir, keep_days=20, today=today) == 0
        assert b.exists()
