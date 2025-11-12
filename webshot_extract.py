import argparse, os, re, csv, json, urllib.parse, datetime, sys
from typing import Dict
import pandas as pd
from tqdm import tqdm
from playwright.sync_api import sync_playwright

DEFAULT_RULES = {
    "it.investing.com": {
        "name": ["h1", "div.instrument-header h1", "div.float_lang_base_1 h1"],
        "price": ["[data-test='instrument-price-last']", "span.text-2xl"],
        "change": ["[data-test='instrument-price-change']", "span.bold.greenFont", "span.bold.redFont"],
        "datetime": ["time[data-test='instrument-price-last-update-time']"]
    }
}

def parse_urls(path: str):
    urls = []
    if not os.path.exists(path):
        return urls
    if path.endswith(".csv"):
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("url"):
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

def try_select(page, selectors):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count():
                txt = loc.first.inner_text().strip()
                if txt:
                    return txt
        except Exception:
            pass
    return ""

def try_text_fragment(url: str):
    out = {"price": "", "change": ""}
    frag = urllib.parse.urlparse(url).fragment
    if not frag:
        return out
    frag = urllib.parse.unquote(frag)
    m = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2})", frag)
    if m:
        out["price"] = m.group(1)
    m2 = re.search(r"\(([-+]\d+,\d+%)\)", frag)
    if m2:
        out["change"] = m2.group(1)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--viewport", default="1366x768")
    ap.add_argument("--delay", type=int, default=2000)
    ap.add_argument("--timeout", type=int, default=50000)
    args = ap.parse_args()

    width, height = (int(x) for x in args.viewport.lower().split("x"))
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(args.out, ts)
    os.makedirs(out_dir, exist_ok=True)

    urls = parse_urls(args.input)

    # Log summary
    with open(os.path.join(out_dir, "run_summary.txt"), "w", encoding="utf-8") as f:
        f.write(f"INPUT: {args.input}\nTOTAL_URLS: {len(urls)}\n")

    if not urls:
        with open(os.path.join(out_dir, "NO_DATA.txt"), "w", encoding="utf-8") as f:
            f.write("No URLs in input file\n")
        return

    records = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": width, "height": height}, user_agent="Mozilla/5.0 (WebshotBot/1.0)")
        page = context.new_page()

        for row in tqdm(urls, desc="Process URLs"):
            url = row.get("url")
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc.lower()

            try:
                page.goto(url, timeout=args.timeout, wait_until="domcontentloaded")
            except Exception as e:
                with open(os.path.join(out_dir, f"ERROR_NAV_{sanitize(url)}.txt"), "w", encoding="utf-8") as ef:
                    ef.write(str(e))
                continue

            # best-effort cookie close
            for sel in ["button:has-text('Accept')","button:has-text('Accetta')","[id*='onetrust-accept']"]:
                try:
                    loc = page.locator(sel)
                    if loc.count():
                        loc.first.click(timeout=800)
                        break
                except Exception:
                    pass

            page.wait_for_load_state("networkidle", timeout=args.timeout)
            if args.delay: page.wait_for_timeout(args.delay)

            rules = DEFAULT_RULES.get(domain, {})
            name = try_select(page, [row.get("name_sel","")]) or try_select(page, rules.get("name", []))
            price = try_select(page, [row.get("price_sel","")]) or try_select(page, rules.get("price", []))
            change = try_select(page, [row.get("change_sel","")]) or try_select(page, rules.get("change", []))
            dt_str = try_select(page, [row.get("datetime_sel","")]) or try_select(page, rules.get("datetime", []))

            if not price or not change:
                frag = try_text_fragment(url)
                price = price or frag.get("price","")
                change = change or frag.get("change","")

            shot_name = sanitize(f"{domain}_{parsed.path}") + ".png"
            page.screenshot(path=os.path.join(out_dir, shot_name), full_page=True)

            records.append({"source": domain, "url": url, "name": name, "price": price, "change_pct": change, "datetime_str": dt_str})

        browser.close()

    import json
    import pandas as pd
    df = pd.DataFrame.from_records(records)
    df.to_csv(os.path.join(out_dir, "quotes.csv"), index=False)
    with open(os.path.join(out_dir, "quotes.json"), "w", encoding="utf-8") as jf:
        json.dump(records, jf, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
