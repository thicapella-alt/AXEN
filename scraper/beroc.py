"""
Beroc scraper — Shopify platform.

Uses the public Shopify /products.json endpoint (no authentication needed).
Shopify intentionally keeps this endpoint open for search engines.
robots.txt allows /collections/* and /products.json.

Strategy:
  1. Try material-specific collection handles first.
  2. If a collection returns 0 results, fall back to fetching all products
     and detecting material from tags / product_type / title.
"""
import logging
from typing import Optional

import requests

from .models import Product
from .utils import parse_price, polite_delay

logger = logging.getLogger(__name__)

STORE_URL = "https://beroc.com.br"

# Ordered list of candidate collection handles per material.
# The scraper tries each in sequence, stopping at the first that returns data.
_MATERIAL_COLLECTIONS: dict[str, list[str]] = {
    "couro": ["pulseiras-de-couro", "couro", "leather"],
    "metal": ["pulseiras-de-metal", "metal", "aco-inox", "aco"],
    "corda": ["pulseiras-de-corda", "corda", "nautica", "corda-e-tecido"],
    "pedra": ["pulseiras-de-pedra", "pedra", "pedras-naturais", "natural-stones"],
}

# Keywords used for tag-based fallback detection (all lowercase).
_MATERIAL_KEYWORDS: dict[str, list[str]] = {
    "couro": ["couro", "leather", "nappa", "vaqueta"],
    "metal": ["metal", "aço", "aco", "inox", "steel", "titanio", "prata", "ouro"],
    "corda": ["corda", "rope", "náutica", "nautica", "tecido", "nylon", "macramê"],
    "pedra": ["pedra", "stone", "cristal", "ágata", "agata", "ônix", "onix",
              "turmalina", "howlita", "quartzo", "jaspe", "hematita"],
}


def _detect_material(tags: list[str], product_type: str, title: str) -> Optional[str]:
    haystack = " ".join(tags).lower() + " " + product_type.lower() + " " + title.lower()
    for material, keywords in _MATERIAL_KEYWORDS.items():
        if any(kw in haystack for kw in keywords):
            return material
    return None


def _fetch_collection_json(handle: str, session: requests.Session) -> list[dict]:
    """Return all raw Shopify products from a collection (handles pagination)."""
    products: list[dict] = []
    page = 1
    url = f"{STORE_URL}/collections/{handle}/products.json"

    while True:
        try:
            resp = session.get(url, params={"limit": 250, "page": page}, timeout=20)
        except requests.RequestException as exc:
            logger.warning("Beroc GET %s page %d: %s", handle, page, exc)
            break

        if resp.status_code == 404:
            break
        if resp.status_code != 200:
            logger.warning("Beroc %s returned HTTP %d", handle, resp.status_code)
            break

        batch = resp.json().get("products", [])
        products.extend(batch)
        logger.debug("Beroc %s page %d: %d products", handle, page, len(batch))

        if len(batch) < 250:
            break

        page += 1
        polite_delay(0.8, 1.5)

    return products


def _raw_to_product(raw: dict, material: str) -> Optional[Product]:
    prices = [
        float(v["price"])
        for v in raw.get("variants", [])
        if v.get("price") and float(v["price"]) > 0
    ]
    if not prices:
        return None
    return Product(
        store="Beroc",
        name=raw.get("title", "").strip(),
        price=min(prices),
        material=material,
        url=f"{STORE_URL}/products/{raw.get('handle', '')}",
    )


def scrape(session: requests.Session) -> list[Product]:
    results: list[Product] = []
    seen_ids: set[int] = set()

    # --- Pass 1: collection-specific endpoints ---
    for material, handles in _MATERIAL_COLLECTIONS.items():
        for handle in handles:
            raw_list = _fetch_collection_json(handle, session)
            if not raw_list:
                polite_delay(0.5, 1.0)
                continue

            for raw in raw_list:
                pid = raw.get("id")
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                p = _raw_to_product(raw, material)
                if p:
                    results.append(p)

            logger.info("Beroc [%s] via /%s: %d products", material, handle, len(raw_list))
            polite_delay(1.0, 2.0)
            break  # first working handle wins

    # --- Pass 2: full-catalog fallback for any material that came up empty ---
    missing = [m for m in _MATERIAL_COLLECTIONS if not any(p.material == m for p in results)]
    if missing:
        logger.info("Beroc: no collection match for %s — scanning full catalog", missing)
        all_raw = _fetch_collection_json("todos-os-produtos", session)
        if not all_raw:
            # Last resort: public /products.json root endpoint
            all_raw = _fetch_collection_json("__all", session)
            if not all_raw:
                try:
                    r = session.get(f"{STORE_URL}/products.json",
                                    params={"limit": 250}, timeout=20)
                    if r.status_code == 200:
                        all_raw = r.json().get("products", [])
                except Exception:
                    pass

        for raw in all_raw:
            pid = raw.get("id")
            if pid in seen_ids:
                continue
            mat = _detect_material(
                raw.get("tags", []),
                raw.get("product_type", ""),
                raw.get("title", ""),
            )
            if mat not in missing:
                continue
            seen_ids.add(pid)
            p = _raw_to_product(raw, mat)
            if p:
                results.append(p)

    logger.info("Beroc total: %d products across %d materials", len(results),
                len({p.material for p in results}))
    return results
