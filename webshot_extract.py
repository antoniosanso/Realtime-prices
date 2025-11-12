import argparse, os, re, csv, json, urllib.parse, datetime, sys
import pandas as pd
from tqdm import tqdm
from playwright.sync_api import sync_playwright
from zoneinfo import ZoneInfo  # per orario locale corretto (CET/CEST)

# Selettori ampliati per Investing (IT)
DEFAULT_RULES = {
    "it.investing.com": {
        "name": [
            "h1",
            "div.instrument-header h1",
            "div.float_lang_base_1 h1",
        ],
        "price": [
            "[data-test='instrument-price-last']",
            "div.instrument-price_instrument-price__3uw25 span",
            "span.text-2xl",
        ],
        # assoluta e percentuale hanno spesso data-test diversi
        "change_abs": [
            "[data-test='instrument-price-change']",
            "span.instrument-price_change__19cas",
            "span.bold.greenFont", "span.bold.redFont",
        ],
        "change_pct": [
            "[data-test='instrument-price-change-percent']",
            "span.instrument-price_change-percent__19cas",
            "span:has-text('%')",
        ],
        # non usiamo più datetime di pagina (richiesto di rimuoverlo)
    }
}

BLOCK_PATTERNS = [
    "googletagmanager.com", "doubleclick.net", "google-analytics.com",
    "facebook.net", "ads.", "/ads?", "/adserver", "scorecardresearch.com"
]

def parse_urls(path: str):
    urls = []
    if not os.path.exists(path):
        print(f"[ERROR] Input file not found: {path}", file=sys.stderr)
        return urls
    if path.endswith(".csv"):
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                u = (row.get("url") or "").strip()
                if u:
                    urls.append(row)
    else:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                u = line.strip()
                if u and not u.startswith("#"):
                    urls.append({"url": u})
    return urls

def sanitize(s: str) -> str:
    return re.sub(r"[^\w\.-]+", "_", s).strip("_")[:180]

def wait_get_text(page, selectors, wait_ms=8000):
    for sel in selectors:
        if not sel:
            continue
        try:
            page.wait_for_selector(sel, state="visible", timeout=wait_ms)
            txt = page.locator(sel).first.inner_text().strip()
            if txt:
                return txt
        except Exception:
            continue
    return ""

def clean_abs(s: str) -> str:
    """Normalizza la variazione assoluta es. '+12,53' o '-0,75'."""
    if not s:
        return s
    s = s.replace("(", "").replace(")", "").strip()
    s = re.sub(r"\s+", " ", s)
    m = re.search(r"[-+]?\d{1,3}(?:\.\d{3})*,\d{2}", s)
    if m:
        return m.group(0)
    m2 = re.search(r"\d+,\d+", s)
    return m2.group(0) if m2 else s

def clean_pct(s: str) -> str:
    """Normalizza la percentuale es. '+1,26%' o '-0,45%'."""
    if not s:
        return s
    s = s.replace("(", "").replace(")", "").strip()
    s = re.sub(r"\s+", " ", s)
    m = re.search(r"[-+]?\d+,\d+%", s)
    if m:
        return m.group(0)
    m2 = re.search(r"\d+,\d+%", s)
    return m2.group(0) if m2 else s

def fallback_from_fragment(url: str):
    """Se l'URL contiene un text fragment estrae prezzo e % (+1,23%)."""
    out = {"price": "", "change_pct": ""}
    frag = urllib.parse.urlparse(url).fragment
    if not frag:
        return out
    frag = urllib.parse.unquote(frag)
    m = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2})", frag)
    if m:
        out["price"] = m.group(1)
    m2 = re.search(r"\(([-+]?\d+,\d+%)\)", frag)
    if m2:
        out["change_pct"] = m2.group(1)
    return out

def page_scan_pct(page) -> str:
    """Ultimo tentativo: cerca una % nel testo della pagina."""
    try:
        txt = page.inner_text("body")[:200000]
        m = re.search(r"[-+]?\d+,\d+%", txt)
        if m:
            return m.group(0)
    except Exception:
        pass
    return ""

def main():
    ap = argparse.ArgumentParser(description="Screenshots + quote extraction")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--viewport", default="1366x768")
    ap.add_argument("--delay", type=int, default=1200)
    ap.add_argument("--timeout", type=int, default=120000)
    args = ap.parse_args()

    width, height = (int(x) for x in args.viewport.lower().split("x"))
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(args.out, ts)
    os.makedirs(out_dir, exist_ok=True)

    urls = parse_urls(args.input)
    with open(os.path.join(out_dir, "run_summary.txt"), "w", encoding="utf-8") as f:
        f.write(f"INPUT: {args.input}\nTOTAL_URLS: {len(urls)}\n")

    if not urls:
        with open(os.path.join(out_dir, "NO_DATA.txt"), "w", encoding="utf-8") as f:
            f.write("No URLs found\n")
        print("[WARN] No URLs found; created NO_DATA.txt")
        return

    # timezone locale (default Europe/Rome; override con env TIMEZONE)
    tz_name = os.environ.get("TIMEZONE", "Europe/Rome")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Europe/Rome")

    records = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
        context = browser.new_context(
            viewport={"width": width, "height": height},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36")
        )

        # Blocca risorse pesanti/ads
        def route_handler(route):
            url = route.request.url
            if any(pat in url for pat in BLOCK_PATTERNS):
                return route.abort()
            return route.continue_()
        context.route("**/*", route_handler)

        page = context.new_page()

        for row in tqdm(urls, desc="Process URLs"):
            url = (row.get("url") or "").strip()
            if not url:
                continue
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc.lower()
            rules = DEFAULT_RULES.get(domain, {})

            try:
                page.goto(url, timeout=args.timeout, wait_until="domcontentloaded")
            except Exception as e:
                with open(os.path.join(out_dir, f"ERROR_NAV_{sanitize(url)}.txt"), "w", encoding="utf-8") as ef:
                    ef.write(str(e))
                continue

            # Cookie wall (best effort)
            for sel in ["button:has-text('Accept')","button:has-text('Accetta')",
                        "[id*='onetrust-accept']","[aria-label*='accept']"]:
                try:
                    page.locator(sel).first.click(timeout=700)
                    break
                except Exception:
                    pass

            if args.delay:
                page.wait_for_timeout(args.delay)

            name = wait_get_text(page, [row.get("name_sel","")] + rules.get("name", []))
            price = wait_get_text(page, [row.get("price_sel","")] + rules.get("price", []))
            ch_abs_raw = wait_get_text(page, [row.get("change_abs_sel","")] + rules.get("change_abs", []))
            ch_pct_raw = wait_get_text(page, [row.get("change_pct_sel","")] + rules.get("change_pct", []))

            # Fallback da frammento URL (solo % e prezzo)
            if (not price) or (not ch_pct_raw):
                frag_vals = fallback_from_fragment(url)
                price = price or frag_vals.get("price","")
                ch_pct_raw = ch_pct_raw or frag_vals.get("change_pct","")

            # Fallback scan del body per la %
            if not ch_pct_raw:
                ch_pct_raw = page_scan_pct(page)

            change_abs = clean_abs(ch_abs_raw)
            change_pct = clean_pct(ch_pct_raw)

            # Screenshot sempre
            shot_name = sanitize(f"{domain}_{parsed.path}") + ".png"
            try:
                page.screenshot(path=os.path.join(out_dir, shot_name), full_page=True)
            except Exception as e:
                with open(os.path.join(out_dir, f"ERROR_SHOT_{sanitize(url)}.txt"), "w", encoding="utf-8") as ef:
                    ef.write(str(e))

            # Ora locale corretta (CET/CEST)
            captured_at_local = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

            records.append({
                "source": domain,
                "url": url,
                "name": name,
                "price": price,
                "change_abs": change_abs,     # valore assoluto
                "change_pct": change_pct,     # percentuale
                "captured_at_local": captured_at_local,
            })

        browser.close()

    df = pd.DataFrame.from_records(records)
    df.to_csv(os.path.join(out_dir, "quotes.csv"), index=False)
    with open(os.path.join(out_dir, "quotes.json"), "w", encoding="utf-8") as jf:
        json.dump(records, jf, ensure_ascii=False, indent=2)

    print(f"[OK] Outputs -> {out_dir}")

if __name__ == "__main__":
    main()
