# step3_row_sample.py — paginate all pages, gather filtered rows
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

PAGER_NEXT_SELECTORS = [
    ".x-tbar-page-next",                                       # Ext3
    "a.x-btn:has(.x-tbar-page-next)",                          # Ext3 variant
    "button[title='Next Page']",
    "a[aria-label='Next Page']",
    "button[aria-label='Next']",
    "a:has-text('Next')",
    "button:has-text('Next')",
    ".x-toolbar .x-btn:has(.x-tbar-page-next)",
]

PAGER_TEXT_SELECTORS = [
    ".x-toolbar .x-toolbar-text",              # often contains "Page 1 of 12"
    ".x-tbar-page-number"                      # page input near "of N"
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
        try:
            text = (a.inner_text() or "").strip()
        except Exception:
            text = ""
        href = normalize_pw_href(a.get_attribute("href") or "")
        return text, href or ""
    try:
        return (cell.inner_text() or "").strip(), ""
    except Exception:
        return "", ""

def is_occupied(text):
    t = (text or "").strip().lower()
    return "occupied" in t  # matches "occupied - renewal", etc.

def county_ok(text):
    t = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return ("alameda" in t) or ("contra costa" in t)

def _rows_locator(ctx):
    for sel in ROW_SELECTORS:
        loc = ctx.locator(sel)
        try:
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None

def load_all_rows(ctx, rows_loc, max_scrolls=200, idle_rounds=3):
    """Scroll grid container to render all virtualized rows on the current page."""
    scroller = None
    for sel in SCROLLER_SELECTORS:
        s = ctx.locator(sel).first
        try:
            if s and s.count():
                scroller = s
                break
        except Exception:
            continue
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
        try:
            scroller.evaluate("(el) => el.scrollTop = el.scrollHeight")
        except Exception:
            try:
                ctx.keyboard.press("End")
            except Exception:
                pass
        time.sleep(0.25)
    time.sleep(0.3)

def _text_or_empty(loc):
    try:
        return (loc.inner_text(timeout=1000) or "").strip()
    except Exception:
        return ""

def _pager_disabled(btn) -> bool:
    try:
        cls = (btn.get_attribute("class") or "").lower()
        aria = (btn.get_attribute("aria-disabled") or "").lower()
        dis = (btn.get_attribute("disabled") is not None)
        return "x-item-disabled" in cls or "x-btn-disabled" in cls or aria == "true" or dis
    except Exception:
        return False

def click_next_page(ctx) -> bool:
    """
    Clicks 'Next' in the grid pager. Returns True if the page changed, else False (end).
    We detect change by watching first-row signature or overall count change.
    """
    # locate next button
    btn = None
    for sel in PAGER_NEXT_SELECTORS:
        cand = ctx.locator(sel).first
        try:
            if cand and cand.count():
                btn = cand
                break
        except Exception:
            continue
    if not btn:
        logger.debug("No pager 'Next' button found.")
        return False
    if _pager_disabled(btn):
        logger.debug("Pager next appears disabled; end of pages.")
        return False

    rows_before = _rows_locator(ctx)
    sig_before = ""
    cnt_before = 0
    if rows_before:
        try:
            cnt_before = rows_before.count()
            if cnt_before > 0:
                sig_before = _text_or_empty(rows_before.first)
        except Exception:
            pass

    # click and wait for change
    try:
        btn.click()
    except Exception:
        return False

    # wait for rows to change (ajax render)
    for _ in range(40):  # ~6–8s total
        time.sleep(0.2)
        rows_after = _rows_locator(ctx)
        if not rows_after:
            continue
        try:
            cnt_after = rows_after.count()
            sig_after = _text_or_empty(rows_after.first) if cnt_after > 0 else ""
        except Exception:
            continue
        if cnt_after != cnt_before or sig_after != sig_before:
            return True
    # no change detected → assume last page
    return False

def process_current_page(ctx, headers, col_status, col_county, col_unit, col_portfolio, col_lease):
    """Return list of filtered rows from the CURRENT page (after virtual scroll)."""
    out = []

    rows_loc = _rows_locator(ctx)
    if not rows_loc:
        return out

    # load all virtualized rows within this page
    load_all_rows(ctx, rows_loc)

    try:
        total = rows_loc.count()
    except Exception:
        total = 0

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

        out.append({
            "unit_name": unit_name,
            "unit_href": unit_href,
            "portfolio_name": portfolio_name,
            "portfolio_href": portfolio_href,
            "lease_name": lease_name,
            "lease_href": lease_href,
            "status": "Occupied",
            "building_county": ct,
        })

    return out

def main():
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    status = {
        "step": "row_sample_all_pages",
        "ok": False,
        "pages_processed": 0,
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

            # if report needs "Run/Apply", try it
            for sel in ['button:has-text("Run")', 'button:has-text("Apply")',
                        'input[value="Run"]', 'input[value="Apply"]']:
                try:
                    if page.locator(sel).first.count():
                        page.click(sel)
                        break
                except Exception:
                    continue

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

            out_rows = []
            seen_leases = set()

            # process page 1 .. N
            pages = 0
            while True:
                pages += 1
                page_rows = process_current_page(ctx, headers, col_status, col_county, col_unit, col_portfolio, col_lease)
                # de-dup by lease_href
                for r in page_rows:
                    key = r.get("lease_href") or (r.get("unit_href") + "|" + r.get("portfolio_href"))
                    if key and key in seen_leases:
                        continue
                    seen_leases.add(key)
                    out_rows.append(r)

                # try to go next; stop if not possible
                if not click_next_page(ctx):
                    break

            # write csv
            with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=[
                    "unit_name","unit_href",
                    "portfolio_name","portfolio_href",
                    "lease_name","lease_href",
                    "status","building_county",
                ])
                w.writeheader()
                w.writerows(out_rows)

            status["pages_processed"] = pages
            status["rows_out"] = len(out_rows)
            status["ok"] = True

        except Exception as e:
            status["error"] = str(e)
            logger.exception(f"step3 error: {e}")
        finally:
            context.close()

    print(status)

if __name__ == "__main__":
    main()
