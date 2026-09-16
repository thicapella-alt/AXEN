"""
axen_scheduler.py — AXEN price intelligence pipeline runner.

Modes
─────
  python axen_scheduler.py              # daemon: fires at 07:00/13:00/21:00 BRT every day
  python axen_scheduler.py --run-now   # one-shot run, then exit
  python axen_scheduler.py --status    # show last run info + next scheduled time

Pipeline (one cycle)
─────────────────────
  1. axen_price_scraper.main(persist_db=False)  → list[Product]
  2. get_connection() + migrate()
  3. ingest_scrape_run()                         → run_id
  4. archive CSVs to data/YYYY-MM-DD/
  5. update data/latest symlink / junction
  6. rotate old log files (keep last 30)

On any exception in steps 2–4: fail_run(conn, run_id, error_msg)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import signal
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import zoneinfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from axen_database import (
    fail_run,
    get_connection,
    get_latest_run_id,
    get_run,
    ingest_scrape_run,
    migrate,
)

# ─────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────

BRT = zoneinfo.ZoneInfo("America/Sao_Paulo")
PROJECT_ROOT = Path(__file__).parent
LOGS_DIR = PROJECT_ROOT / "logs"
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = str(PROJECT_ROOT / "axen.db")
LOG_KEEP = 30
SCRAPE_HOURS = (7, 13, 21)  # 07:00 / 13:00 / 21:00 BRT — item 0.7 (Fase 0)
_SCRAPE_HOURS_CRON = ",".join(str(h) for h in SCRAPE_HOURS)  # "7,13,21" for CronTrigger

# ─────────────────────────────────────────────
#  RE-ENTRANCY GUARD
#  Prevents overlapping pipeline runs when the scheduler fires again
#  while a previous cycle is still executing.
# ─────────────────────────────────────────────

_PIPELINE_LOCK = threading.Lock()


# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────

def setup_logging(logs_dir: Path = LOGS_DIR) -> logging.Logger:
    """
    Configure the axen.scheduler logger with:
      - A dated file handler  → logs/YYYY-MM-DD.log  (BRT date, append)
      - A stderr console handler (INFO+)

    Returns the configured logger.
    Safe to call multiple times — handlers are added only once.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(BRT).strftime("%Y-%m-%d")
    log_path = logs_dir / f"{date_str}.log"

    logger = logging.getLogger("axen.scheduler")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # do not bubble up to root logger

    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s  [%(levelname)-8s]  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh = logging.FileHandler(log_path, encoding="utf-8", mode="a")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    return logger


# ─────────────────────────────────────────────
#  UTILITY FUNCTIONS  (all independently testable)
# ─────────────────────────────────────────────

def rotate_logs(logs_dir: Path, keep: int = LOG_KEEP) -> int:
    """
    Delete the oldest *.log files in logs_dir so that at most `keep` remain.
    Files are sorted lexicographically by name — ISO-date names sort correctly.

    Returns the count of files deleted.
    Non-existent directory → returns 0 (no error).
    Unlink failures are silently ignored (permissions, locked file on Windows).
    """
    if not logs_dir.exists():
        return 0
    log_files = sorted(logs_dir.glob("*.log"), key=lambda p: p.name)
    excess = max(0, len(log_files) - keep)
    deleted = 0
    for lf in log_files[:excess]:
        try:
            lf.unlink()
            deleted += 1
        except OSError:
            pass
    return deleted


def archive_csvs(
    src_dir: Path,
    dest_dir: Path,
    date_str: str,
) -> list[str]:
    """
    Copy this cycle's output CSVs from src_dir into dest_dir with dated names.

    Mapping:
      axen_competitive_prices.csv     → axen_prices_<date>.csv
      axen_delivery_detail.csv        → axen_delivery_detail_<date>.csv
      axen_competitive_summary.csv    → axen_summary_<date>.csv
      axen_competitive_delivery.csv   → axen_delivery_<date>.csv
      axen_competitive_promotions.csv → axen_promotions_<date>.csv  (optional)

    Missing source files are silently skipped.
    dest_dir is created if it does not exist.

    Returns a list of destination filenames that were actually written.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {
        "axen_competitive_prices.csv": f"axen_prices_{date_str}.csv",
        "axen_delivery_detail.csv": f"axen_delivery_detail_{date_str}.csv",
        "axen_competitive_summary.csv": f"axen_summary_{date_str}.csv",
        "axen_competitive_delivery.csv": f"axen_delivery_{date_str}.csv",
        "axen_competitive_promotions.csv": f"axen_promotions_{date_str}.csv",
    }
    created: list[str] = []
    for src_name, dest_name in mapping.items():
        src = src_dir / src_name
        if src.exists():
            shutil.copy2(src, dest_dir / dest_name)
            created.append(dest_name)
    return created


def update_latest_link(data_dir: Path, dated_dir: Path) -> bool:
    """
    Point data/latest at dated_dir.

    - Windows: creates a directory junction via  cmd /c mklink /J
      (no admin rights required when Developer Mode is ON or UAC allows it)
    - Linux/macOS: creates a directory symlink

    Returns True on success, False if the operation could not be completed.
    Refuses to remove data/latest if it is a real (non-linked) directory.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    latest = data_dir / "latest"

    # Remove any existing link / junction  (but never a real directory)
    if latest.exists() or latest.is_symlink():
        if latest.is_dir() and not latest.is_symlink():
            return False  # real directory — refuse to overwrite
        try:
            if sys.platform == "win32":
                # is_symlink() covers junctions in Python 3.12+; os.rmdir for older
                try:
                    latest.unlink()
                except (OSError, PermissionError):
                    os.rmdir(str(latest))  # junctions respond to rmdir
            else:
                latest.unlink()
        except OSError:
            return False

    try:
        if sys.platform == "win32":
            import subprocess
            res = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(latest), str(dated_dir)],
                capture_output=True,
                text=True,
            )
            return res.returncode == 0
        else:
            latest.symlink_to(dated_dir)
            return True
    except OSError:
        return False


def _next_run_time() -> datetime:
    """Return the next wall-clock scheduled time (one of SCRAPE_HOURS, BRT)
    from the current moment — today if an hour is still ahead, otherwise
    the earliest hour tomorrow."""
    now = datetime.now(BRT)
    todays_candidates = [
        now.replace(hour=h, minute=0, second=0, microsecond=0) for h in SCRAPE_HOURS
    ]
    upcoming_today = [c for c in todays_candidates if c > now]
    if upcoming_today:
        return min(upcoming_today)
    tomorrow = now + timedelta(days=1)
    return tomorrow.replace(hour=min(SCRAPE_HOURS), minute=0, second=0, microsecond=0)


# ─────────────────────────────────────────────
#  CORE PIPELINE
# ─────────────────────────────────────────────

def run_pipeline(date_str: Optional[str] = None) -> dict:
    """
    Execute one full scrape → persist → archive cycle.

    Thread-safety
    -------------
    Protected by _PIPELINE_LOCK (non-blocking acquire).  If another thread
    already holds the lock the function returns immediately with
    status="skipped" — no side-effects.

    Return value
    ------------
    A dict with keys:
        status         : "done" | "empty" | "skipped" | "error"
        run_id         : int | None
        products_count : int
        error          : str | None
    """
    log = logging.getLogger("axen.scheduler")

    if not _PIPELINE_LOCK.acquire(blocking=False):
        log.warning("Pipeline já em execução — esta invocação será ignorada.")
        return {
            "status": "skipped",
            "run_id": None,
            "products_count": 0,
            "error": "already running",
        }

    result: dict = {
        "status": "error",
        "run_id": None,
        "products_count": 0,
        "error": None,
    }
    conn = None
    run_id: Optional[int] = None

    try:
        if date_str is None:
            date_str = datetime.now(BRT).strftime("%Y-%m-%d")

        log.info("══ Pipeline AXEN iniciada — %s ══", date_str)

        # ── [1/4] Scrape ─────────────────────────────────────────────────
        log.info("[1/4] Raspagem de preços...")
        from axen_price_scraper import main as _scraper_main  # lazy import (heavy deps)
        products = _scraper_main(persist_db=False) or []
        log.info("[1/4] %d produto(s) coletado(s).", len(products))

        if not products:
            log.warning("Nenhum produto coletado — pipeline encerrada sem persistência.")
            result["status"] = "empty"
            return result

        # ── [2/4] DB connection + migration ──────────────────────────────
        log.info("[2/4] Conectando ao banco e aplicando migrations...")
        conn = get_connection(DB_PATH)
        migrate(conn)

        # ── [3/4] Ingest ──────────────────────────────────────────────────
        stores_scraped = sorted({p.store for p in products})
        log.info(
            "[3/4] Persistindo %d produto(s) de %d loja(s): %s",
            len(products),
            len(stores_scraped),
            stores_scraped,
        )
        run_id = ingest_scrape_run(conn, products, stores_scraped=stores_scraped)
        log.info("[3/4] run_id=%d — OK.", run_id)
        result["run_id"] = run_id
        result["products_count"] = len(products)

        # ── [4/4] Archive CSVs ────────────────────────────────────────────
        log.info("[4/4] Arquivando CSVs em data/%s/...", date_str)
        dated_dir = DATA_DIR / date_str
        archived = archive_csvs(PROJECT_ROOT, dated_dir, date_str)
        if archived:
            log.info("[4/4] %d arquivo(s) arquivado(s): %s", len(archived), archived)
            if update_latest_link(DATA_DIR, dated_dir):
                log.info("[4/4] data/latest → %s", dated_dir.name)
            else:
                log.warning("[4/4] Não foi possível atualizar data/latest (permissão?).")
        else:
            log.warning("[4/4] Nenhum CSV encontrado para arquivar (scraper ainda não concluído?).")

        # ── Log rotation ──────────────────────────────────────────────────
        deleted = rotate_logs(LOGS_DIR)
        if deleted:
            log.info("Rotação de logs: %d arquivo(s) antigo(s) removido(s).", deleted)

        result["status"] = "done"
        log.info(
            "══ Pipeline concluída — run_id=%d, %d produto(s) ══",
            run_id,
            len(products),
        )

    except Exception as exc:
        log.exception("Pipeline falhou com exceção inesperada: %s", exc)
        result["error"] = str(exc)
        if conn is not None and run_id is not None:
            try:
                fail_run(conn, run_id, str(exc))
                log.info("run_id=%d marcado como 'error' no banco.", run_id)
            except Exception as fe:
                log.warning("Não foi possível registrar fail_run: %s", fe)

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _PIPELINE_LOCK.release()

    return result


# ─────────────────────────────────────────────
#  ASYNC WRAPPER  (for APScheduler)
# ─────────────────────────────────────────────

async def _pipeline_task(date_str: Optional[str] = None) -> None:
    """
    APScheduler-compatible coroutine.
    Runs the synchronous run_pipeline() in a thread-pool executor so it does
    not block the event loop.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_pipeline, date_str)


# ─────────────────────────────────────────────
#  CLI COMMANDS
# ─────────────────────────────────────────────

def cmd_status() -> None:
    """Print the last completed run info (from DB) and the next scheduled time."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    try:
        conn = get_connection(DB_PATH)
        migrate(conn)
        run_id = get_latest_run_id(conn)
        if run_id is None:
            console.print("[yellow]Nenhuma execução encontrada no banco de dados.[/]")
        else:
            run = get_run(conn, run_id)
            if run:
                tbl = Table(
                    title="Última Execução AXEN",
                    show_header=True,
                    header_style="bold magenta",
                )
                tbl.add_column("Campo", style="bold cyan", no_wrap=True)
                tbl.add_column("Valor")
                tbl.add_row("run_id", str(run.get("id", "—")))
                tbl.add_row("status", run.get("status", "—"))
                tbl.add_row("iniciada em", run.get("started_at", "—") or "—")
                tbl.add_row("concluída em", run.get("finished_at", "—") or "—")
                tbl.add_row("lojas", run.get("stores_scraped", "—") or "—")
                tbl.add_row("produtos", str(run.get("product_count", "—")))
                tbl.add_row("erro", run.get("error_message", "") or "—")
                console.print(tbl)
        conn.close()
    except Exception as exc:
        console.print(f"[red]Erro ao consultar o banco de dados: {exc}[/]")

    next_run = _next_run_time()
    console.print(
        f"\n[bold]Próxima execução agendada:[/] "
        f"[green]{next_run.strftime('%Y-%m-%d %H:%M')} BRT[/]"
    )


def cmd_run_now() -> None:
    """Execute one full pipeline cycle immediately and exit."""
    log = setup_logging()
    log.info("Execução manual iniciada (--run-now).")
    result = run_pipeline()
    if result["status"] == "done":
        log.info(
            "--run-now concluída: run_id=%s, %d produto(s).",
            result["run_id"],
            result["products_count"],
        )
    elif result["status"] == "empty":
        log.warning("--run-now: nenhum produto coletado — nada foi persistido.")
    elif result["status"] == "skipped":
        log.warning("--run-now: pipeline já em execução, chamada ignorada.")
    else:
        log.error("--run-now falhou: %s", result.get("error", "desconhecido"))
        sys.exit(1)


def cmd_daemon() -> None:
    """
    Start the APScheduler daemon.

    - Fires run_pipeline() 3x/day (07:00, 13:00, 21:00 BRT) via a single
      CronTrigger with a comma-separated hour list — one daemon process,
      one systemd service, one _PIPELINE_LOCK. This was chosen over three
      separate systemd timers because _PIPELINE_LOCK (the re-entrancy
      guard against overlapping runs) is an in-process threading.Lock: it
      only protects against overlap when every run shares one process.
      Three independent timer-launched one-shot processes would each get
      their own lock and could run concurrently if one cycle overruns
      into the next slot — exactly the failure mode the lock exists to
      prevent. A single daemon with a richer cron expression avoids that
      risk for free and keeps one log stream / one thing to restart.
    - Blocks until SIGINT or SIGTERM is received.
    - On shutdown, waits up to 60 s for an in-progress pipeline to finish
      before forcing exit.
    """
    log = setup_logging()
    log.info(
        "Daemon AXEN iniciado — pipeline 3x/dia às %s BRT.",
        ", ".join(f"{h:02d}:00" for h in SCRAPE_HOURS),
    )

    _stop_event = threading.Event()

    def _handle_signal(signum: int, frame) -> None:  # noqa: ANN001
        log.info(
            "Sinal %d recebido — aguardando pipeline em curso (máx. 60 s)...",
            signum,
        )
        _stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    async def _event_loop() -> None:
        scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")
        scheduler.add_job(
            _pipeline_task,
            CronTrigger(hour=_SCRAPE_HOURS_CRON, minute=0, timezone="America/Sao_Paulo"),
            id="daily_scrape",
            name="AXEN Daily Scrape (3x/dia)",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        scheduler.start()
        log.info(
            "Scheduler ativo. Próxima execução: %s",
            _next_run_time().strftime("%Y-%m-%d %H:%M BRT"),
        )

        try:
            while not _stop_event.is_set():
                await asyncio.sleep(1)
        finally:
            scheduler.shutdown(wait=False)
            # Wait for any in-progress pipeline (held via lock) up to 60 s.
            acquired = _PIPELINE_LOCK.acquire(timeout=60)
            if acquired:
                _PIPELINE_LOCK.release()
            else:
                log.warning(
                    "Pipeline ainda em execução após 60 s de espera — forçando encerramento."
                )
            log.info("Daemon encerrado.")

    asyncio.run(_event_loop())


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AXEN Scheduler — pipeline de raspagem diária de preços.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  python axen_scheduler.py            # daemon (07:00 BRT)\n"
            "  python axen_scheduler.py --run-now  # executa agora e sai\n"
            "  python axen_scheduler.py --status   # mostra última execução\n"
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--run-now",
        action="store_true",
        help="Executa o pipeline uma vez imediatamente e encerra.",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Exibe a última execução no banco e o próximo horário agendado.",
    )
    args = parser.parse_args()

    if args.status:
        cmd_status()
    elif args.run_now:
        cmd_run_now()
    else:
        cmd_daemon()


if __name__ == "__main__":
    main()
