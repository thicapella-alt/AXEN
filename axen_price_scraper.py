#!/usr/bin/env python3
"""
AXEN — Análise Competitiva de Preços · Web Scraper
Lojas : Key Design · Beroc · W. Buscatti
Materiais: Couro · Metal · Corda · Pedra

SETUP:
    pip install -r requirements.txt

EXECUÇÃO:
    python axen_price_scraper.py
    python axen_price_scraper.py --cloudscraper
    python axen_price_scraper.py --demo
    python axen_price_scraper.py --materials couro metal
    python axen_price_scraper.py --axen-couro 199 --axen-metal 249
    python axen_price_scraper.py --output-dir resultados/

PROXY:
    PowerShell : $env:HTTPS_PROXY = "http://user:senha@proxy.exemplo.com:8080"
    bash       : export HTTPS_PROXY=http://user:senha@proxy.exemplo.com:8080
"""

import argparse
import csv
import logging
import os
import random
import re
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from rich import print as rprint
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

# cloudscraper é opcional — não quebra se não estiver instalado
try:
    import cloudscraper as _cs_mod
    _CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    _cs_mod = None
    _CLOUDSCRAPER_AVAILABLE = False


# ── Configuração global ───────────────────────────────────────────────────────

DELAY_MIN = 2.0
DELAY_MAX = 5.0
DEBUG_HTML = os.getenv("AXEN_DEBUG_HTML", "0") == "1"

PRICE_AUDIT         = False
PRICE_AUDIT_SAMPLES = 10
PRICE_AUDIT_COUNTS: dict[str, int] = {}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

console = Console()
log     = logging.getLogger(__name__)

STORES_ORDER    = ["Key Design", "W. Buscatti", "Beroc"]
MATERIALS_ORDER = ["couro", "metal", "corda", "pedra"]


def setup_logging(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(output_dir / "scraper.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


# ── Modelo de dados ───────────────────────────────────────────────────────────

@dataclass
class Product:
    store:        str
    name:         str
    price:        float
    material:     str
    url:          str = ""
    category_url: str = ""
    context:      str = ""


# ── Utilitários ───────────────────────────────────────────────────────────────

def polite_sleep() -> None:
    t = random.uniform(DELAY_MIN, DELAY_MAX)
    log.debug("Sleeping %.1fs", t)
    time.sleep(t)


def dump_debug_html(store: str, material: str, url: str, html: str) -> None:
    if not DEBUG_HTML:
        return
    safe = lambda s: re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    out = Path("debug_pages")
    out.mkdir(exist_ok=True)
    f = out / f"{safe(store)}_{safe(material)}_{int(time.time())}.html"
    f.write_text(f"<!-- URL: {url} -->\n{html}", encoding="utf-8")
    log.info("[%s] HTML salvo em: %s", store, f)


def audit_price(store: str, raw, parsed: Optional[float], source: str = "") -> None:
    if not PRICE_AUDIT:
        return
    count = PRICE_AUDIT_COUNTS.get(store, 0)
    if count >= PRICE_AUDIT_SAMPLES:
        return
    PRICE_AUDIT_COUNTS[store] = count + 1
    log.info("[PRICE_AUDIT][%s] raw=%r -> parsed=%r%s",
             store, raw, parsed, f" | src={source}" if source else "")


def check_robots(base_url: str, path: str = "/") -> bool:
    rp = RobotFileParser()
    rp.set_url(urljoin(base_url, "/robots.txt"))
    try:
        rp.read()
        allowed = rp.can_fetch(HEADERS["User-Agent"], urljoin(base_url, path))
        if not allowed:
            log.warning("robots.txt bloqueia %s — pulando.", urljoin(base_url, path))
        return allowed
    except Exception as e:
        log.warning("Não foi possível ler robots.txt de %s: %s. Continuando.", base_url, e)
        return True


def _get_with_retry(
    session: requests.Session,
    url: str,
    retries: int = 3,
    **kwargs,
) -> requests.Response:
    """GET com backoff exponencial: 2s → 4s → 8s."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=15, **kwargs)
            if resp.status_code < 500:
                return resp
            log.warning("HTTP %d em %s (tentativa %d/%d)",
                        resp.status_code, url, attempt + 1, retries)
        except requests.RequestException as exc:
            last_exc = exc
            log.warning("Erro em %s (tentativa %d/%d): %s", url, attempt + 1, retries, exc)
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt)
    raise last_exc or RuntimeError(f"Falha após {retries} tentativas: {url}")


def parse_price(text) -> Optional[float]:
    """Converte preço no formato BR (R$ 1.299,90) ou US (1299.90) para float."""
    if text is None:
        return None

    if isinstance(text, (int, float)):
        v = float(text)
        # Alguns endpoints retornam centavos como inteiro (ex: 29900 → R$299,00).
        # Threshold alto para não converter preços exatos como R$1.000.
        if isinstance(text, int) and v >= 100_000:
            return v / 100.0
        return v if v > 0 else None

    raw = str(text).strip()
    if not raw:
        return None

    def _token(s: str) -> Optional[float]:
        s = re.sub(r"[^\d,.\-]", "", s)
        if not s:
            return None
        has_comma = "," in s
        has_dot   = "." in s
        if has_comma and has_dot:
            # último separador é decimal
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif has_comma:
            s = s.replace(",", ".")
        elif has_dot and s.count(".") > 1:
            parts = s.split(".")
            s = "".join(parts[:-1]) + "." + parts[-1]
        try:
            return float(s)
        except ValueError:
            return None

    # Captura valores monetários, ignorando parcelas "Nx de R$ ..."
    values: list[float] = []
    for m in re.finditer(r"R\$\s*([\d\.,]+)", raw, re.IGNORECASE):
        prefix = raw[max(0, m.start() - 14): m.start()].lower()
        if re.search(r"\d+\s*x\s*de\s*$", prefix):
            continue
        v = _token(m.group(1))
        if v and v > 0:
            values.append(v)

    if values:
        return min(values)          # preço promocional é sempre o menor

    return _token(re.sub(r"[^\d,.\-]", "", raw))


def classify_material(text: str) -> str:
    """
    Couro > Metal > Corda > Pedra — ordem evita falsos positivos.
    'natural' foi removido dos keywords de pedra para não conflitar com 'Couro Natural'.
    """
    t = text.lower()

    if any(w in t for w in ["couro", "leather", "pele", "suede", "camurça", "nappa", "vaqueta"]):
        return "couro"

    if any(w in t for w in [
        "metal", "aço", "aco", "steel", "inox", "prata", "ouro", "gold",
        "silver", "titanio", "titânio", "corrente", "chain", "elo",
        "grumet", "bangle", "rigida", "rígida",
    ]):
        return "metal"

    if any(w in t for w in [
        "corda", "rope", "nylon", "náutica", "nautica", "fio", "tecido",
        "borracha", "paracord", "silicone", "âncora", "ancora", "anchor",
        "knot", "nó", "wave", "surf",
    ]):
        return "corda"

    if any(w in t for w in [
        "pedra", "stone", "hematita", "ônix", "onix", "jade", "turquesa",
        "quartzo", "obsidiana", "bead", "miçanga", "cristal", "ágata",
        "agata", "howlita", "jaspe", "lava",
    ]):
        return "pedra"

    return "desconhecido"


def is_masculine_product(p: Product) -> bool:
    text = f"{p.name} {p.url} {p.category_url} {p.context}".lower()
    norm = (
        text.replace("ã","a").replace("á","a").replace("â","a")
            .replace("é","e").replace("ê","e").replace("í","i")
            .replace("ó","o").replace("ô","o").replace("õ","o")
            .replace("ú","u").replace("ç","c")
    )
    if any(w in norm for w in ["feminino","feminina","para ela","woman","women","femin","lady","menina"]):
        return False
    if any(w in norm for w in ["masculino","masculina","para ele","homem","male","men","pulseiras-masculinas"]):
        return True
    return "pulseira" in norm


def get_session(use_cloudscraper: bool = False) -> requests.Session:
    if use_cloudscraper:
        if not _CLOUDSCRAPER_AVAILABLE:
            log.warning("cloudscraper não instalado — usando requests. Execute: pip install cloudscraper")
        else:
            s = _cs_mod.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "desktop": True}
            )
            s.headers.update(HEADERS)
            return s
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# ── Scraper: Beroc (Shopify) ──────────────────────────────────────────────────

class BerocScraper:
    BASE = "https://beroc.com.br"
    CATEGORIES = {
        "couro": "/collections/pulseiras-de-couro",
        "metal": "/collections/pulseira-de-metal",
        "corda": "/collections/pulseiras-de-corda",
        "pedra": "/collections/pulseira-de-pedra",
    }

    def __init__(self, use_cloudscraper: bool = True, materials: list[str] | None = None):
        self.session   = get_session(use_cloudscraper=use_cloudscraper)
        self.materials = materials

    def scrape(self, progress=None, task=None) -> list[Product]:
        products: list[Product] = []
        cats = {k: v for k, v in self.CATEGORIES.items()
                if self.materials is None or k in self.materials}
        for material, path in cats.items():
            if not check_robots(self.BASE, path):
                continue
            log.info("[Beroc] Raspando: %s", material)
            products.extend(self._scrape_category(path, material, progress, task))
        return products

    def _scrape_category(self, path: str, material: str, progress=None, task=None) -> list[Product]:
        products: list[Product] = []
        page = 1
        while True:
            url = f"{self.BASE}{path}/products.json?limit=250&page={page}"
            log.info("[Beroc] Página %d: %s", page, url)
            try:
                resp  = _get_with_retry(self.session, url)
                resp.raise_for_status()
                items = resp.json().get("products", [])
            except Exception as e:
                log.error("[Beroc] Erro: %s", e)
                break

            if not items:
                break

            for item in items:
                p = self._parse(item, material, url)
                if p:
                    products.append(p)

            if progress and task is not None:
                progress.advance(task)

            if len(items) < 250:
                break
            page += 1
            polite_sleep()

        log.info("[Beroc] %s: %d produtos", material, len(products))
        return products

    def _parse(self, item: dict, material: str, cat_url: str) -> Optional[Product]:
        try:
            name = (item.get("title") or "").strip()
            if not name:
                return None
            price: Optional[float] = None
            for v in item.get("variants", []):
                c = parse_price(str(v.get("price", "")))
                audit_price("Beroc", v.get("price"), c, source=cat_url)
                if c and c > 0:
                    price = c if price is None else min(price, c)
            if not price:
                return None
            handle = item.get("handle", "")
            mat    = classify_material(name)
            if mat == "desconhecido":
                mat = material
            return Product(
                store="Beroc", name=name, price=price, material=mat,
                url=urljoin(self.BASE, f"/products/{handle}") if handle else "",
                category_url=cat_url, context=f"{cat_url} {handle}",
            )
        except Exception as e:
            log.debug("[Beroc] Erro ao parsear: %s", e)
            return None


# ── Scraper: Key Design (VTEX IO) ────────────────────────────────────────────

class KeyDesignScraper:
    BASE      = "https://www.keydesign.com.br"
    PAGE_SIZE = 50
    # Paths de categoria VTEX por material — mais preciso que busca genérica
    MATERIAL_PATHS = {
        "couro": "/pulseiras-masculinas/couro-pm",
        "metal": "/pulseiras-masculinas/metal-pm",
        "corda": "/pulseiras-masculinas/corda-pm",
        "pedra": "/pulseiras-masculinas/pedra-pm",
    }

    def __init__(self, use_cloudscraper: bool = False, materials: list[str] | None = None):
        self.session   = get_session(use_cloudscraper=use_cloudscraper)
        self.materials = materials

    def scrape(self, progress=None, task=None) -> list[Product]:
        products: list[Product] = []
        mats = self.materials or list(self.MATERIAL_PATHS.keys())
        for material in mats:
            log.info("[Key Design] Raspando: %s", material)
            products.extend(self._scrape_material(material, progress, task))
        return products

    def _scrape_material(self, material: str, progress=None, task=None) -> list[Product]:
        products: list[Product] = []
        offset   = 0
        cat_path = self.MATERIAL_PATHS.get(material, "/pulseiras")
        while True:
            url = (
                f"{self.BASE}/api/catalog_system/pub/products/search{cat_path}"
                f"?_from={offset}&_to={offset + self.PAGE_SIZE - 1}"
            )
            log.info("[Key Design] %s offset %d", material, offset)
            try:
                resp  = _get_with_retry(self.session, url)
                resp.raise_for_status()
                items = resp.json()
            except Exception as e:
                log.error("[Key Design] Erro: %s", e)
                break

            if not isinstance(items, list) or not items:
                break

            for item in items:
                p = self._parse(item, material, url)
                if p:
                    products.append(p)

            if progress and task is not None:
                progress.advance(task)

            if len(items) < self.PAGE_SIZE:
                break
            offset += self.PAGE_SIZE
            polite_sleep()

        log.info("[Key Design] %s: %d produtos", material, len(products))
        return products

    def _parse(self, item: dict, material: str, cat_url: str) -> Optional[Product]:
        try:
            name = (item.get("productName") or "").strip()
            if not name:
                return None
            best: Optional[float] = None
            for sku in item.get("items", []):
                for seller in sku.get("sellers", []):
                    raw = seller.get("commertialOffer", {}).get("Price")
                    if raw is None:
                        continue
                    c = float(raw)
                    audit_price("Key Design", raw, c, source=cat_url)
                    if c > 0:
                        best = c if best is None else min(best, c)
            if best is None:
                return None
            categories = " ".join(item.get("categories", []))
            mat = classify_material(f"{categories} {name}")
            if mat == "desconhecido":
                mat = material          # usa categoria como fallback (não "metal")
            return Product(
                store="Key Design", name=name, price=best, material=mat,
                url=item.get("link", ""), category_url=cat_url, context=categories,
            )
        except Exception as e:
            log.debug("[Key Design] Erro ao parsear: %s", e)
            return None


# ── Scraper: W. Buscatti (Loja Integrada) ────────────────────────────────────

class WBuscattiScraper:
    BASE      = "https://www.wbuscatti.com.br"
    STORE_ID  = "488685"
    MAX_PAGES = 40

    # Loja Integrada: material no path da URL, paginação via ?pg=N
    CATEGORIES: dict[str, list[str]] = {
        "couro": ["/masculino/pulseiras/couro", "/pulseiras/couro", "/couro"],
        "metal": ["/masculino/pulseiras/metal", "/masculino/pulseiras/aco", "/pulseiras/metal"],
        "corda": ["/masculino/pulseiras/corda", "/masculino/pulseiras/corda-e-tecido", "/pulseiras/corda"],
        "pedra": ["/masculino/pulseiras/pedra", "/masculino/pulseiras/pedras-naturais", "/pulseiras/pedra"],
    }
    # Fallback: busca interna Loja Integrada (store ID fixo)
    SEARCH_FALLBACK = {
        "couro": f"/loja/busca.php?loja=488685&query=pulseira+couro",
        "metal": f"/loja/busca.php?loja=488685&query=pulseira+metal",
        "corda": f"/loja/busca.php?loja=488685&query=pulseira+corda",
        "pedra": f"/loja/busca.php?loja=488685&query=pulseira+pedra",
    }
    CARD_SELECTORS  = [".produto", ".produto-item", "li.produto", '[class*="produto"]', ".shelf-item"]
    NAME_SELECTORS  = [".produto-nome", ".titulo", "h2", "h3", '[class*="nome"]', '[class*="titulo"]']
    PRICE_SELECTORS = [".produto-preco", ".preco-por", ".preco-promocional", ".preco", '[class*="preco"]', ".valor"]

    def __init__(self, use_cloudscraper: bool = True, materials: list[str] | None = None):
        self.session   = get_session(use_cloudscraper=use_cloudscraper)
        self.materials = materials

    def scrape(self, progress=None, task=None) -> list[Product]:
        products: list[Product] = []
        cats = {k: v for k, v in self.CATEGORIES.items()
                if self.materials is None or k in self.materials}
        for material, paths in cats.items():
            log.info("[W.Buscatti] Raspando: %s", material)
            found = False
            for path in paths:
                if not check_robots(self.BASE, path):
                    continue
                result = self._scrape_category(path, material, progress, task)
                if result:
                    products.extend(result)
                    found = True
                    break
                polite_sleep()
            if not found:
                log.info("[W.Buscatti] Tentando busca para: %s", material)
                fb = self.SEARCH_FALLBACK.get(material, "")
                if fb:
                    result = self._scrape_category(fb, material, progress, task)
                    products.extend(result)
                if not result:
                    log.warning(
                        "[W.Buscatti] 0 produtos para '%s'. "
                        "Defina AXEN_DEBUG_HTML=1 para inspecionar o HTML.",
                        material,
                    )
        return products

    def _scrape_category(self, cat_path: str, material: str, progress=None, task=None) -> list[Product]:
        products: list[Product] = []
        page           = 1
        last_signature = ""
        stale_pages    = 0

        while page <= self.MAX_PAGES:
            sep = "&" if "?" in cat_path else "?"
            url = f"{self.BASE}{cat_path}" if page == 1 else f"{self.BASE}{cat_path}{sep}pg={page}"
            log.info("[W.Buscatti] Página %d: %s", page, url)

            try:
                resp = _get_with_retry(self.session, url)
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
            except Exception as e:
                log.error("[W.Buscatti] Erro: %s", e)
                break

            soup  = BeautifulSoup(resp.text, "lxml")
            cards = []
            for sel in self.CARD_SELECTORS:
                cards = soup.select(sel)
                if len(cards) >= 2:
                    break
            if not cards:
                cards = soup.find_all(["li", "div"], class_=re.compile(r"produto|item|product", re.I))

            if not cards:
                log.info("[W.Buscatti] Sem produtos na página %d.", page)
                dump_debug_html("WBuscatti", material, url, resp.text)
                break

            page_products: list[Product] = []
            for card in cards:
                p = self._parse_card(card, material, url)
                if p:
                    page_products.append(p)

            # Stale detection: compara apenas com a página imediatamente anterior
            signature = "|".join(sorted(p.url for p in page_products if p.url))
            if signature and signature == last_signature:
                stale_pages += 1
            else:
                stale_pages = 0
            last_signature = signature

            if stale_pages >= 2:
                log.info("[W.Buscatti] Conteúdo repetido — encerrando paginação.")
                break

            products.extend(page_products)

            if progress and task is not None:
                progress.advance(task)

            if not soup.select_one("a[rel='next'], .proxima-pagina a, .pagination a.next"):
                break

            page += 1
            polite_sleep()

        log.info("[W.Buscatti] %s: %d produtos", material, len(products))
        return products

    def _first_text(self, card, selectors: list[str]) -> str:
        for sel in selectors:
            el = card.select_one(sel)
            if el:
                t = el.get_text(" ", strip=True)
                if t:
                    return t
        return ""

    def _parse_card(self, card, material: str, cat_url: str) -> Optional[Product]:
        try:
            name       = self._first_text(card, self.NAME_SELECTORS)
            price_text = self._first_text(card, self.PRICE_SELECTORS) or card.get_text(" ", strip=True)
            price      = parse_price(price_text)
            audit_price("W. Buscatti", price_text, price, source=cat_url)
            if not name or not price or price <= 0:
                return None
            link_el  = card.select_one("a[href]")
            prod_url = (link_el["href"] if link_el else "").strip()
            if prod_url and not prod_url.startswith("http"):
                prod_url = urljoin(self.BASE, prod_url)
            mat = classify_material(name)
            if mat == "desconhecido":
                mat = material
            return Product(
                store="W. Buscatti", name=name, price=price, material=mat,
                url=prod_url, category_url=cat_url,
            )
        except Exception as e:
            log.debug("[W.Buscatti] Erro ao parsear card: %s", e)
            return None


# ── Demo (sem internet) ───────────────────────────────────────────────────────

def _demo_products() -> list[Product]:
    rng = random.Random(42)
    catalogue: dict[str, dict[str, list[tuple[str, float]]]] = {
        "Key Design": {
            "couro": [("Couro Preta Trançada",249.90),("Couro Marrom Flat",189.90),("Couro Verde Militar",219.90),("Couro Caramelo Ajust.",279.90),("Couro Azul Naval",259.90),("Couro Double Wrap",319.90),("Couro Vegano Preto",179.90),("Couro Envelhecido",289.90)],
            "metal":  [("Metal Aço Escovado",179.90),("Elo Grumet Prata",199.90),("Titan Fosco",249.90),("Cartier Dourado",299.90),("Malha Milanesa",219.90),("Cadeado Preto",189.90),("PVD Preto",239.90)],
            "corda":  [("Corda Náutica Azul",129.90),("Corda Trançada Preta",119.90),("Mix Náutico",149.90),("Nó Marinheiro",99.90),("Dupla Trança Cinza",109.90)],
            "pedra":  [("Pedra Olho de Tigre",199.90),("Ônix Facetado",219.90),("Turmalina Negra",249.90),("Lava Vulcânica",189.90),("Howlita Branca",179.90),("Ágata Azul",229.90),("Hematita Fosca",169.90)],
        },
        "W. Buscatti": {
            "couro": [("W.B. Couro Preta Costurada",159.90),("W.B. Couro Marrom Vintage",179.90),("W.B. Nappa Preta",219.90),("W.B. Dupla Preta",199.90),("W.B. Caramelo Ajust.",169.90),("W.B. Tressê Marrom",209.90)],
            "metal":  [("W.B. Grumet Fino Prata",149.90),("W.B. Elo Largo Ouro",189.90),("W.B. Aço Escovado",169.90),("W.B. Cadeado Inox",159.90),("W.B. Milanesa Prata",179.90),("W.B. Cartier Fino",139.90)],
            "corda":  [("W.B. Náutica Verde",89.90),("W.B. Corda Preta Dupla",99.90),("W.B. Surf Branca",84.90),("W.B. Corda Colorida",79.90)],
            "pedra":  [("W.B. Ônix Redondo",159.90),("W.B. Olho de Tigre",149.90),("W.B. Lava Fosca",139.90),("W.B. Hematita",129.90),("W.B. Turmalina",179.90)],
        },
        "Beroc": {
            "couro": [("Beroc Strand Classic",229.90),("Beroc Dark Brown Heritage",259.90),("Beroc Black Nappa",289.90),("Beroc Caramel Wrap",199.90),("Beroc Navy Wax",219.90),("Beroc Double Wrap",269.90),("Beroc Premium Heritage",419.90)],
            "metal":  [("Beroc Steel Mesh",209.90),("Beroc Gold Figaro",239.90),("Beroc Black PVD",249.90),("Beroc Silver Curb",189.90),("Beroc Rose Gold Chain",229.90),("Beroc Brushed Titan",259.90)],
            "corda":  [("Beroc Náutica Azul",119.90),("Beroc Rope Natural",109.90),("Beroc Marinheiro Preto",99.90),("Beroc Surf Branca Azul",114.90),("Beroc Macramê Bege",124.90)],
            "pedra":  [("Beroc Lava Rock",189.90),("Beroc Tiger Eye",209.90),("Beroc Black Onyx",219.90),("Beroc Howlite White",179.90),("Beroc Turmalina Dark",239.90),("Beroc Hematite Matte",169.90)],
        },
    }
    products: list[Product] = []
    for store, mats in catalogue.items():
        for mat, items in mats.items():
            for name, price in items:
                products.append(Product(
                    store=store, name=name, material=mat,
                    price=round(price * rng.uniform(0.95, 1.05), 2),
                    url="#demo",
                ))
    return products


# ── Análise e relatório ───────────────────────────────────────────────────────

def build_rows(products: list[Product], *, male_only: bool = False) -> list[dict]:
    seen: set[tuple] = set()
    rows: list[dict] = []
    for p in products:
        if male_only and not is_masculine_product(p):
            continue
        key = (p.store, p.name, round(p.price, 2))
        if key in seen:
            continue
        seen.add(key)
        rows.append({"Loja": p.store, "Material": p.material,
                     "Produto": p.name, "Preço": float(p.price), "URL": p.url})
    return rows


def build_summary(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        grouped.setdefault((row["Loja"], row["Material"]), []).append(float(row["Preço"]))
    summary: list[dict] = []
    for (store, material), prices in grouped.items():
        summary.append({
            "Loja":     store,
            "Material": material,
            "Mínimo":   round(min(prices), 2),
            "Mediana":  round(statistics.median(prices), 2),
            "Médio":    round(statistics.mean(prices), 2),
            "Máximo":   round(max(prices), 2),
            "Produtos": len(prices),
        })
    return sorted(summary, key=lambda r: (r["Material"], r["Loja"]))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_rich_table(summary: list[dict]) -> None:
    table = Table(
        title="Análise Competitiva de Preços — AXEN vs. Concorrentes",
        header_style="bold white on dark_blue",
        border_style="blue",
        show_lines=True,
    )
    table.add_column("Loja",         style="bold cyan", min_width=14)
    table.add_column("Material",     style="yellow",    min_width=10)
    table.add_column("Mín (R$)",     justify="right",   min_width=10)
    table.add_column("Mediana (R$)", justify="right",   min_width=11)
    table.add_column("Médio (R$)",   justify="right",   min_width=11, style="bold green")
    table.add_column("Máx (R$)",     justify="right",   min_width=10)
    table.add_column("Qtd",          justify="center",  min_width=5)

    idx = {(r["Loja"], r["Material"]): r for r in summary}
    for store in STORES_ORDER:
        for mat in MATERIALS_ORDER:
            row = idx.get((store, mat))
            if row:
                table.add_row(
                    row["Loja"], row["Material"].capitalize(),
                    f"R$ {row['Mínimo']:.2f}", f"R$ {row['Mediana']:.2f}",
                    f"R$ {row['Médio']:.2f}",  f"R$ {row['Máximo']:.2f}",
                    str(int(row["Produtos"])),
                )
            else:
                table.add_row(store, mat.capitalize(), "—", "—", "—", "—", "—")
    console.print(table)


def print_axen_insights(summary: list[dict], axen_prices: dict[str, Optional[float]]) -> None:
    rprint("\n[bold white on dark_orange3]  RESUMO EXECUTIVO — POSICIONAMENTO AXEN  [/]")
    for material in MATERIALS_ORDER:
        axen = axen_prices.get(material)
        if axen is None:
            rprint(f"\n[dim]{material.upper()}: sem preço AXEN definido (use --axen-{material} VALOR).[/]")
            continue
        mat_rows = [r for r in summary if r["Material"] == material]
        if not mat_rows:
            rprint(f"\n[yellow]{material.upper()}:[/] sem dados dos concorrentes.")
            continue
        rprint(f"\n[bold yellow]▶ {material.upper()}[/]  (AXEN: [bold green]R$ {axen:.0f}[/])")
        for row in mat_rows:
            avg  = row["Médio"]
            diff = (axen - avg) / avg * 100
            if diff < -15:
                pos = f"[green]{diff:+.1f}% vs média ← AXEN mais barata[/]"
            elif diff < 0:
                pos = f"[green]{diff:+.1f}% vs média ← AXEN ligeiramente mais barata[/]"
            elif diff < 10:
                pos = f"[yellow]{diff:+.1f}% vs média ← AXEN equivalente[/]"
            else:
                pos = f"[red]{diff:+.1f}% vs média ← AXEN mais cara[/]"
            rprint(
                f"  {row['Loja']:15s} | "
                f"Faixa: R${row['Mínimo']:.0f}–{row['Máximo']:.0f} | "
                f"Mediana: R${row['Mediana']:.0f} | Média: R${avg:.0f} | {pos}"
            )


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AXEN — análise competitiva de preços.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("--cloudscraper",  action="store_true", help="Bypass Cloudflare via cloudscraper.")
    p.add_argument("--male-only",     action="store_true", help="Mantém apenas produtos masculinos.")
    p.add_argument("--price-audit",   action="store_true", help="Loga amostras de normalização de preço.")
    p.add_argument("--price-audit-samples", type=int, default=10, metavar="N")
    p.add_argument("--materials", nargs="+", choices=MATERIALS_ORDER,
                   help="Scrapa só os materiais indicados (padrão: todos).")
    p.add_argument("--output-dir", type=Path, default=Path("."),
                   help="Diretório de saída para CSVs e log.")
    p.add_argument("--demo", action="store_true", help="Dados simulados — sem internet.")
    p.add_argument("--axen-couro", type=float, default=None, metavar="R$")
    p.add_argument("--axen-metal", type=float, default=None, metavar="R$")
    p.add_argument("--axen-corda", type=float, default=None, metavar="R$")
    p.add_argument("--axen-pedra", type=float, default=None, metavar="R$")
    return p.parse_args()


def main() -> None:
    args = build_args()

    global PRICE_AUDIT, PRICE_AUDIT_SAMPLES, PRICE_AUDIT_COUNTS
    PRICE_AUDIT         = args.price_audit
    PRICE_AUDIT_SAMPLES = args.price_audit_samples
    PRICE_AUDIT_COUNTS  = {}

    setup_logging(args.output_dir)
    rprint("\n[bold white on blue]  AXEN Price Intelligence Scraper · Iniciando  [/]\n")

    axen_prices = {
        "couro": args.axen_couro,
        "metal": args.axen_metal,
        "corda": args.axen_corda,
        "pedra": args.axen_pedra,
    }

    # ── Coleta ──
    all_products: list[Product] = []

    if args.demo:
        rprint("[yellow]Modo demo — dados simulados (sem acesso à internet).[/]")
        all_products = _demo_products()
        if args.materials:
            all_products = [p for p in all_products if p.material in args.materials]
    else:
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TaskProgressColumn(), console=console,
        ) as prog:
            task_b = prog.add_task("[cyan]Beroc…",       total=None)
            task_k = prog.add_task("[cyan]Key Design…",  total=None)
            task_w = prog.add_task("[cyan]W. Buscatti…", total=None)

            for label, task, scraper_cls, kwargs in [
                ("Beroc",       task_b, BerocScraper,      {"use_cloudscraper": True,                  "materials": args.materials}),
                ("Key Design",  task_k, KeyDesignScraper,  {"use_cloudscraper": args.cloudscraper,     "materials": args.materials}),
                ("W. Buscatti", task_w, WBuscattiScraper,  {"use_cloudscraper": True,                  "materials": args.materials}),
            ]:
                try:
                    prods = scraper_cls(**kwargs).scrape(progress=prog, task=task)
                    all_products.extend(prods)
                    prog.update(task, description=f"[green]{label} ✓ {len(prods)} produtos")
                except Exception as e:
                    prog.update(task, description=f"[red]{label} ✗ {e}")
                    log.exception("%s falhou", label)

    if not all_products:
        rprint("[red bold]Nenhum produto coletado. Verifique conectividade ou use --demo.[/]")
        return

    rprint(f"\n[bold]Total coletado: {len(all_products)} produtos[/]")

    rows    = build_rows(all_products, male_only=args.male_only)
    if args.male_only:
        rprint(f"[bold]Após filtro masculino: {len(rows)} produtos[/]")
    if not rows:
        rprint("[red]Nenhum produto encontrado com os critérios atuais.[/]")
        return

    summary = build_summary(rows)

    # ── Exportar CSVs ──
    out = args.output_dir
    write_csv(out / "axen_competitive_prices.csv",  rows,    ["Loja","Material","Produto","Preço","URL"])
    write_csv(out / "axen_competitive_summary.csv", summary, ["Loja","Material","Mínimo","Mediana","Médio","Máximo","Produtos"])
    rprint(f"\n[green]✓ CSVs salvos em:[/] {out}/")

    print_rich_table(summary)

    if any(v is not None for v in axen_prices.values()):
        print_axen_insights(summary, axen_prices)
    else:
        rprint("\n[dim]Dica: use --axen-couro 199 --axen-metal 249 etc. para ver o posicionamento AXEN.[/]")

    rprint("\n[bold green]✓ Análise concluída.[/]")


if __name__ == "__main__":
    main()
