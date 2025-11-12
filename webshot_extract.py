
import argparse, os, re, csv, json, urllib.parse, datetime
from typing import Dict, Optional
import pandas as pd
from tqdm import tqdm
from playwright.sync_api import sync_playwright

DEFAULT_RULES = {
    "it.investing.com": {
        "name": ["h1", "div.instrument-header h1", "div.float_lang_base_1 h1"],
        "price": [
            "span.text-2xl", 
            "[data-test='instrument-price-last']",
            "div.instrument-price_instrument-price__3uw25 span",
            "div.tradingViewHtml5 span",
            "div.price span"
        ],
        "change": [
            "[data-test='instrument-price-change']",
            "span.bold.greenFont", "span.bold.redFont",
            "span.instrument-price_change-percent__19cas"
        ],
        "datetime": [
            "time[data-test='instrument-price-last-update-time']",
            "div.u-text-left time"
        ]
    }
}

def parse_urls(path: str):
    urls = []
    if path.endswith(".csv"):
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                urls.append(row)
    else:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                u = line.strip()
                if u and not u.startswith("#"):
                    urls.append({"url": u})
    return urls

def sanitize(name: str) -> str:
    return re.sub(r"[^\w\.-]+", "_", name).strip("_")[:180]

def try_select(page, selectors):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                txt = loc.first.inner_text().strip()
                if txt:
                    return txt
        except Exception:
            pass
    return ""

def try_text_fragment(url: str) -> Dict[str, str]:
    out = {"price": "", "change": ""}
    frag = urllib.parse.urlparse(url).fragment
    if not frag:
        return out
    frag = urllib.parse.unquote(frag)
    # Look for patterns like 158,75 and (+1,01%)
    m = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2})", frag)
    if m:
        out["price"] = m.group(1)
    m2 = re.search(r"\(([-+]\d+,\d+%)\)", frag)
    if m2:
        out["change"] = m2.group(1)
    return out

def main():
    p = argparse.ArgumentParser(description="Screenshot + extract quotes")
    p.add_argument("--input", required=True, help="urls.csv (url,name_sel,price_sel,change_sel,datetime_sel) or urls.txt")
    p.add_argument("--out", required=True, help="output base dir")
    p.add_argument("--viewport", default="1366x768")
    p.add_argument("--delay", type=int, default=1500)
    p.add_argument("--timeout", type=int, default=45000)
    args = p.parse_args()

    width, height = (int(x) for x in args.viewport.lower().split("x"))
    ts_folder = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    base_out = os.path.join(args.out, ts_folder)
    os.makedirs(base_out, exist_ok=True)

    records = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()

        for row in tqdm(parse_urls(args.input), desc="Process URLs"):
            url = row.get("url") or row.get("URL") or row.get("link")
            if not url:
                continue
            parsed = urllib.parse.urlparse(url)
            dom = parsed.netloc.lower()
            rules = DEFAULT_RULES.get(dom, {})
            # navigation
            try:
                page.goto(url, timeout=args.timeout, wait_until="domcontentloaded")
            except Exception as e:
                error_path = os.path.join(base_out, f"ERROR_{sanitize(url)}.txt")
                with open(error_path, "w", encoding="utf-8") as ef:
                    ef.write(str(e))
                continue

            # cookie banners (best effort)
            for sel in ["button:has-text('Accept')","button:has-text('Accetta')","button:has-text('I agree')","[id*='onetrust-accept']",
                        "[aria-label*='accept']","[data-testid*='accept']"]:
                try:
                    loc = page.locator(sel)
                    if loc.count() and loc.first.is_visible():
                        loc.first.click(timeout=1000)
                        break
                except Exception:
                    pass

            page.wait_for_load_state("networkidle", timeout=args.timeout)
            if args.delay > 0:
                page.wait_for_timeout(args.delay)

            # extraction by explicit selectors in CSV (if provided)
            name_sel = (row.get("name_sel") or "").strip()
            price_sel = (row.get("price_sel") or "").strip()
            change_sel = (row.get("change_sel") or "").strip()
            datetime_sel = (row.get("datetime_sel") or "").strip()

            def get_text(sel_list, fallback_rules):
                if sel_list:
                    try:
                        loc = page.locator(sel_list)
                        if loc.count() and loc.first.is_visible():
                            return loc.first.inner_text().strip()
                    except Exception:
                        pass
                return try_select(page, fallback_rules) if fallback_rules else ""

            name = get_text(name_sel, rules.get("name", []))
            price = get_text(price_sel, rules.get("price", []))
            change = get_text(change_sel, rules.get("change", []))
            dt_txt = get_text(datetime_sel, rules.get("datetime", []))

            # fallback: extract from URL text fragment if empty
            if not price or not change:
                frag_vals = try_text_fragment(url)
                price = price or frag_vals.get("price","")
                change = change or frag_vals.get("change","")

            # final fallback: try to regex-scan whole page text lightly (last resort)
            if not price:
                try:
                    txt = page.inner_text("body")[:200000]
                    m = re.search(r"\b(\d{1,3}(?:\.\d{3})*,\d{2})\b", txt)
                    if m: price = m.group(1)
                except Exception:
                    pass

            # screenshot
            fname = sanitize(f"{dom}_{parsed.path}")
            shot_path = os.path.join(base_out, f"{fname}.png")
            page.screenshot(path=shot_path, full_page=True)

            records.append({
                "source": dom,
                "url": url,
                "name": name,
                "price": price,
                "change_pct": change,
                "datetime_str": dt_txt
            })

        browser.close()

    # Save table
    table_path = os.path.join(base_out, "quotes.csv")
    pd.DataFrame.from_records(records).to_csv(table_path, index=False)

    # Also JSON
    json_path = os.path.join(base_out, "quotes.json")
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(records, jf, ensure_ascii=False, indent=2)

    print(f"Saved to {base_out}")
