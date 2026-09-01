"""
integrations/axen_nuvemshop_scraper.py — Coleta pedidos do painel admin da Nuvemshop via Playwright.

Não requer upgrade de plano — faz login no painel web e extrai pedidos diretamente.

Credenciais no .env:
    NUVEMSHOP_EMAIL       email de login
    NUVEMSHOP_PASSWORD    senha de login
    NUVEMSHOP_STORE_URL   URL base da loja (sem /admin)
                          padrão: https://testenuvemenvio.lojavirtualnuvem.com.br
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)

STORE_URL = os.getenv(
    "NUVEMSHOP_STORE_URL",
    "https://testenuvemenvio.lojavirtualnuvem.com.br",
).rstrip("/")
EMAIL    = os.getenv("NUVEMSHOP_EMAIL", "")
PASSWORD = os.getenv("NUVEMSHOP_PASSWORD", "")

LOGIN_URL = "https://www.nuvemshop.com.br/login"


# ── Public API ─────────────────────────────────────────────────────────────────

def is_configured() -> bool:
    return bool(EMAIL and PASSWORD)


def get_analytics(days: int = 30) -> list[dict]:
    """
    Coleta dados de visitas e comportamento do painel admin da Nuvemshop.
    Retorna lista de dicts com dados diários compatíveis com upsert_visit().
    """
    if not is_configured():
        log.warning("[Nuvemshop] Analytics: credenciais não configuradas.")
        return []

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.warning("[Nuvemshop] Playwright não instalado.")
        return []

    records: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        try:
            _login(page)
            records = _scrape_analytics(page, days=days)
            log.info("[Nuvemshop] %d registros de visitas coletados.", len(records))
        except PWTimeout as e:
            log.error("[Nuvemshop] Analytics timeout: %s", e)
        except Exception as e:
            log.error("[Nuvemshop] Analytics erro: %s", e)
        finally:
            browser.close()

    return records


def get_orders(days: int = 30) -> list[dict]:
    """
    Coleta pedidos dos últimos `days` dias do painel admin da Nuvemshop.
    Retorna lista de dicts compatíveis com upsert_sale().
    Retorna [] se não configurado ou se Playwright não estiver disponível.
    """
    if not is_configured():
        log.warning("[Nuvemshop] NUVEMSHOP_EMAIL ou NUVEMSHOP_PASSWORD não configurados.")
        return []

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.warning("[Nuvemshop] Playwright não instalado.")
        return []

    orders: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        try:
            _login(page)
            orders = _scrape_orders(page, days=days)
            log.info("[Nuvemshop] %d pedidos coletados.", len(orders))
        except PWTimeout as e:
            log.error("[Nuvemshop] Timeout: %s", e)
        except Exception as e:
            log.error("[Nuvemshop] Erro: %s", e)
        finally:
            browser.close()

    return orders


# ── Login ──────────────────────────────────────────────────────────────────────

_EMAIL_SELECTORS = [
    'input[type="email"]',
    'input[name="email"]',
    'input[id="email"]',
    'input[name="user[email]"]',
    'input[placeholder*="mail" i]',
    'input[placeholder*="E-mail" i]',
]
_PASSWORD_SELECTORS = [
    'input[type="password"]',
    'input[name="password"]',
    'input[id="password"]',
    'input[name="user[password]"]',
]


def _find_input(page, selectors: list[str], label: str, screenshot_path: str):
    for sel in selectors:
        try:
            el = page.wait_for_selector(sel, timeout=4_000)
            if el:
                log.info("[Nuvemshop] Campo '%s' encontrado com: %s", label, sel)
                return el
        except Exception:
            continue
    page.screenshot(path=screenshot_path)
    raise ValueError(
        f"[Nuvemshop] Campo '{label}' não encontrado. "
        f"Screenshot salvo em {screenshot_path}"
    )


def _login(page) -> None:
    store_host = STORE_URL.replace("https://", "").replace("http://", "")
    admin_url  = f"{STORE_URL}/admin/"

    log.info("[Nuvemshop] Navegando para o admin (aguarda redirect de login)...")
    page.goto(admin_url, wait_until="domcontentloaded", timeout=30_000)

    # O redirect URL de login pode conter store_host como query param
    # (ex: ?return_url=https://testenuvem…/admin/), então verificamos também
    # que não há indicador de página de login na URL atual.
    _login_keywords = ("login", "entrar", "signin", "users/session", "accounts", "tiendanube")
    current_url = page.url
    on_login_page = any(kw in current_url.lower() for kw in _login_keywords)

    if not on_login_page and store_host in current_url and "/admin" in current_url:
        log.info("[Nuvemshop] Já autenticado — sem necessidade de login.")
        return

    log.info("[Nuvemshop] Página de login detectada: %s", page.url)

    # Nuvemshop pode mostrar escolha de método (Google / Apple / E-mail) antes
    # do formulário. Se houver o botão "Entrar com e-mail", clica primeiro.
    try:
        email_btn = page.get_by_text(re.compile(r"entrar com e-?mail", re.IGNORECASE))
        if email_btn.count() > 0:
            log.info("[Nuvemshop] Clicando em 'Entrar com e-mail'...")
            email_btn.first.click()
            page.wait_for_timeout(1_500)
    except Exception as e:
        log.debug("[Nuvemshop] Botão 'Entrar com e-mail' não encontrado: %s", e)

    # Aguarda campo de email aparecer
    try:
        page.wait_for_selector("input", timeout=15_000)
    except Exception:
        page.screenshot(path="/var/www/axen/debug_nuvemshop_login.png")
        raise ValueError(
            f"[Nuvemshop] Nenhum <input> encontrado após navegar para {page.url}. "
            "Screenshot salvo em debug_nuvemshop_login.png"
        )

    log.info("[Nuvemshop] Preenchendo credenciais...")

    email_el = _find_input(page, _EMAIL_SELECTORS, "email", "/var/www/axen/debug_nuvemshop_login.png")
    email_el.fill(EMAIL)

    pw_el = _find_input(page, _PASSWORD_SELECTORS, "senha", "/var/www/axen/debug_nuvemshop_login.png")
    pw_el.fill(PASSWORD)

    page.click('button[type="submit"], input[type="submit"]')

    # Aguarda redirect para o admin da loja
    page.wait_for_url(f"**{store_host}/admin/**", timeout=30_000)
    log.info("[Nuvemshop] Login OK — %s", page.url)


# ── Orders scraping ────────────────────────────────────────────────────────────

def _scrape_orders(page, days: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()

    page.goto(f"{STORE_URL}/admin/orders", wait_until="networkidle", timeout=30_000)

    orders: list[dict] = []
    pg = 1

    while True:
        log.info("[Nuvemshop] Extraindo pedidos — pág %d", pg)

        # Aguarda tabela ou lista de pedidos
        try:
            page.wait_for_selector(
                "table tbody tr, .order-list-item, [data-order-id], tr[data-id]",
                timeout=12_000,
            )
        except Exception:
            log.info("[Nuvemshop] Nenhum pedido na pág %d.", pg)
            break

        # Extrai dados via JS para lidar com qualquer estrutura de DOM
        raw_orders: list[dict] = page.evaluate("""
            () => {
                function text(el) { return el ? el.textContent.trim() : ''; }
                function attr(el, a) { return el ? (el.getAttribute(a) || '') : ''; }

                // Tenta linhas de tabela
                let rows = Array.from(document.querySelectorAll('table tbody tr'));

                // Fallback: cards de pedido
                if (!rows.length) {
                    rows = Array.from(document.querySelectorAll(
                        '.order-list-item, [data-order-id], tr[data-id]'
                    ));
                }

                return rows.map(row => {
                    const cells = row.querySelectorAll('td');

                    // Número do pedido
                    const orderIdEl = row.querySelector(
                        '[data-order-id], .order-number, .order-id, a[href*="/orders/"]'
                    );
                    let orderId = attr(orderIdEl, 'data-order-id')
                        || text(orderIdEl).replace(/[^0-9]/g, '')
                        || (cells[0] ? text(cells[0]).replace(/[^0-9]/g, '') : '');

                    // Se for link href="/admin/orders/123", extrai o ID
                    const href = attr(orderIdEl, 'href');
                    if (!orderId && href) {
                        const m = href.match(/\\/orders\\/(\\d+)/);
                        if (m) orderId = m[1];
                    }

                    // Cliente
                    const custEl = row.querySelector('.customer-name, .order-customer');
                    const customer = text(custEl) || (cells[1] ? text(cells[1]) : '');

                    // Total
                    const totalEl = row.querySelector('[class*="total"], .order-total');
                    const totalText = text(totalEl) || (cells[cells.length - 1] ? text(cells[cells.length - 1]) : '0');

                    // Data
                    const dateEl = row.querySelector('time, [datetime], .order-date');
                    const date = attr(dateEl, 'datetime') || text(dateEl)
                        || (cells[2] ? text(cells[2]) : '');

                    // Status
                    const statusEl = row.querySelector('.badge, [class*="status"], [class*="badge"]');
                    const status = text(statusEl);

                    return { orderId, customer, totalText, date, status };
                }).filter(o => o.orderId && o.orderId.length > 0);
            }
        """)

        if not raw_orders:
            log.info("[Nuvemshop] Nenhum dado extraído na pág %d.", pg)
            break

        stop = False
        for raw in raw_orders:
            parsed = _parse_order(raw, cutoff)
            if parsed is None:
                stop = True  # pedido mais antigo que cutoff → para paginação
                break
            if parsed:
                orders.append(parsed)

        if stop:
            break

        # Próxima página
        next_btn = page.query_selector(
            'a[rel="next"], .pagination-next a, [aria-label="Próxima"], '
            '[aria-label="Next"], .next-page a, li.next a'
        )
        if not next_btn:
            break

        next_btn.click()
        page.wait_for_load_state("networkidle", timeout=15_000)
        pg += 1

    return orders


# ── Analytics scraping ────────────────────────────────────────────────────────

def _scrape_analytics(page, days: int) -> list[dict]:
    """
    Navega para /admin/dashboard da Nuvemshop e extrai visitas diárias.

    Fluxo:
      1. Abre dashboard para confirmar autenticação
      2. Extrai os valores visíveis: Total de visitas, Carrinhos criados, Checkout
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    records: list[dict] = []

    # ── Passo 1: Dashboard ────────────────────────────────────────────────────
    store_host = STORE_URL.replace("https://", "").replace("http://", "")
    stats_url = f"{STORE_URL}/admin/dashboard"
    try:
        page.goto(stats_url, wait_until="networkidle", timeout=30_000)
    except Exception:
        try:
            page.goto(stats_url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            log.error("[Nuvemshop] Falha ao carregar stats: %s", e)
            return []

    page.screenshot(path="/var/www/axen/debug_nuvemshop_stats.png")
    log.info("[Nuvemshop] Stats URL: %s", page.url)

    # Detecta se /admin/dashboard redirecionou para login (requer sessão válida)
    try:
        _preview = page.evaluate("document.body.innerText.slice(0, 150)")
    except Exception:
        _preview = ""
    _on_login = (
        store_host not in page.url
        or any(kw in page.url.lower() for kw in ("login", "signin", "tiendanube"))
        or "Que bom ter você aqui" in _preview
    )
    if _on_login:
        log.info("[Nuvemshop] Analytics: login page detectada — autenticando... (%s)", page.url)
        page.screenshot(path="/var/www/axen/debug_nuvemshop_analytics_login_before.png")

        btn_found = False
        try:
            btn = page.get_by_text(re.compile(r"entrar com e-?mail", re.IGNORECASE))
            btn_count = btn.count()
            log.info("[Nuvemshop] Botão 'Entrar com e-mail' encontrado? count=%d", btn_count)
            if btn_count > 0:
                log.info("[Nuvemshop] Clicando em 'Entrar com e-mail'...")
                btn.first.click()
                page.wait_for_timeout(2_000)
                btn_found = True
                page.screenshot(path="/var/www/axen/debug_nuvemshop_analytics_login_after_click.png")
        except Exception as e:
            log.warning("[Nuvemshop] Erro ao procurar/clicar botão 'Entrar com e-mail': %s", e)

        if not btn_found:
            log.warning("[Nuvemshop] Botão 'Entrar com e-mail' não encontrado — tentando encontrar input diretamente")
            page.screenshot(path="/var/www/axen/debug_nuvemshop_analytics_login_no_btn.png")

        email_el = _find_input(page, _EMAIL_SELECTORS, "email", "/var/www/axen/debug_nuvemshop_login_analytics.png")
        email_el.fill(EMAIL)
        pw_el = _find_input(page, _PASSWORD_SELECTORS, "senha", "/var/www/axen/debug_nuvemshop_login_analytics.png")
        pw_el.fill(PASSWORD)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_url(f"**{store_host}/admin/**", timeout=30_000)
        log.info("[Nuvemshop] Login OK (analytics) — %s", page.url)
        page.goto(stats_url, wait_until="networkidle", timeout=30_000)
        log.info("[Nuvemshop] Stats URL (pós-login): %s", page.url)

    # ── Passo 2: Aguarda conteúdo dinâmico e extrai via innerText ─────────────
    try:
        # Aguarda o conteúdo renderizar no dashboard
        try:
            page.wait_for_selector(
                "text=Total de visitas, text=visitas",
                timeout=12_000,
            )
        except Exception:
            page.wait_for_timeout(4_000)  # fallback: aguarda 4s

        # inner_text() retorna o texto visível como renderizado — funciona com SVG e React
        body_text = page.inner_text("body")
        log.info("[Nuvemshop] Body text (300 chars): %s", body_text[:300].replace("\n", " | "))

        lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]

        def find_num_after(label: str) -> int | None:
            lc = label.lower()
            for i, line in enumerate(lines):
                if lc in line.lower():
                    # O número pode estar na mesma linha ou na linha seguinte
                    for src in [line, lines[i + 1] if i + 1 < len(lines) else ""]:
                        m = re.search(r"\b(\d+)\b", src)
                        if m:
                            return int(m.group(1))
            return None

        visits           = find_num_after("total de visitas") or find_num_after("visitas")
        add_to_cart      = find_num_after("carrinhos criados")
        reached_checkout = find_num_after("checkout iniciado")
        purchased        = find_num_after("vendas")

        log.info(
            "[Nuvemshop] Extraído — visitas=%s cart=%s checkout=%s vendas=%s",
            visits, add_to_cart, reached_checkout, purchased,
        )

        if visits:
            records.append({
                "platform":         "nuvemshop",
                "source":           "loja",
                "date":             today,
                "visits":           visits,
                "add_to_cart":      add_to_cart,
                "reached_checkout": reached_checkout,
                "purchased":        purchased,
            })
        else:
            log.warning("[Nuvemshop] Nenhum dado de visitas encontrado na página.")

    except Exception as e:
        log.error("[Nuvemshop] Extração de analytics falhou: %s", e)

    return records


# ── Parsers ────────────────────────────────────────────────────────────────────

def _parse_order(raw: dict, cutoff) -> Optional[dict]:
    """
    Converte pedido bruto em dict compatível com upsert_sale().
    Retorna None se o pedido for anterior ao cutoff (sinal para parar paginação).
    Retorna {} se o pedido for inválido mas a paginação deve continuar.
    """
    order_id = str(raw.get("orderId", "")).strip().lstrip("#")
    if not order_id:
        return {}

    sold_at = _parse_date(raw.get("date", ""))
    if sold_at and sold_at.date() < cutoff:
        return None

    total = _parse_brl(raw.get("totalText", "0"))
    customer = raw.get("customer", "").strip()

    return {
        "platform":     "nuvemshop",
        "order_id":     order_id,
        "product_name": f"Pedido #{order_id}" + (f" — {customer}" if customer else ""),
        "unit_price":   total,
        "total_value":  total,
        "sold_at":      sold_at.isoformat() if sold_at else "",
        "quantity":     1,
    }


def _parse_date(text: str) -> Optional[datetime]:
    if not text:
        return None
    text = text.strip()
    FORMATS = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ]
    for fmt in FORMATS:
        try:
            dt = datetime.strptime(text[: len(fmt) + 6], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _parse_brl(text: str) -> float:
    cleaned = re.sub(r"[^\d,.]", "", text)
    if not cleaned:
        return 0.0
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0
