# step3_row_sample.py  — loads ALL rows from virtualized grid
from pathlib import Path
from loguru import logger
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from pw_common import (
    make_context, login, PW_LIST_URL,
    find_grid_context, map_headers, normalize_pw_href
)
import csv, re, time

DEBUG_DIR = Path("data/debug")
OUT_CSV = DEBUG_DIR / "rows_sample.csv"
SHOT = DEBUG_DIR / "step3_rows.png"

ROW_SELECTORS = [
    ".x-grid3-body .x-grid3-row",          # ExtJS 3
    ".x-grid-view .x-grid-item",           # ExtJS 4+
    "table tbody tr",                      # fallback
]

SCROLLER_SELECTORS = [
    ".x-grid3-scroller",
    ".x-grid-view .x-grid-view-scroller",
    ".x-grid3-body",
    ".x-grid-view",
]

COL_CANDIDATES = {
    "status": ["status", "lease status", "tenant status"],
    "building_county": ["building county", "county", "building county name"],
    "unit": ["unit", "unit name", "property", "property/unit", "unit/property"],
    "portfolio": ["portfolio", "portfolio name"],
    "lease": ["lease", "lease name", "tenant", "tenant/lease"],
}

def pick_index(headers, candidates):
    for c in candidates:
        k = c.strip().lower()
        if k in headers:
            return headers[k]
    return None

def get_cell(row, idx):
    if idx is None:
        return None
    try:
        tds = row.locator("td")
        if tds.count() > idx:
            return tds.nth(idx)
    except Exception:
        pass
    return None

def extract_link_text_href(cell):
    if cell is None:
        return "", ""
    a = cell.locator("a").first
    if a.count():
        text = (a.inner_text() or "").strip()
        href = normalize_pw_href(a.get_attribute("href") or "")
        return text, href or ""
    return (cell.inner_text() or "").strip(), ""

def is_occupied(text):
    t = (text or "").strip().lower()
    return "occupied" in t  # matches "occupied - renewal", etc.

def county_ok(text):
    t = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return ("alameda" in t) or ("contra costa" in t)

def load_all_rows(ctx, rows_loc, max_scrolls=200, idle_rounds=3):
    """
    Scroll the grid container to force virtualized rows to render.
    Stops after 'idle_rounds' consecutive scrolls where count doesn't grow,
    or after 'max_scrolls'.
    """
    scroller = None
    for sel in SCROLLER_SELECTORS:
        s = ctx.locator(sel).first
        if s and s.count():
            scroller = s
            break
    if not scroller:
        return

    same_count_rounds = 0
    last = 0
    for _ in range(max_scrolls):
        try:
            count = rows_loc.count()
        except Exception:
            break
        if count == last:
            same_count_rounds += 1
        else:
            same_count_rounds = 0
        if same_count_rounds >= idle_rounds:
            break
        last = count
        # scroll to bottom
        try:
            scroller.evaluate("(el) => el.scrollTop = el.scrollHeight")
        except Exception:
            # fallback: press End
            try:
                ctx.keyboard.press("End")
            except Exception:
                pass
        time.sleep(0.25)  # small settle
    # one final settle
    time.sleep(0.3)

def main():
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    status = {
        "step": "row_sample_all",
        "ok": False,
        "rows_in_dom": 0,
        "rows_after_scroll": 0,
        "rows_out": 0,
        "csv": str(OUT_CSV),
        "error": None,
    }

    with sync_playwright() as p:
        context = make_context(p, headless=True, slow_mo=0)
        page = context.new_page()
        try:
            login(page)
            page.goto(PW_LIST_URL, wait_until="domcontentloaded", timeout=120_000)
            try:
                page.wait_for_load_state("networkidle", timeout=120_000)
            except PWTimeout:
                pass

            # if report needs "Run", try it
            for sel in ['button:has-text("Run")', 'button:has-text("Apply")', 'input[value="Run"]', 'input[value="Apply"]']:
                if page.locator(sel).first.count():
                    page.click(sel)
                    break

            ctx = find_grid_context(page)
            ctx.wait_for_selector(".x-grid3-header, .x-grid-header-ct, table thead", timeout=120_000)

            headers = map_headers(ctx)
            if not headers:
                raise RuntimeError("Headers not found")

            col_status = pick_index(headers, COL_CANDIDATES["status"])
            col_county = pick_index(headers, COL_CANDIDATES["building_county"])
            col_unit = pick_index(headers, COL_CANDIDATES["unit"])
            col_portfolio = pick_index(headers, COL_CANDIDATES["portfolio"])
            col_lease = pick_index(headers, COL_CANDIDATES["lease"])
            if any(x is None for x in (col_status, col_county, col_unit, col_portfolio, col_lease)):
                raise RuntimeError("Required columns missing")

            # rows before scroll (for telemetry)
            rows_loc = None
            for sel in ROW_SELECTORS:
                loc = ctx.locator(sel)
                if loc.count() > 0:
                    rows_loc = loc
                    break
            if not rows_loc:
                raise RuntimeError("No rows locator found")

            status["rows_in_dom"] = rows_loc.count()

            # load all virtualized rows
            load_all_rows(ctx, rows_loc)

            total = rows_loc.count()
            status["rows_after_scroll"] = total

            out_rows = []
            for i in range(total):
                row = rows_loc.nth(i)

                st = (get_cell(row, col_status).inner_text().strip()
                      if get_cell(row, col_status) else "")
                if not is_occupied(st):
                    continue

                ct = (get_cell(row, col_county).inner_text().strip()
                      if get_cell(row, col_county) else "")
                if not county_ok(ct):
                    continue

                unit_name, unit_href = extract_link_text_href(get_cell(row, col_unit))
                portfolio_name, portfolio_href = extract_link_text_href(get_cell(row, col_portfolio))
                lease_name, lease_href = extract_link_text_href(get_cell(row, col_lease))

                out_rows.append({
                    "unit_name": unit_name,
                    "unit_href": unit_href,
                    "portfolio_name": portfolio_name,
                    "portfolio_href": portfolio_href,
                    "lease_name": lease_name,
                    "lease_href": lease_href,
                    "status": "Occupied",
                    "building_county": ct,
                })

            with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=[
                    "unit_name","unit_href",
                    "portfolio_name","portfolio_href",
                    "lease_name","lease_href",
                    "status","building_county",
                ])
                w.writeheader()
                w.writerows(out_rows)

            status["rows_out"] = len(out_rows)
            status["ok"] = True

            try:
                ctx.screenshot(path=str(SHOT), full_page=True)
            except Exception:
                pass

        except Exception as e:
            status["error"] = str(e)
            logger.exception(f"step3 error: {e}")
        finally:
            context.close()

    print(status)

if __name__ == "__main__":
    main()
