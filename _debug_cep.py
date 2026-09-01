"""
Script de diagnóstico para CEP playwright.
Abre cada loja, percorre até MAX_URLS_PER_STORE URLs por loja até encontrar
um produto em estoque, testa o fluxo de CEP e imprime o resultado.

Uso:
    python _debug_cep.py
"""

from playwright.sync_api import sync_playwright
import re, csv

CEP = "88065185"
MAX_URLS_PER_STORE = 25   # quantas URLs tentar por loja antes de desistir

# ── Lojas que usam CEP via Playwright ────────────────────────────────────────
TARGET_STORES = {"Papachulli", "W. Buscatti", "4Men"}

# ── Carrega URLs do CSV ───────────────────────────────────────────────────────
def load_urls_by_store(max_per_store: int = MAX_URLS_PER_STORE) -> dict[str, list[str]]:
    """Lê axen_competitive_prices.csv e devolve até max_per_store URLs por loja."""
    urls: dict[str, list[str]] = {s: [] for s in TARGET_STORES}
    try:
        with open("axen_competitive_prices.csv", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                store = row.get("Loja", "").strip()
                url   = row.get("URL",  "").strip()
                if store in urls and url and len(urls[store]) < max_per_store:
                    urls[store].append(url)
    except FileNotFoundError:
        print("axen_competitive_prices.csv não encontrado — use URLs manuais.")
    return urls

STORE_URLS = load_urls_by_store()

# Forçar URLs manuais (descomente se quiser testar URL específica primeiro):
# STORE_URLS["4Men"].insert(0, "https://4men.com.br/pulseira-masculina-de-couro-califa-caramelo-ajustavel/")

print("\n=== URLs que serão testadas ===")
for store, urls in STORE_URLS.items():
    print(f"  {store}: {len(urls)} URL(s)")
    for u in urls[:5]:
        print(f"      {u}")
    if len(urls) > 5:
        print(f"      ... e mais {len(urls)-5}")
print()

# ── Seletores ─────────────────────────────────────────────────────────────────
SIZE_LABEL_SELECTORS = [
    "button:text-is('G')", "button:text-is('M')", "button:text-is('P')",
    "a:text-is('G')",      "a:text-is('M')",      "a:text-is('P')",
    "label:text-is('G')",  "label:text-is('M')",  "label:text-is('P')",
    "[data-value='G']",    "[data-value='M']",    "[data-value='P']",
    "li.variable-item:text-is('G')", "li.variable-item:text-is('M')",
    ".swatch-label:text-is('G')",    ".swatch-label:text-is('M')",
]
SIZE_SELECT_SELECTORS = [
    "select[name*='tamanho']",
    "select[name*='size']",
    "select[id*='tamanho']",
    "select[id*='size']",
    "select.variation-select",
    ".variations select",
    "select[data-attribute_name]",
    "select[id*='attribute']",
    "select[name*='attribute']",
    "select[name^='atributo']",
    "select[name^='opcao']",
    "select[id^='atributo']",
    # Papachulli (Tray)
    "#variation_first_select",
    "select[id*='variation']",
    "select.product-form__select",
    "select[data-option-index]",
    "form.product-form select",
    "form.cart select",
    "#product-form select",
]
BUTTON_CONTAINERS = [
    ".product-options-list", ".product-option-list", ".attribute-options",
    ".variant-options", ".variable-items-wrapper", ".product-variants",
    ".size-selector", "[class*='size-option']", "[class*='variacao']",
    "[class*='variation']",
]
CEP_SELECTORS = [
    "input[id='cep']",
    "input[name='cep']",
    "input[id*='cep']",
    "input[name*='cep']",
    "input[name='zip']",
    "input[id='zip']",
    "input[placeholder*='CEP']",
    "input[placeholder*='cep']",
    "input.input-cep",
    "#calcular-frete input",
]
CALC_SELECTORS = [
    "button:has-text('Calcular')",
    "button:has-text('CALCULAR')",
    "input[value='Calcular']",
    "input[value='IR']",
    "button:has-text('IR')",
    "a:has-text('Calcular')",
    "button[id*='calcular']",
]


# ── Preenchimento robusto de CEP ──────────────────────────────────────────────
def fill_cep_robust(page, cep_digits: str) -> tuple[bool, str]:
    """
    Tenta preencher o campo CEP com 3 estratégias.
    Retorna (True, método_usado) ou (False, mensagem_de_erro).
    """
    for sel in CEP_SELECTORS:
        try:
            field = page.locator(sel).first
            if not field.is_visible(timeout=700):
                continue

            field.scroll_into_view_if_needed(timeout=1000)
            field.click()
            field.triple_click()

            # Tentativa 1 — fill() nativo
            try:
                field.fill(cep_digits)
                val = field.input_value(timeout=500)
                if re.sub(r"\D", "", val) == cep_digits:
                    return True, f"fill() via {sel!r}"
            except Exception as e1:
                pass

            # Tentativa 2 — press_sequentially (simula teclado real)
            try:
                field.triple_click()
                field.press("Control+a")
                field.press("Delete")
                field.press_sequentially(cep_digits, delay=40)
                val = field.input_value(timeout=500)
                if re.sub(r"\D", "", val) == cep_digits:
                    return True, f"press_sequentially() via {sel!r}"
            except Exception as e2:
                pass

            # Tentativa 3 — JavaScript com eventos sintéticos React/Vue
            try:
                js_sel = sel.replace("'", "\\'")
                page.evaluate(f"""() => {{
                    const el = document.querySelector('{js_sel}');
                    if (!el) return;
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, '{cep_digits}');
                    el.dispatchEvent(new Event('input',  {{bubbles:true}}));
                    el.dispatchEvent(new Event('change', {{bubbles:true}}));
                }}""")
                val = field.input_value(timeout=500)
                if re.sub(r"\D", "", val) == cep_digits:
                    return True, f"JavaScript via {sel!r}"
                else:
                    return False, f"JS falhou: valor ficou '{val}' (sel={sel!r})"
            except Exception as e3:
                return False, f"todas as tentativas falharam em {sel!r}: e3={e3}"

        except Exception as outer:
            continue

    return False, "campo CEP não encontrado por nenhum seletor"


# ── Detecção de estoque ───────────────────────────────────────────────────────
def is_out_of_stock(page) -> tuple[bool, str]:
    try:
        if page.locator(".stock.out-of-stock").first.is_visible(timeout=400):
            return True, ".stock.out-of-stock visível"
    except Exception:
        pass

    try:
        cart_form = page.locator("form.cart, form#product-form, .product-form")
        if cart_form.first.is_visible(timeout=400):
            disabled = cart_form.first.locator(
                "button.disabled, button[disabled], "
                ".single_add_to_cart_button.disabled, "
                ".disabled.add_to_cart_button"
            )
            if disabled.first.is_visible(timeout=300):
                return True, "botão compra desabilitado no form"
    except Exception:
        pass

    try:
        stock_el = page.locator("p.stock, span.stock")
        if stock_el.first.is_visible(timeout=300):
            stock_text = stock_el.first.inner_text().lower()
            OOS_WORDS = ["esgotado", "esgotada", "fora de estoque", "out of stock",
                         "indisponível", "indisponivel", "sem estoque"]
            for w in OOS_WORDS:
                if w in stock_text:
                    return True, f"p.stock contém '{w}'"
    except Exception:
        pass

    try:
        text = page.inner_text("body").lower()
    except Exception:
        return False, ""

    OOS_SIGNALS = [
        "fora de estoque", "out of stock", "esgotado", "esgotada",
        "indisponível", "indisponivel", "sem estoque",
    ]
    found_oos = next((s for s in OOS_SIGNALS if s in text), None)
    if found_oos:
        try:
            active_btn = page.locator(
                "form.cart button:not([disabled]):not(.disabled), "
                "form#product-form button[type='submit']:not([disabled]):not(.disabled), "
                ".single_add_to_cart_button:not(.disabled):not([disabled])"
            ).first
            if active_btn.is_visible(timeout=400):
                return False, ""
        except Exception:
            pass
        return True, f"body contém '{found_oos}' + sem botão ativo no form"

    return False, ""


# ── Probe de uma única URL ────────────────────────────────────────────────────
def probe_url(page, store: str, url: str, url_idx: int, total: int) -> bool:
    print(f"\n{'─'*60}")
    print(f"  [{url_idx}/{total}] LOJA: {store}")
    print(f"  URL: {url}")
    print(f"{'─'*60}")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
    except Exception as e:
        print(f"  ✗ Erro ao carregar página: {e}")
        return False

    # ── Verificação de estoque ────────────────────────────────────────────
    oos, oos_reason = is_out_of_stock(page)
    if oos:
        print(f"  ⚠ FORA DE ESTOQUE ({oos_reason}) — pulando")
        return False
    print("  ✓ Produto parece em estoque")

    # ── 1. Tamanho — botões/links ─────────────────────────────────────────
    print("\n[1] SELETORES DE TAMANHO (botão/link/label):")
    found_size_info = False
    for sel in SIZE_LABEL_SELECTORS:
        try:
            els = page.locator(sel).all()
            if els:
                texts = [e.inner_text()[:20] for e in els[:4]]
                print(f"  ✓ {sel!r}  →  {texts}")
                found_size_info = True
        except Exception:
            pass

    print("\n[1b] SELETORES DE TAMANHO (<select>):")
    for sel in SIZE_SELECT_SELECTORS:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=500):
                opts = el.locator("option").all()
                values = [o.get_attribute("value") for o in opts[:8]]
                texts  = [o.inner_text()[:20] for o in opts[:8]]
                print(f"  ✓ {sel!r}  → values={values}  texts={texts}")
                found_size_info = True
        except Exception:
            pass

    if not found_size_info:
        print("  ✗ nenhum select/botão de tamanho detectado")
        try:
            all_selects = page.evaluate("""() => {
                return [...document.querySelectorAll('select')].map(s => ({
                    name: s.name, id: s.id,
                    opts: [...s.options].slice(0,6).map(o => o.value + '/' + o.text)
                }));
            }""")
            if all_selects:
                print("  ℹ todos os selects na página:")
                for s in all_selects:
                    print(f"      name={s['name']!r} id={s['id']!r} opts={s['opts']}")
        except Exception:
            pass

    # ── 2. Campo CEP ──────────────────────────────────────────────────────
    print("\n[2] CAMPO CEP:")
    found_cep = False
    for sel in CEP_SELECTORS:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=500):
                pid = el.get_attribute("id")
                pname = el.get_attribute("name")
                ppholder = el.get_attribute("placeholder")
                print(f"  ✓ {sel!r}  id={pid!r} name={pname!r} placeholder={ppholder!r}")
                found_cep = True
        except Exception:
            pass

    if not found_cep:
        print("  ✗ campo CEP não detectado")
        try:
            all_inputs = page.evaluate("""() => {
                return [...document.querySelectorAll('input')].map(i => ({
                    id: i.id, name: i.name, placeholder: i.placeholder, type: i.type
                })).slice(0, 15);
            }""")
            print("  ℹ inputs na página:", all_inputs)
        except Exception:
            pass

    # ── 3. Botão calcular ─────────────────────────────────────────────────
    print("\n[3] BOTÃO CALCULAR:")
    found_btn = False
    for sel in CALC_SELECTORS:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=500):
                txt = el.inner_text()[:30]
                print(f"  ✓ {sel!r}  texto={txt!r}")
                found_btn = True
        except Exception:
            pass

    if not found_btn:
        print("  ✗ botão calcular não detectado")
        try:
            btns = page.evaluate("""() => {
                return [...document.querySelectorAll('button, input[type=button], input[type=submit], a.btn')].map(b => ({
                    tag: b.tagName, text: (b.innerText || b.value || '').trim().substring(0,30),
                    id: b.id, cls: b.className.substring(0,50)
                })).filter(b => b.text).slice(0, 15);
            }""")
            print("  ℹ botões na página:", btns)
        except Exception:
            pass

    # ── 4. Fluxo completo ─────────────────────────────────────────────────
    print("\n[4] FLUXO COMPLETO:")
    if not (found_cep and found_btn):
        print("  ✗ Fluxo não executado (CEP ou botão ausente)")
        input(f"\n  [PAUSA] Verifique o browser para {store}. Enter para continuar...")
        return False

    # Seleciona tamanho
    tamanho_selecionado = False

    # Camada 1: G/M/P por texto exato
    for size in ["G", "M", "P"]:
        for sel in [f"button:text-is('{size}')", f"a:text-is('{size}')",
                    f"label:text-is('{size}')", f"[data-value='{size}']"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=400):
                    el.click()
                    tamanho_selecionado = True
                    print(f"  ✓ Tamanho '{size}' clicado via {sel!r}")
                    break
            except Exception:
                pass
        if tamanho_selecionado:
            break

    # Camada 2: G/M/P em <select>
    if not tamanho_selecionado:
        for sel in SIZE_SELECT_SELECTORS:
            try:
                sel_el = page.locator(sel).first
                if sel_el.is_visible(timeout=400):
                    for size in ["G", "M", "P"]:
                        for method in ["value", "label"]:
                            try:
                                if method == "value":
                                    sel_el.select_option(value=size, timeout=500)
                                else:
                                    sel_el.select_option(label=size, timeout=500)
                                tamanho_selecionado = True
                                print(f"  ✓ Tamanho '{size}' via select {sel!r} ({method})")
                                break
                            except Exception:
                                pass
                        if tamanho_selecionado:
                            break
                    if tamanho_selecionado:
                        break
            except Exception:
                pass

    # Camada 3: primeira opção não-vazia em qualquer <select>
    if not tamanho_selecionado:
        for sel in SIZE_SELECT_SELECTORS:
            try:
                sel_el = page.locator(sel).first
                if sel_el.is_visible(timeout=400):
                    opts = page.evaluate(
                        """(s) => { const el = document.querySelector(s);
                           if(!el) return [];
                           return [...el.options].map(o => ({v: o.value, t: o.text.trim()})); }""",
                        sel,
                    )
                    SKIP = {"", "0", "escolha", "selecione", "choose", "select"}
                    for opt in opts:
                        v = opt.get("v", "").strip()
                        t = opt.get("t", "").strip()
                        if v and v.lower() not in SKIP:
                            try:
                                sel_el.select_option(value=v, timeout=500)
                                tamanho_selecionado = True
                                print(f"  ✓ Primeira opção value='{v}' text='{t}' via {sel!r}")
                                break
                            except Exception:
                                pass
                        if not tamanho_selecionado and t and t.lower() not in SKIP:
                            try:
                                sel_el.select_option(label=t, timeout=500)
                                tamanho_selecionado = True
                                print(f"  ✓ Primeira opção label='{t}' via {sel!r}")
                                break
                            except Exception:
                                pass
                    if tamanho_selecionado:
                        break
            except Exception:
                pass

    # Camada 4: primeiro elemento em containers de variação
    if not tamanho_selecionado:
        for container in BUTTON_CONTAINERS:
            try:
                el = page.locator(
                    f"{container} button, {container} a, "
                    f"{container} label, {container} li"
                ).first
                if el.is_visible(timeout=400):
                    txt = el.inner_text()[:20]
                    el.click()
                    tamanho_selecionado = True
                    print(f"  ✓ Primeiro elemento '{txt}' em container {container!r}")
                    break
            except Exception:
                pass

    if not tamanho_selecionado:
        print("  ⚠ nenhum tamanho selecionado")

    # Aguarda AJAX processar variação
    page.wait_for_timeout(1500)

    # Preenche CEP com estratégia robusta
    ok, method = fill_cep_robust(page, re.sub(r"\D", "", CEP))
    if ok:
        print(f"  ✓ CEP preenchido — {method}")
    else:
        print(f"  ✗ Falha ao preencher CEP: {method}")
        input(f"\n  [PAUSA] Verifique o browser para {store}. Enter para continuar...")
        return False

    # Clica calcular
    clicked_calc = False
    for sel in CALC_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=500):
                btn.click()
                print(f"  ✓ Calcular clicado via {sel!r}")
                clicked_calc = True
                break
        except Exception:
            pass

    if not clicked_calc:
        print("  ✗ Botão calcular não encontrado após preencher CEP")

    page.wait_for_timeout(4000)

    # Lê resultado
    all_text = page.inner_text("body")
    matches = re.findall(r"(\d+)\s*dias?\s*(úteis?|uteis?)?", all_text.lower())
    days_found = [(int(d), u) for d, u in matches if 1 <= int(d) <= 30]
    if days_found:
        best = min(days_found, key=lambda x: x[0])
        tipo = " úteis" if best[1] else ""
        print(f"  ✓ PRAZO ENCONTRADO: Entrega em {best[0]} dia{'s' if best[0]>1 else ''}{tipo}")
        input(f"\n  [PAUSA] Sucesso! Verifique o browser para {store}. Enter para continuar...")
        return True
    else:
        if "frete grátis" in all_text.lower() or "grátis" in all_text.lower():
            print("  ✓ FRETE GRÁTIS detectado")
            input(f"\n  [PAUSA] Sucesso (grátis)! Verifique o browser. Enter para continuar...")
            return True
        else:
            print("  ✗ Nenhum prazo detectado após calcular")
            lines = [l.strip() for l in all_text.splitlines() if l.strip() and
                     any(w in l.lower() for w in ["frete", "entrega", "prazo", "sedex", "pac", "loggi", "dias", "cep"])]
            print("  ℹ trechos relevantes:")
            for line in lines[:15]:
                print(f"      {line[:120]}")
            input(f"\n  [PAUSA] Verifique o browser para {store}. Enter para continuar...")
            return False


# ── Loop principal ────────────────────────────────────────────────────────────
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
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

    results: dict[str, str] = {}

    for store in TARGET_STORES:
        urls = STORE_URLS.get(store, [])
        if not urls:
            print(f"\n⚠ {store}: nenhuma URL disponível — pulando.")
            results[store] = "sem URLs"
            continue

        print(f"\n{'='*60}")
        print(f"  LOJA: {store}  ({len(urls)} URL(s) para testar)")
        print(f"{'='*60}")

        success = False
        skipped_oos = 0
        for i, url in enumerate(urls, 1):
            try:
                ok = probe_url(page, store, url, i, len(urls))
                if ok:
                    success = True
                    results[store] = f"✓ sucesso (URL {i}/{len(urls)})"
                    break
                else:
                    skipped_oos += 1
            except Exception as e:
                print(f"\n✗ Erro em {store} URL {i}: {e}")
                skipped_oos += 1

        if not success:
            results[store] = f"✗ falhou — {skipped_oos} URL(s) OOS/erro de {len(urls)}"

    browser.close()

print("\n" + "="*60)
print("  RESUMO FINAL")
print("="*60)
for store, status in results.items():
    print(f"  {store:20s}: {status}")
print("\n✓ Diagnóstico concluído.")
