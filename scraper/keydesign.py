"""
Key Design scraper — VTEX IO platform, Cloudflare-protected.

Strategy (in order of preference):
  1. VTEX classic catalog search REST API — fastest, no JS rendering.
     Endpoint: /api/catalog_system/pub/products/search/{path}?_from=N&_to=M
     Paginates via _from/_to; total count read from "resources" response header.

  2. cloudscraper — lightweight Cloudflare JS-challenge bypass using a real
     TLS fingerprint.  No headless browser needed.  Install: pip install cloudscraper

  3. Playwright + stealth — full headless browser fallback for strong WAF.
     Install: pip install playwright playwright-stealth && playwright install chromium

VTEX CSS handles (stable across all VTEX IO stores):
  - Gallery item  : .vtex-search-result-3-x-galleryItem
  - Product name  : .vtex-product-summary-2-x-nameContainer
  - Selling price : .vtex-product-price-1-x-sellingPrice
  - Link wrapper  : a.vtex-product-summary-2-x-clearLink
"""
import logging
import re
from typing import Optional

import requests

from .models import Product
from .utils import parse_price, polite_delay

logger = logging.getLogger(__name__)

STORE_URL = "https://www.keydesign.com.br"

# VTEX category URL paths to try per material.
_MATERIAL_PATHS: dict[str, list[str]] = {
    "couro": [
        "/pulseiras-masculinas/couro-pm",
        "/pulseiras/couro",
        "/couro",
        "/acessorios-personalizados/couro",
        "/pulseiras-masculinas-de-couro",
    ],
    "metal": [
        "/pulseiras-masculinas/metal-pm",
        "/pulseiras/metal",
        "/metal",
        "/acessorios-personalizados/metal",
        "/pulseiras-masculinas-de-metal",
    ],
    "corda": [
        "/pulseiras-masculinas/corda-pm",
        "/pulseiras/corda",
        "/corda",
        "/acessorios-personalizados/corda",
        "/pulseiras-masculinas-de-corda",
    ],
    "pedra": [
        "/pulseiras-masculinas/pedra-pm",
        "/pulseiras/pedra",
        "/pedra",
        "/acessorios-personalizados/pedra",
        "/pulseiras-masculinas-de-pedra",
    ],
}

# VTEX specification filter candidates per material (filter ID may vary per store).
# The scraper probes filter IDs 70–80 automatically.
_MATERIAL_SPEC_VALUES: dict[str, list[str]] = {
    "couro": ["Couro", "couro", "Leather"],
    "metal": ["Metal", "metal", "Aço", "Aco", "Inox"],
    "corda": ["Corda", "corda", "Náutica", "Nautica"],
    "pedra": ["Pedra", "pedra", "Pedra Natural"],
}

# JS snippet injected via Playwright to extract product data from VTEX IO DOM.
_EXTRACT_JS = """
() => {
    const items = [];
    const cards = document.querySelectorAll(
        '.vtex-search-result-3-x-galleryItem, ' +
        '[class*="galleryItem"], [class*="productSummary"]'
    );
    cards.forEach(card => {
        const nameEl =
            card.querySelector('[class*="nameContainer"]') ||
            card.querySelector('[class*="productName"]') ||
            card.querySelector('h2, h3');
        const priceEl =
            card.querySelector('[class*="sellingPrice"]') ||
            card.querySelector('[class*="price"]');
        const linkEl = card.querySelector('a[href]');

        const name = nameEl ? nameEl.innerText.trim() : null;
        const priceText = priceEl ? priceEl.innerText.trim() : null;
        const url = linkEl ? linkEl.href : '';

        if (name && priceText) {
            items.push({ name, priceText, url });
        }
    });
    return items;
}
"""


# ── VTEX catalog REST API ─────────────────────────────────────────────────────

def _vtex_api_fetch(
    session: requests.Session, path: str, from_idx: int
) -> tuple[list[dict], int]:
    """
    Call VTEX catalog search API.
    Returns (products_list, total_count).
    total_count=0 means the call failed or returned nothing.
    """
    url = f"{STORE_URL}/api/catalog_system/pub/products/search{path}"
    params = {"_from": from_idx, "_to": from_idx + 49}
    try:
        resp = session.get(url, params=params, timeout=20)
    except requests.RequestException as exc:
        logger.debug("VTEX API %s: %s", path, exc)
        return [], 0

    if resp.status_code != 200:
        logger.debug("VTEX API %s → HTTP %d", path, resp.status_code)
        return [], 0

    try:
        data = resp.json()
    except ValueError:
        return [], 0

    if not isinstance(data, list) or not data:
        return [], 0

    # Parse total from "resources: 0-49/200" header
    resources = resp.headers.get("resources", "")
    total = 0
    m = re.search(r"/(\d+)$", resources)
    if m:
        total = int(m.group(1))
    else:
        total = len(data)

    return data, total


def _parse_vtex_product(raw: dict, material: str) -> Optional[Product]:
    name = raw.get("productName") or raw.get("name", "")
    link = raw.get("link", "")

    price: Optional[float] = None
    for item in raw.get("items", []):
        for seller in item.get("sellers", []):
            offer = seller.get("commertialOffer", {})
            p = offer.get("Price") or offer.get("ListPrice")
            if p and float(p) > 0:
                price = float(p) if price is None else min(price, float(p))

    if not name or price is None:
        return None
    return Product(store="Key Design", name=name.strip(), price=price,
                   material=material, url=link)


def _scrape_via_vtex_api(
    session: requests.Session, material: str
) -> list[Product]:
    results: list[Product] = []

    # Try category path endpoints
    for path in _MATERIAL_PATHS[material]:
        from_idx = 0
        batch_results: list[Product] = []

        while True:
            data, total = _vtex_api_fetch(session, path, from_idx)
            if not data:
                break

            for raw in data:
                p = _parse_vtex_product(raw, material)
                if p:
                    batch_results.append(p)

            from_idx += 50
            if from_idx >= total:
                break
            polite_delay(0.8, 1.5)

        if batch_results:
            logger.info("Key Design [%s] via API %s: %d products",
                        material, path, len(batch_results))
            return batch_results

        polite_delay(0.5, 1.0)

    # Try specification filter (probe IDs 68–82)
    for filter_id in range(68, 83):
        for value in _MATERIAL_SPEC_VALUES[material]:
            path = f"/?fq=specificationFilter_{filter_id}:{value}"
            data, total = _vtex_api_fetch(session, "", from_idx=0)
            # rewrite URL inline since path has query string
            url = (f"{STORE_URL}/api/catalog_system/pub/products/search/"
                   f"?fq=specificationFilter_{filter_id}:{value}&_from=0&_to=49")
            try:
                resp = session.get(url, timeout=20)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        products = [_parse_vtex_product(r, material) for r in data]
                        products = [p for p in products if p]
                        if products:
                            logger.info(
                                "Key Design [%s] via specificationFilter_%d:%s: %d products",
                                material, filter_id, value, len(products))
                            return products
            except Exception:
                pass
        polite_delay(0.3, 0.6)

    return []


# ── Playwright fallback ───────────────────────────────────────────────────────

def _scrape_via_playwright(material: str) -> list[Product]:
    """Full headless-browser fallback. Requires playwright + playwright-stealth."""
    results: list[Product] = []

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        logger.warning(
            "Key Design: Playwright not installed. "
            "Run: pip install playwright playwright-stealth && playwright install chromium"
        )
        return results

    stealth_fn = None
    try:
        from playwright_stealth import stealth_sync  # type: ignore
        stealth_fn = stealth_sync
    except ImportError:
        logger.warning("playwright-stealth not installed — Cloudflare bypass may fail.")

    paths = _MATERIAL_PATHS[material]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
        )
        page = ctx.new_page()
        if stealth_fn:
            stealth_fn(page)

        for path in paths:
            pg_num = 1

            while pg_num <= 25:
                url = f"{STORE_URL}{path}?page={pg_num}"
                try:
                    page.goto(url, wait_until="networkidle", timeout=35_000)
                    page.wait_for_timeout(2_500)
                except Exception as exc:
                    logger.warning("Playwright: %s → %s", url, exc)
                    break

                content = page.content()
                if "403" in page.title() or "Access Denied" in content[:500]:
                    logger.error(
                        "Key Design: Cloudflare blocked Playwright. "
                        "Use a residential proxy (e.g. Bright Data) and retry."
                    )
                    break

                items = page.evaluate(_EXTRACT_JS)
                if not items:
                    break

                for item in items:
                    price = parse_price(item.get("priceText", ""))
                    if item.get("name") and price:
                        results.append(Product(
                            store="Key Design",
                            name=item["name"],
                            price=price,
                            material=material,
                            url=item.get("url", ""),
                        ))

                pg_num += 1
                polite_delay(2.0, 4.0)

            if results:
                logger.info("Key Design [%s] via Playwright: %d products",
                            material, len(results))
                break

        ctx.close()
        browser.close()

    return results


# ── Public entry point ────────────────────────────────────────────────────────

def scrape(
    session: requests.Session,
    use_playwright: bool = True,
) -> list[Product]:
    results: list[Product] = []

    for material in _MATERIAL_PATHS:
        logger.info("Key Design: scraping material '%s'", material)

        products = _scrape_via_vtex_api(session, material)

        if not products and use_playwright:
            logger.info("Key Design [%s]: API blocked — trying Playwright", material)
            products = _scrape_via_playwright(material)

        if not products:
            logger.warning(
                "Key Design [%s]: 0 products collected.\n"
                "  → The VTEX API may be behind Cloudflare WAF.\n"
                "  → Install and configure: pip install cloudscraper playwright playwright-stealth\n"
                "  → Or supply a residential proxy via HTTPS_PROXY env var.",
                material,
            )

        results.extend(products)
        polite_delay(1.5, 3.0)

    logger.info("Key Design total: %d products", len(results))
    return results
