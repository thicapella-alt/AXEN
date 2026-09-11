"""
AXEN Intelligence — Database Layer
===================================
SQLite schema, versioned migrations, scrape-run ingestion, and typed query helpers.

Usage
-----
    from axen_database import get_connection, migrate, ingest_scrape_run

    conn = get_connection()          # path from DB_PATH env var (default: axen_intelligence.db)
    migrate(conn)                    # idempotent — safe to call every startup
    run_id = ingest_scrape_run(conn, products)

Design principles
-----------------
- SQL puro via sqlite3 — sem ORM.
- Migrações versionadas: PRAGMA user_version garante que cada passo é aplicado exatamente uma vez.
- ingest_scrape_run() é a única porta de escrita do scraper para o banco.
- Agentes lêem via helpers tipados e escrevem apenas em agent_recommendations.
- Todos os timestamps em UTC ISO-8601.
"""

import json
import logging
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────

_SCHEMA_VERSION = 2
DEFAULT_DB_PATH = "axen_intelligence.db"


# ─────────────────────────────────────────────
#  CONNECTION
# ─────────────────────────────────────────────

def get_connection(path: Optional[str] = None) -> sqlite3.Connection:
    """
    Opens a SQLite connection with production settings:
      - WAL journal mode  → concurrent reads during long scrape runs
      - Foreign keys ON   → referential integrity enforced
      - busy_timeout 5s   → retries on locked DB instead of raising immediately
      - Row factory       → rows behave like dicts (row["column"])

    Args:
        path: File path for the database.
              Defaults to DB_PATH env var, then "axen_intelligence.db".
              Pass ":memory:" in tests.
    """
    db_path = path or os.getenv("DB_PATH", DEFAULT_DB_PATH)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ─────────────────────────────────────────────
#  MIGRATIONS
# ─────────────────────────────────────────────

def migrate(conn: sqlite3.Connection) -> None:
    """
    Brings the database schema up to _SCHEMA_VERSION.
    Safe to call on every application startup — skips already-applied steps.

    How it works:
      - Reads PRAGMA user_version (starts at 0 for new databases).
      - Applies each missing migration step in order.
      - Each step sets PRAGMA user_version to its own version number.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    log.info("[DB] Schema: current=v%d target=v%d", current, _SCHEMA_VERSION)

    if current < 1:
        _migrate_v1(conn)
    if current < 2:
        _migrate_v2(conn)

    log.info("[DB] Schema up to date (v%d).", _SCHEMA_VERSION)


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """
    Version 1 — initial schema.
    Creates all seven tables plus their indices.
    Wrapped in a single transaction so it either fully applies or rolls back.
    """
    log.info("[DB] Applying migration v1 — creating all tables.")
    with conn:
        conn.executescript("""
            -- ── scrape_runs ────────────────────────────────────────────────────
            -- One record per full scraper execution. Anchor for all snapshots.
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at      TEXT    NOT NULL,          -- UTC ISO-8601
                finished_at     TEXT,                      -- set when status='done'/'error'
                total_products  INTEGER NOT NULL DEFAULT 0,
                stores_scraped  TEXT,                      -- JSON array of store names
                status          TEXT    NOT NULL DEFAULT 'running',  -- 'running'|'done'|'error'
                error_msg       TEXT                       -- stack trace on error
            );

            -- ── prices ─────────────────────────────────────────────────────────
            -- One row per product per run. Immutable historical record.
            CREATE TABLE IF NOT EXISTS prices (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          INTEGER NOT NULL REFERENCES scrape_runs(id),
                scraped_at      TEXT    NOT NULL,
                store           TEXT    NOT NULL,          -- 'Beroc', 'Mercado Livre', etc.
                product_name    TEXT    NOT NULL,
                price           REAL    NOT NULL,          -- R$ float
                material        TEXT    NOT NULL,          -- 'corda'|'couro'|'metal'|'pedra'|'desconhecido'
                url             TEXT    NOT NULL DEFAULT '',
                delivery_info   TEXT    NOT NULL DEFAULT '',  -- 'Frete Grátis', 'Entrega em 5 dias', ''
                discount_pct    REAL,                      -- % from ML original_price, null if no discount
                context         TEXT    NOT NULL DEFAULT '' -- raw Product.context field
            );

            CREATE INDEX IF NOT EXISTS idx_prices_run_store
                ON prices (run_id, store);
            CREATE INDEX IF NOT EXISTS idx_prices_material_date
                ON prices (material, scraped_at);
            CREATE INDEX IF NOT EXISTS idx_prices_store_url
                ON prices (store, url);

            -- ── price_changes ──────────────────────────────────────────────────
            -- Deltas computed by ingest_scrape_run() vs. the previous completed run.
            CREATE TABLE IF NOT EXISTS price_changes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          INTEGER NOT NULL REFERENCES scrape_runs(id),
                store           TEXT    NOT NULL,
                url             TEXT    NOT NULL,
                product_name    TEXT    NOT NULL,
                material        TEXT    NOT NULL,
                price_before    REAL    NOT NULL,
                price_after     REAL    NOT NULL,
                change_pct      REAL    NOT NULL,          -- (after-before)/before*100, negative = price drop
                detected_at     TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_price_changes_store_detected
                ON price_changes (store, detected_at);
            CREATE INDEX IF NOT EXISTS idx_price_changes_material_pct
                ON price_changes (material, change_pct);

            -- ── delivery_snapshots ─────────────────────────────────────────────
            -- Delivery info aggregated per store × material per run.
            -- Separated from prices because delivery is a store-level attribute.
            CREATE TABLE IF NOT EXISTS delivery_snapshots (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id              INTEGER NOT NULL REFERENCES scrape_runs(id),
                store               TEXT    NOT NULL,
                material            TEXT    NOT NULL,
                price_min           REAL,
                price_avg           REAL,
                product_count       INTEGER,
                delivery_mode       TEXT,                  -- statistical mode of delivery_info strings
                free_shipping_pct   REAL,                  -- 0–100
                recorded_at         TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_delivery_store_material
                ON delivery_snapshots (store, material, recorded_at);

            -- ── ml_positions ───────────────────────────────────────────────────
            -- Search ranking position of ML listings per scrape run.
            -- Populated for store='Mercado Livre' when context contains 'position:N'.
            CREATE TABLE IF NOT EXISTS ml_positions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          INTEGER NOT NULL REFERENCES scrape_runs(id),
                item_id         TEXT    NOT NULL,          -- 'MLB123456789'
                query           TEXT    NOT NULL DEFAULT '',  -- search term used
                material        TEXT    NOT NULL,
                position        INTEGER NOT NULL,          -- 1-based, 0 = not captured yet
                price           REAL    NOT NULL,
                product_name    TEXT    NOT NULL,
                url             TEXT    NOT NULL DEFAULT '',
                recorded_at     TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ml_positions_item_date
                ON ml_positions (item_id, recorded_at);
            CREATE INDEX IF NOT EXISTS idx_ml_positions_query_date
                ON ml_positions (query, recorded_at);

            -- ── sales ──────────────────────────────────────────────────────────
            -- Orders collected from platforms (ML, Nuvemshop).
            -- Populated by axen_sales_agent.py.
            CREATE TABLE IF NOT EXISTS sales (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                platform        TEXT    NOT NULL,          -- 'mercadolivre'|'nuvemshop'
                order_id        TEXT    NOT NULL,
                item_id         TEXT,                      -- platform SKU id
                product_name    TEXT    NOT NULL,
                material        TEXT,                      -- inferred via classify_material()
                color           TEXT,
                quantity        INTEGER NOT NULL DEFAULT 1,
                unit_price      REAL    NOT NULL,
                total_value     REAL    NOT NULL,          -- quantity * unit_price
                buyer_state     TEXT,                      -- BR state code (SP, RJ, ...)
                sold_at         TEXT    NOT NULL,          -- UTC ISO-8601
                week_label      TEXT,                      -- 'YYYY-WNN' for weekly aggregation
                ingested_at     TEXT    NOT NULL,
                UNIQUE (platform, order_id)               -- prevents duplicate ingestion
            );

            CREATE INDEX IF NOT EXISTS idx_sales_material_week
                ON sales (material, week_label);
            CREATE INDEX IF NOT EXISTS idx_sales_state_material
                ON sales (buyer_state, material);

            -- ── roas_campaigns ─────────────────────────────────────────────────
            -- ADS campaign ROAS per measurement period.
            -- Populated by axen_roas_agent.py.
            CREATE TABLE IF NOT EXISTS roas_campaigns (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                platform            TEXT    NOT NULL,
                campaign_id         TEXT    NOT NULL,
                campaign_name       TEXT,
                period_start        TEXT    NOT NULL,      -- UTC ISO-8601
                period_end          TEXT    NOT NULL,
                ad_spend            REAL    NOT NULL,      -- R$
                attributed_revenue  REAL    NOT NULL,      -- R$
                roas                REAL    NOT NULL,      -- attributed_revenue / ad_spend
                impressions         INTEGER,
                clicks              INTEGER,
                recorded_at         TEXT    NOT NULL,
                UNIQUE (platform, campaign_id, period_start)
            );

            CREATE INDEX IF NOT EXISTS idx_roas_platform_date
                ON roas_campaigns (platform, period_start);

            -- ── agent_recommendations ──────────────────────────────────────────
            -- Output of all intelligence agents. Single source of truth for the UI feed.
            CREATE TABLE IF NOT EXISTS agent_recommendations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                type            TEXT    NOT NULL,          -- 'promotion_alert'|'ads_suggestion'|'price_suggestion'
                priority        TEXT    NOT NULL DEFAULT 'medium', -- 'high'|'medium'|'low'
                material        TEXT,                      -- null = applies to all materials
                store           TEXT,                      -- null = applies to all stores
                title           TEXT    NOT NULL,          -- short label for UI card header
                body            TEXT    NOT NULL,          -- full explanatory text
                suggested_value REAL,                      -- suggested price in R$ (for price_suggestion)
                data_json       TEXT,                      -- full context as JSON string
                run_id          INTEGER REFERENCES scrape_runs(id),
                created_at      TEXT    NOT NULL,
                dismissed_at    TEXT,                      -- set when user clicks "Dispensar"
                applied_at      TEXT                       -- set when user clicks "Aplicar"
            );

            CREATE INDEX IF NOT EXISTS idx_recs_type_priority
                ON agent_recommendations (type, priority, created_at);
            CREATE INDEX IF NOT EXISTS idx_recs_material_active
                ON agent_recommendations (material, dismissed_at, applied_at);

            PRAGMA user_version = 1;
        """)
    log.info("[DB] Migration v1 applied.")


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """Version 2 — adds visits table for Nuvemshop + ML traffic history."""
    log.info("[DB] Applying migration v2 — creating visits table.")
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS visits (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                platform         TEXT    NOT NULL,          -- 'nuvemshop' | 'mercadolivre'
                source           TEXT,                      -- loja ou item_id do ML
                date             TEXT    NOT NULL,          -- YYYY-MM-DD
                visits           INTEGER DEFAULT 0,
                visitors         INTEGER,                   -- visitantes únicos (Nuvemshop)
                add_to_cart      INTEGER,                   -- adicionaram ao carrinho
                reached_checkout INTEGER,                   -- chegaram ao checkout
                purchased        INTEGER,                   -- finalizaram compra
                listing_title    TEXT,                      -- título do anúncio (ML)
                UNIQUE (platform, source, date)
            );
            CREATE INDEX IF NOT EXISTS idx_visits_platform_date
                ON visits (platform, date);
            PRAGMA user_version = 2;
        """)
    log.info("[DB] Migration v2 applied.")


# ─────────────────────────────────────────────
#  INTERNAL UTILITIES
# ─────────────────────────────────────────────

def _now_iso() -> str:
    """Current UTC time as ISO-8601 string (timezone-aware)."""
    return datetime.now(timezone.utc).isoformat()


def _parse_context(context: str) -> dict:
    """
    Extracts structured fields from Product.context.

    ML format: "id:MLB123456789 position:3 desconto:15% query:pulseira_masculina_corda"

    Returns dict with keys: item_id, position, discount_pct, query.
    Missing fields default to empty string / 0 / None.
    """
    result: dict = {"item_id": "", "position": 0, "discount_pct": None, "query": ""}
    if not context:
        return result

    m = re.search(r"\bid:(MLB\S+)", context)
    if m:
        result["item_id"] = m.group(1)

    m = re.search(r"\bposition:(\d+)", context)
    if m:
        result["position"] = int(m.group(1))

    m = re.search(r"\bdesconto:(\d+(?:\.\d+)?)%", context)
    if m:
        result["discount_pct"] = float(m.group(1))

    m = re.search(r"\bquery:(\S+)", context)
    if m:
        result["query"] = m.group(1).replace("_", " ")

    return result


def _week_label(iso_date: str) -> str:
    """
    Converts an ISO-8601 date/datetime string to ISO week label 'YYYY-WNN'.
    Returns empty string on parse failure.
    """
    try:
        # Handle both date-only and datetime strings
        dt_str = iso_date.split("T")[0] if "T" in iso_date else iso_date
        dt = datetime.strptime(dt_str, "%Y-%m-%d")
        return dt.strftime("%G-W%V")
    except (ValueError, AttributeError):
        return ""


# ─────────────────────────────────────────────
#  INGESTION
# ─────────────────────────────────────────────

def ingest_scrape_run(
    conn: sqlite3.Connection,
    products: list,
    stores_scraped: Optional[list] = None,
) -> int:
    """
    Persists one complete scraper execution to the database.

    Pipeline:
      1. Creates a scrape_run record (status='running').
      2. Inserts all products into `prices`.
      3. Inserts ML products (store='Mercado Livre') into `ml_positions`.
      4. Aggregates per store × material into `delivery_snapshots`.
      5. Computes price deltas vs. the previous completed run → `price_changes`.
      6. Updates the scrape_run record (status='done', totals).

    Args:
        conn:           Active database connection.
        products:       List of Product dataclass instances from axen_price_scraper.
        stores_scraped: Optional explicit list of store names scraped this run.
                        If None, inferred from the products list.

    Returns:
        run_id: ID of the created scrape_run record.

    Raises:
        Exception: Any unhandled DB error. Caller should call fail_run() on error.
    """
    started_at = _now_iso()

    # ── 1. Create run record ──────────────────────────────────────────────
    with conn:
        cur = conn.execute(
            "INSERT INTO scrape_runs (started_at, status, stores_scraped) VALUES (?, 'running', ?)",
            (started_at, json.dumps(stores_scraped or [])),
        )
        run_id: int = cur.lastrowid

    log.info("[DB] scrape_run %d started at %s.", run_id, started_at)

    # ── Find previous completed run for diff ──────────────────────────────
    prev_row = conn.execute(
        "SELECT id FROM scrape_runs WHERE id < ? AND status = 'done' ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    prev_run_id: Optional[int] = prev_row["id"] if prev_row else None

    # ── 2 & 3. Build rows for prices and ml_positions ─────────────────────
    scraped_at = _now_iso()
    stores_seen: set = set()
    # Group products by (store, material) for delivery_snapshots aggregation
    store_material_groups: dict = {}

    price_rows = []
    ml_position_rows = []

    for p in products:
        ctx = _parse_context(p.context or "")

        price_rows.append((
            run_id,
            scraped_at,
            p.store,
            p.name,
            float(p.price),
            p.material,
            p.url or "",
            p.delivery_info or "",
            ctx["discount_pct"],
            p.context or "",
        ))

        stores_seen.add(p.store)
        key = (p.store, p.material)
        store_material_groups.setdefault(key, []).append(p)

        # ML positions: only when item_id was captured
        if p.store == "Mercado Livre" and ctx["item_id"]:
            ml_position_rows.append((
                run_id,
                ctx["item_id"],
                ctx["query"],
                p.material,
                ctx["position"],
                float(p.price),
                p.name,
                p.url or "",
                scraped_at,
            ))

    with conn:
        conn.executemany(
            """INSERT INTO prices
               (run_id, scraped_at, store, product_name, price, material,
                url, delivery_info, discount_pct, context)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            price_rows,
        )
        if ml_position_rows:
            conn.executemany(
                """INSERT INTO ml_positions
                   (run_id, item_id, query, material, position, price,
                    product_name, url, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ml_position_rows,
            )

    log.info(
        "[DB] Inserted %d prices and %d ML positions for run %d.",
        len(price_rows), len(ml_position_rows), run_id,
    )

    # ── 4. delivery_snapshots ─────────────────────────────────────────────
    snapshot_rows = []
    for (store, material), group in store_material_groups.items():
        group_prices = [float(p.price) for p in group]
        delivery_terms = [p.delivery_info for p in group if p.delivery_info and p.delivery_info != "-"]

        price_min = min(group_prices)
        price_avg = round(sum(group_prices) / len(group_prices), 2)
        product_count = len(group)
        delivery_mode = Counter(delivery_terms).most_common(1)[0][0] if delivery_terms else ""
        free_count = sum(
            1 for t in delivery_terms
            if "grát" in t.lower() or "gratis" in t.lower()
        )
        free_pct = round(free_count / product_count * 100, 1)

        snapshot_rows.append((
            run_id, store, material,
            price_min, price_avg, product_count,
            delivery_mode, free_pct, scraped_at,
        ))

    with conn:
        conn.executemany(
            """INSERT INTO delivery_snapshots
               (run_id, store, material, price_min, price_avg, product_count,
                delivery_mode, free_shipping_pct, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            snapshot_rows,
        )

    log.info("[DB] Inserted %d delivery snapshots for run %d.", len(snapshot_rows), run_id)

    # ── 5. price_changes vs. previous run ────────────────────────────────
    n_changes = 0
    if prev_run_id is not None:
        changes = conn.execute(
            """
            SELECT
                curr.store,
                curr.url,
                curr.product_name,
                curr.material,
                prev.price                                        AS price_before,
                curr.price                                        AS price_after,
                ROUND(
                    (curr.price - prev.price) / prev.price * 100.0,
                    2
                )                                                 AS change_pct
            FROM prices AS curr
            JOIN prices AS prev
                ON  curr.store = prev.store
                AND curr.url   = prev.url
                AND curr.url  != ''
            WHERE curr.run_id = ?
              AND prev.run_id = ?
              AND ABS(curr.price - prev.price) > 0.01
            """,
            (run_id, prev_run_id),
        ).fetchall()

        if changes:
            detected_at = _now_iso()
            with conn:
                conn.executemany(
                    """INSERT INTO price_changes
                       (run_id, store, url, product_name, material,
                        price_before, price_after, change_pct, detected_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            run_id,
                            r["store"], r["url"], r["product_name"], r["material"],
                            r["price_before"], r["price_after"], r["change_pct"],
                            detected_at,
                        )
                        for r in changes
                    ],
                )
            n_changes = len(changes)

    log.info(
        "[DB] Detected %d price changes vs. run %s for run %d.",
        n_changes, prev_run_id, run_id,
    )

    # ── 6. Finalize run record ────────────────────────────────────────────
    finished_at = _now_iso()
    actual_stores = sorted(stores_seen)
    with conn:
        conn.execute(
            """UPDATE scrape_runs
               SET finished_at    = ?,
                   total_products = ?,
                   stores_scraped = ?,
                   status         = 'done'
               WHERE id = ?""",
            (finished_at, len(products), json.dumps(actual_stores), run_id),
        )

    log.info(
        "[DB] scrape_run %d done — %d products across %d stores.",
        run_id, len(products), len(actual_stores),
    )
    return run_id


def fail_run(conn: sqlite3.Connection, run_id: int, error_msg: str) -> None:
    """
    Marks a scrape_run as failed. Call this in the except block
    that wraps ingest_scrape_run() so the status is never left as 'running'.

    Args:
        conn:      Active database connection.
        run_id:    The run ID returned by the initial INSERT (may be None if
                   the INSERT itself failed — in that case, this call is a no-op).
        error_msg: Exception string or traceback (truncated to 2000 chars).
    """
    if run_id is None:
        return
    with conn:
        conn.execute(
            "UPDATE scrape_runs SET status='error', finished_at=?, error_msg=? WHERE id=?",
            (_now_iso(), str(error_msg)[:2000], run_id),
        )
    log.warning("[DB] scrape_run %d marked as error.", run_id)


# ─────────────────────────────────────────────
#  HELPERS — SCRAPE RUNS
# ─────────────────────────────────────────────

def get_latest_run_id(conn: sqlite3.Connection) -> Optional[int]:
    """Returns the id of the most recent completed scrape_run, or None."""
    row = conn.execute(
        "SELECT id FROM scrape_runs WHERE status='done' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def get_run(conn: sqlite3.Connection, run_id: int) -> Optional[dict]:
    """Returns a single scrape_run by id, or None."""
    row = conn.execute("SELECT * FROM scrape_runs WHERE id=?", (run_id,)).fetchone()
    return dict(row) if row else None


# ─────────────────────────────────────────────
#  HELPERS — PRICES
# ─────────────────────────────────────────────

def get_latest_prices(
    conn: sqlite3.Connection,
    material: Optional[str] = None,
    store: Optional[str] = None,
) -> list:
    """
    All prices from the most recent completed run, optionally filtered.

    Returns:
        List of dicts: {id, run_id, scraped_at, store, product_name, price,
                        material, url, delivery_info, discount_pct, context}
    """
    run_id = get_latest_run_id(conn)
    if run_id is None:
        return []

    sql = "SELECT * FROM prices WHERE run_id = ?"
    params: list = [run_id]

    if material:
        sql += " AND material = ?"
        params.append(material)
    if store:
        sql += " AND store = ?"
        params.append(store)

    sql += " ORDER BY store, price"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_price_history(
    conn: sqlite3.Connection,
    material: Optional[str] = None,
    days: int = 30,
) -> list:
    """
    Daily average and min price per store for the given period.
    Groups: one row per (date, store, material).

    Returns:
        List of dicts: {date, store, material, price_avg, price_min, product_count}
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = """
        SELECT
            DATE(scraped_at)     AS date,
            store,
            material,
            ROUND(AVG(price), 2) AS price_avg,
            MIN(price)           AS price_min,
            COUNT(*)             AS product_count
        FROM prices
        WHERE scraped_at >= ?
    """
    params: list = [cutoff]
    if material:
        sql += " AND material = ?"
        params.append(material)
    sql += " GROUP BY date, store, material ORDER BY date, store"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_price_changes(
    conn: sqlite3.Connection,
    days: int = 7,
    min_pct: float = 0.0,
) -> list:
    """
    Recent price changes, filtered by minimum absolute percentage change.

    Args:
        days:    How many days back to look.
        min_pct: Minimum |change_pct| to include (e.g. 5.0 = only changes ≥ 5%).

    Returns:
        List of dicts: {store, product_name, material, price_before, price_after,
                        change_pct, detected_at, url}
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [
        dict(r) for r in conn.execute(
            """SELECT store, product_name, material,
                      price_before, price_after, change_pct, detected_at, url
               FROM price_changes
               WHERE detected_at >= ?
                 AND ABS(change_pct) >= ?
               ORDER BY detected_at DESC""",
            (cutoff, min_pct),
        ).fetchall()
    ]


# ─────────────────────────────────────────────
#  HELPERS — COMPETITORS
# ─────────────────────────────────────────────

def get_delivery_summary(
    conn: sqlite3.Connection,
    material: Optional[str] = None,
) -> list:
    """
    Latest delivery snapshot per store × material (from the most recent run).

    Returns:
        List of dicts: {store, material, price_min, price_avg, product_count,
                        delivery_mode, free_shipping_pct, recorded_at}
    """
    run_id = get_latest_run_id(conn)
    if run_id is None:
        return []

    sql = "SELECT * FROM delivery_snapshots WHERE run_id = ?"
    params: list = [run_id]
    if material:
        sql += " AND material = ?"
        params.append(material)
    sql += " ORDER BY store, material"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_promotions(
    conn: sqlite3.Connection,
    days: int = 1,
) -> list:
    """
    Products with a non-null discount from the past N days.

    Returns:
        List of dicts: {store, product_name, material, price, discount_pct,
                        url, delivery_info, scraped_at}
        Ordered by discount_pct descending.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [
        dict(r) for r in conn.execute(
            """SELECT store, product_name, material, price, discount_pct,
                      url, delivery_info, scraped_at
               FROM prices
               WHERE scraped_at >= ?
                 AND discount_pct IS NOT NULL
                 AND discount_pct > 0
               ORDER BY discount_pct DESC""",
            (cutoff,),
        ).fetchall()
    ]


# ─────────────────────────────────────────────
#  HELPERS — ML POSITIONS
# ─────────────────────────────────────────────

def get_ml_positions(
    conn: sqlite3.Connection,
    query: Optional[str] = None,
    days: int = 7,
) -> list:
    """
    ML position history for the past N days, optionally filtered by search query.

    Returns:
        List of dicts: {item_id, query, material, position, price,
                        product_name, url, recorded_at}
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = """
        SELECT item_id, query, material, position, price, product_name, url, recorded_at
        FROM ml_positions
        WHERE recorded_at >= ?
    """
    params: list = [cutoff]
    if query:
        sql += " AND query = ?"
        params.append(query)
    sql += " ORDER BY recorded_at DESC, position"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_ml_position_trend(
    conn: sqlite3.Connection,
    days: int = 7,
) -> list:
    """
    Per-item position trend over the past N days.
    Used by the ML position agent to detect worsening rankings.

    Returns:
        List of dicts: {item_id, query, material, avg_position, min_position,
                        max_position, first_position, last_position, n_runs}
        Ordered by avg_position ascending (best positions first).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [
        dict(r) for r in conn.execute(
            """
            SELECT
                item_id,
                query,
                material,
                ROUND(AVG(position), 1)  AS avg_position,
                MIN(position)            AS min_position,
                MAX(position)            AS max_position,
                (SELECT position
                 FROM ml_positions AS m2
                 WHERE m2.item_id = ml.item_id
                   AND m2.query   = ml.query
                 ORDER BY recorded_at ASC
                 LIMIT 1)                AS first_position,
                (SELECT position
                 FROM ml_positions AS m3
                 WHERE m3.item_id = ml.item_id
                   AND m3.query   = ml.query
                 ORDER BY recorded_at DESC
                 LIMIT 1)                AS last_position,
                COUNT(*)                 AS n_runs
            FROM ml_positions AS ml
            WHERE recorded_at >= ?
              AND position > 0
            GROUP BY item_id, query, material
            ORDER BY avg_position
            """,
            (cutoff,),
        ).fetchall()
    ]


# ─────────────────────────────────────────────
#  HELPERS — RECOMMENDATIONS
# ─────────────────────────────────────────────

def save_recommendation(
    conn: sqlite3.Connection,
    *,
    type_: str,
    priority: str,
    title: str,
    body: str,
    material: Optional[str] = None,
    store: Optional[str] = None,
    suggested_value: Optional[float] = None,
    data_json: Optional[dict] = None,
    run_id: Optional[int] = None,
) -> int:
    """
    Inserts a new recommendation into agent_recommendations.
    Agents use this as the sole write path for their output.

    Args:
        type_:          'promotion_alert' | 'ads_suggestion' | 'price_suggestion'
        priority:       'high' | 'medium' | 'low'
        title:          Short label (≤ 80 chars) for the UI card header.
        body:           Full explanatory text for the card body.
        material:       Related material, or None for cross-material alerts.
        store:          Related competitor store, or None.
        suggested_value: Price in R$ for price_suggestion type.
        data_json:      Full context dict (serialized to JSON).
        run_id:         scrape_run that triggered this recommendation.

    Returns:
        The new recommendation id.
    """
    with conn:
        cur = conn.execute(
            """INSERT INTO agent_recommendations
               (type, priority, material, store, title, body,
                suggested_value, data_json, run_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                type_, priority, material, store, title, body,
                suggested_value,
                json.dumps(data_json) if data_json is not None else None,
                run_id,
                _now_iso(),
            ),
        )
    return cur.lastrowid


def get_recommendations(
    conn: sqlite3.Connection,
    type_: Optional[str] = None,
    priority: Optional[str] = None,
    material: Optional[str] = None,
    active_only: bool = True,
    limit: int = 100,
) -> list:
    """
    Returns recommendations ordered by priority (high→medium→low) then created_at DESC.

    Args:
        type_:       Filter by type ('promotion_alert', 'ads_suggestion', 'price_suggestion').
        priority:    Filter by priority ('high', 'medium', 'low').
        material:    Filter by material.
        active_only: If True, excludes dismissed and applied records.
        limit:       Maximum number of records to return.

    Returns:
        List of dicts with all agent_recommendations columns plus
        data_json already as a parsed dict (or None).
    """
    priority_order = "CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"
    sql = "SELECT * FROM agent_recommendations WHERE 1=1"
    params: list = []

    if active_only:
        sql += " AND dismissed_at IS NULL AND applied_at IS NULL"
    if type_:
        sql += " AND type = ?"
        params.append(type_)
    if priority:
        sql += " AND priority = ?"
        params.append(priority)
    if material:
        sql += " AND material = ?"
        params.append(material)

    sql += f" ORDER BY {priority_order}, created_at DESC LIMIT ?"
    params.append(limit)

    rows = []
    for r in conn.execute(sql, params).fetchall():
        row = dict(r)
        if row.get("data_json"):
            try:
                row["data_json"] = json.loads(row["data_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        rows.append(row)
    return rows


def get_recommendation_by_id(conn: sqlite3.Connection, rec_id: int) -> Optional[dict]:
    """Returns a single recommendation by id, or None if not found."""
    row = conn.execute(
        "SELECT * FROM agent_recommendations WHERE id=?", (rec_id,)
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    if result.get("data_json"):
        try:
            result["data_json"] = json.loads(result["data_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    return result


def dismiss_recommendation(conn: sqlite3.Connection, rec_id: int) -> bool:
    """
    Marks a recommendation as dismissed (sets dismissed_at).
    No-op if already dismissed.

    Returns:
        True if the recommendation exists and was (or already is) dismissed.
        False if the id does not exist.
    """
    with conn:
        conn.execute(
            """UPDATE agent_recommendations
               SET dismissed_at = ?
               WHERE id = ? AND dismissed_at IS NULL""",
            (_now_iso(), rec_id),
        )
    row = conn.execute(
        "SELECT dismissed_at FROM agent_recommendations WHERE id=?", (rec_id,)
    ).fetchone()
    return row is not None and row["dismissed_at"] is not None


def apply_recommendation(conn: sqlite3.Connection, rec_id: int) -> bool:
    """
    Marks a recommendation as applied (sets applied_at).
    No-op if already applied.

    Returns:
        True if the recommendation exists and was (or already is) applied.
        False if the id does not exist.
    """
    with conn:
        conn.execute(
            """UPDATE agent_recommendations
               SET applied_at = ?
               WHERE id = ? AND applied_at IS NULL""",
            (_now_iso(), rec_id),
        )
    row = conn.execute(
        "SELECT applied_at FROM agent_recommendations WHERE id=?", (rec_id,)
    ).fetchone()
    return row is not None and row["applied_at"] is not None


# ─────────────────────────────────────────────
#  HELPERS — SALES
# ─────────────────────────────────────────────

def upsert_sale(conn: sqlite3.Connection, sale: dict) -> int:
    """
    Inserts a sale record. Silently skips if (platform, order_id) already exists
    (INSERT OR IGNORE), preventing duplicate ingestion on re-runs.

    Required keys: platform, order_id, product_name, unit_price, total_value, sold_at.
    Optional keys: item_id, material, color, quantity, buyer_state.

    Returns:
        Row id of the new or existing record.
    """
    week = _week_label(sale.get("sold_at", ""))
    with conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO sales
               (platform, order_id, item_id, product_name, material, color,
                quantity, unit_price, total_value, buyer_state, sold_at,
                week_label, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sale.get("platform"),
                sale.get("order_id"),
                sale.get("item_id"),
                sale.get("product_name"),
                sale.get("material"),
                sale.get("color"),
                int(sale.get("quantity", 1)),
                float(sale.get("unit_price", 0)),
                float(sale.get("total_value", 0)),
                sale.get("buyer_state"),
                sale.get("sold_at"),
                week,
                _now_iso(),
            ),
        )
    if cur.lastrowid:
        return cur.lastrowid
    # Record already existed — return its id
    row = conn.execute(
        "SELECT id FROM sales WHERE platform=? AND order_id=?",
        (sale.get("platform"), sale.get("order_id")),
    ).fetchone()
    return row["id"] if row else -1


def get_top_products(
    conn: sqlite3.Connection,
    days: int = 30,
    limit: int = 5,
) -> list:
    """
    Top products by units sold in the past N days.

    Returns:
        List of dicts: {product_name, material, total_units, total_revenue}
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [
        dict(r) for r in conn.execute(
            """SELECT product_name, material,
                      SUM(quantity)              AS total_units,
                      ROUND(SUM(total_value), 2) AS total_revenue
               FROM sales
               WHERE sold_at >= ?
               GROUP BY product_name, material
               ORDER BY total_units DESC
               LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
    ]


def get_sales_by_state(
    conn: sqlite3.Connection,
    days: int = 30,
    material: Optional[str] = None,
) -> list:
    """
    Sales aggregated by buyer state for the past N days.

    Returns:
        List of dicts: {buyer_state, material, total_units, total_revenue}
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = """
        SELECT buyer_state, material,
               SUM(quantity)              AS total_units,
               ROUND(SUM(total_value), 2) AS total_revenue
        FROM sales
        WHERE sold_at >= ?
    """
    params: list = [cutoff]
    if material:
        sql += " AND material = ?"
        params.append(material)
    sql += " GROUP BY buyer_state, material ORDER BY total_units DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_weekly_sales(
    conn: sqlite3.Connection,
    weeks: int = 8,
) -> list:
    """
    Revenue and units aggregated by ISO week for the past N weeks.

    Returns:
        List of dicts: {week_label, total_units, total_revenue}
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(weeks=weeks)).isoformat()
    return [
        dict(r) for r in conn.execute(
            """SELECT week_label,
                      SUM(quantity)              AS total_units,
                      ROUND(SUM(total_value), 2) AS total_revenue
               FROM sales
               WHERE sold_at >= ?
                 AND week_label IS NOT NULL
               GROUP BY week_label
               ORDER BY week_label""",
            (cutoff,),
        ).fetchall()
    ]


# ─────────────────────────────────────────────
#  HELPERS — ROAS
# ─────────────────────────────────────────────

def upsert_roas_campaign(conn: sqlite3.Connection, campaign: dict) -> int:
    """
    Inserts or replaces a ROAS campaign record.
    ROAS is calculated automatically from ad_spend and attributed_revenue.

    Required keys: platform, campaign_id, period_start, period_end,
                   ad_spend, attributed_revenue.
    Optional keys: campaign_name, impressions, clicks.

    Returns:
        Row id of the inserted/replaced record.
    """
    ad_spend = float(campaign.get("ad_spend") or 0)
    revenue = float(campaign.get("attributed_revenue") or 0)
    roas = round(revenue / ad_spend, 4) if ad_spend > 0 else 0.0

    with conn:
        cur = conn.execute(
            """INSERT OR REPLACE INTO roas_campaigns
               (platform, campaign_id, campaign_name,
                period_start, period_end,
                ad_spend, attributed_revenue, roas,
                impressions, clicks, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                campaign.get("platform"),
                campaign.get("campaign_id"),
                campaign.get("campaign_name"),
                campaign.get("period_start"),
                campaign.get("period_end"),
                ad_spend,
                revenue,
                roas,
                campaign.get("impressions"),
                campaign.get("clicks"),
                _now_iso(),
            ),
        )
    return cur.lastrowid


def get_roas_campaigns(
    conn: sqlite3.Connection,
    days: int = 30,
) -> list:
    """
    All ROAS campaign records for the given period.

    Returns:
        List of dicts with all roas_campaigns columns.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [
        dict(r) for r in conn.execute(
            "SELECT * FROM roas_campaigns WHERE period_start >= ? ORDER BY period_start DESC",
            (cutoff,),
        ).fetchall()
    ]


def upsert_visit(conn: sqlite3.Connection, visit: dict) -> None:
    """
    Inserts or updates a daily visit record.
    UNIQUE key: (platform, source, date) — re-running sync overwrites with latest data.

    Required keys: platform, source, date, visits.
    Optional keys: visitors, add_to_cart, reached_checkout, purchased, listing_title.
    """
    with conn:
        conn.execute(
            """INSERT INTO visits
               (platform, source, date, visits, visitors,
                add_to_cart, reached_checkout, purchased, listing_title)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(platform, source, date) DO UPDATE SET
                   visits           = excluded.visits,
                   visitors         = excluded.visitors,
                   add_to_cart      = excluded.add_to_cart,
                   reached_checkout = excluded.reached_checkout,
                   purchased        = excluded.purchased,
                   listing_title    = excluded.listing_title""",
            (
                visit.get("platform"),
                visit.get("source"),
                visit.get("date"),
                int(visit.get("visits") or 0),
                visit.get("visitors"),
                visit.get("add_to_cart"),
                visit.get("reached_checkout"),
                visit.get("purchased"),
                visit.get("listing_title"),
            ),
        )


def get_roas_summary(
    conn: sqlite3.Connection,
    days: int = 30,
) -> dict:
    """
    Aggregate ROAS metrics for the given period.

    Returns:
        Dict: {avg_roas, total_spend, total_revenue, campaign_count, best_campaign}
        best_campaign is None if no records exist.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    row = conn.execute(
        """SELECT
               ROUND(AVG(roas), 2)               AS avg_roas,
               ROUND(SUM(ad_spend), 2)           AS total_spend,
               ROUND(SUM(attributed_revenue), 2) AS total_revenue,
               COUNT(DISTINCT campaign_id)       AS campaign_count
           FROM roas_campaigns
           WHERE period_start >= ?""",
        (cutoff,),
    ).fetchone()

    best = conn.execute(
        """SELECT campaign_name, ROUND(roas, 2) AS roas
           FROM roas_campaigns
           WHERE period_start >= ?
           ORDER BY roas DESC LIMIT 1""",
        (cutoff,),
    ).fetchone()

    result = dict(row) if row else {
        "avg_roas": None,
        "total_spend": 0.0,
        "total_revenue": 0.0,
        "campaign_count": 0,
    }
    result["best_campaign"] = dict(best) if best else None
    return result
