"""
Shared pytest fixtures for the AXEN Intelligence test suite.

All tests that touch the database use the `db` fixture, which provides
a fresh in-memory SQLite connection with the schema already applied.
No test writes to disk.
"""

import logging
import sys
import os
from dataclasses import dataclass, field
from typing import Optional

import pytest

# ─────────────────────────────────────────────
#  STDOUT GUARD — must run before any scraper import
#
#  axen_price_scraper.py calls logging.basicConfig() at module level with a
#  handler list that includes TextIOWrapper(sys.stdout.buffer, ...).
#  Even though basicConfig() is a no-op when root logger already has handlers,
#  the handler list is **evaluated** before the call. The resulting TextIOWrapper
#  is then GC'd immediately (unused), and TextIOWrapper.__del__ calls close() on
#  sys.stdout.buffer — closing the underlying file that pytest is writing to.
#
#  Fix: replace sys.stdout.buffer with a non-closeable proxy BEFORE the scraper
#  is imported. When the orphaned TextIOWrapper is GC'd, its close() calls our
#  proxy's close() which is a no-op, leaving the real buffer open and usable.
# ─────────────────────────────────────────────

import io as _io


class _UnclosableBuf:
    """Binary IO proxy that ignores close() — protects the real buffer from GC'd TextIOWrappers."""
    def __init__(self, buf):
        self._buf = buf

    def close(self):
        pass   # intentional no-op: do NOT close the real buffer

    def write(self, b):
        return self._buf.write(b)

    def flush(self):
        return self._buf.flush()

    def __getattr__(self, name):
        return getattr(self._buf, name)


if hasattr(sys.stdout, "buffer"):
    sys.stdout = _io.TextIOWrapper(
        _UnclosableBuf(sys.stdout.buffer),
        encoding=getattr(sys.stdout, "encoding", "utf-8") or "utf-8",
        errors="replace",
        line_buffering=True,
    )

# Secondary guard: if root logger is still handler-free, basicConfig is a no-op.
logging.getLogger().addHandler(logging.NullHandler())

# Make the project root importable without installing as a package
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from axen_database import get_connection, migrate


# ─────────────────────────────────────────────
#  MINIMAL Product STUB
#  Mirrors the real Product dataclass from axen_price_scraper.py
#  without importing that module (which pulls in heavy dependencies
#  like playwright, cloudscraper, etc.).
# ─────────────────────────────────────────────

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
#  DATABASE FIXTURES
# ─────────────────────────────────────────────

@pytest.fixture
def db():
    """
    In-memory SQLite connection, schema migrated, closed after each test.
    Each test gets a completely clean database — no cross-test contamination.
    """
    conn = get_connection(":memory:")
    migrate(conn)
    yield conn
    conn.close()


# ─────────────────────────────────────────────
#  ENVIRONMENT FIXTURES
# ─────────────────────────────────────────────

@pytest.fixture
def mock_env_ml_only(monkeypatch):
    """
    Only Mercado Livre enabled — the default MVP configuration.
    Nuvemshop and Shopee flags are explicitly OFF.
    """
    monkeypatch.setenv("MERCADOLIVRE_ENABLED", "true")
    monkeypatch.setenv("NUVEMSHOP_ENABLED", "false")
    monkeypatch.setenv("SHOPEE_ENABLED", "false")


@pytest.fixture
def mock_env_all_off(monkeypatch):
    """All integrations disabled — useful for testing disabled-state UI paths."""
    monkeypatch.setenv("MERCADOLIVRE_ENABLED", "false")
    monkeypatch.setenv("NUVEMSHOP_ENABLED", "false")
    monkeypatch.setenv("SHOPEE_ENABLED", "false")


# ─────────────────────────────────────────────
#  PRODUCT FACTORY HELPERS
# ─────────────────────────────────────────────

def make_product(
    store: str = "Beroc",
    name: str = "Pulseira Masculina Corda",
    price: float = 99.90,
    material: str = "corda",
    url: str = "https://beroc.com.br/products/pulseira-corda-1",
    delivery_info: str = "Frete Grátis",
    context: str = "",
) -> Product:
    """Creates a Product with sensible defaults for testing."""
    return Product(
        store=store,
        name=name,
        price=price,
        material=material,
        url=url,
        delivery_info=delivery_info,
        context=context,
    )


def make_ml_product(
    item_id: str = "MLB123456789",
    position: int = 3,
    price: float = 89.90,
    material: str = "corda",
    discount_pct: Optional[int] = None,
    query: str = "pulseira masculina corda",
) -> Product:
    """Creates a Mercado Livre product with context field correctly formatted."""
    context_parts = [f"id:{item_id}", f"position:{position}"]
    if discount_pct is not None:
        context_parts.append(f"desconto:{discount_pct}%")
    if query:
        context_parts.append(f"query:{query.replace(' ', '_')}")
    return Product(
        store="Mercado Livre",
        name=f"Pulseira Masculina {material.capitalize()} MLB",
        price=price,
        material=material,
        url=f"https://www.mercadolivre.com.br/p/{item_id}",
        delivery_info="Frete Grátis",
        context=" ".join(context_parts),
    )
