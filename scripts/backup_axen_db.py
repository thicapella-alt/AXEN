#!/usr/bin/env python3
"""
scripts/backup_axen_db.py — Backup diário do axen.db, com rotação (item 0.7).

Uso:
    cd /var/www/axen && source venv/bin/activate
    python scripts/backup_axen_db.py                  # backup + rotação (padrão: mantém 30 dias)
    python scripts/backup_axen_db.py --keep-days 45    # rotação com outra janela
    python scripts/backup_axen_db.py --dry-run         # mostra o que faria, sem gravar/apagar nada

Como funciona
─────────────
  - Usa sqlite3.Connection.backup() (API "online backup" do próprio
    sqlite3) em vez de copiar o arquivo com cp/shutil — isso evita um
    backup corrompido caso haja uma escrita em andamento no axen.db no
    momento do backup (o banco roda em WAL, então um `cp` bruto pode
    pegar o arquivo principal sem o WAL correspondente).
  - Grava em ~/backups/axen_db_YYYYMMDD.db (um por dia — reexecuções no
    mesmo dia sobrescrevem o backup do dia, não acumulam).
  - Rotação: apaga backups com mais de `--keep-days` dias (padrão 30),
    baseado no nome do arquivo (YYYYMMDD), não na data de modificação —
    assim funciona mesmo se os arquivos forem copiados/restaurados.

Não sobe para o Google Drive nem qualquer storage externo — isso é
bônus, não bloqueia o item 0.7 (ver prompt de abertura da sessão).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import zoneinfo

BRT = zoneinfo.ZoneInfo("America/Sao_Paulo")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "axen.db"
DEFAULT_BACKUP_DIR = Path.home() / "backups"
DEFAULT_KEEP_DAYS = 30

_BACKUP_NAME_RE = re.compile(r"^axen_db_(\d{8})\.db$")

log = logging.getLogger("axen.backup")


def backup_database(db_path: Path, backup_dir: Path, date_str: str, dry_run: bool = False) -> Path:
    """
    Create (or overwrite) today's backup at backup_dir/axen_db_<date_str>.db
    using SQLite's online backup API. Returns the destination path.

    Raises FileNotFoundError if db_path does not exist.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Banco não encontrado: {db_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    dest_path = backup_dir / f"axen_db_{date_str}.db"

    if dry_run:
        log.info("[dry-run] backup de %s -> %s", db_path, dest_path)
        return dest_path

    src_conn = sqlite3.connect(str(db_path))
    try:
        # Overwrite any partial/stale backup from a previous run today.
        if dest_path.exists():
            dest_path.unlink()
        dest_conn = sqlite3.connect(str(dest_path))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()

    log.info("Backup gravado: %s (%d bytes)", dest_path, dest_path.stat().st_size)
    return dest_path


def rotate_backups(backup_dir: Path, keep_days: int = DEFAULT_KEEP_DAYS, today: datetime | None = None, dry_run: bool = False) -> int:
    """
    Delete axen_db_YYYYMMDD.db files older than `keep_days` days (by the
    date encoded in the filename, not mtime).

    Returns the count of files deleted (or that would be deleted, in dry-run).
    Non-existent backup_dir -> returns 0. Filenames that don't match the
    expected pattern are left untouched (never guess-delete).
    """
    if not backup_dir.exists():
        return 0

    today = today or datetime.now(BRT)
    cutoff = today.date() - timedelta(days=keep_days)

    deleted = 0
    for f in sorted(backup_dir.glob("axen_db_*.db")):
        m = _BACKUP_NAME_RE.match(f.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            if dry_run:
                log.info("[dry-run] removeria backup antigo: %s (%s)", f.name, file_date)
            else:
                try:
                    f.unlink()
                    log.info("Backup antigo removido: %s (%s)", f.name, file_date)
                except OSError as exc:
                    log.warning("Falha ao remover %s: %s", f.name, exc)
                    continue
            deleted += 1
    return deleted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Caminho do axen.db")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR), help="Diretório de backups")
    parser.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS, help="Dias de retenção (padrão: 30)")
    parser.add_argument("--dry-run", action="store_true", help="Mostra o que faria, sem gravar/apagar nada")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    db_path = Path(args.db_path)
    backup_dir = Path(args.backup_dir)
    date_str = datetime.now(BRT).strftime("%Y%m%d")

    try:
        backup_database(db_path, backup_dir, date_str, dry_run=args.dry_run)
    except FileNotFoundError as exc:
        log.error(str(exc))
        return 1

    deleted = rotate_backups(backup_dir, keep_days=args.keep_days, dry_run=args.dry_run)
    if deleted:
        action = "seriam removidos" if args.dry_run else "removidos"
        log.info("%d backup(s) antigo(s) %s.", deleted, action)

    return 0


if __name__ == "__main__":
    sys.exit(main())
