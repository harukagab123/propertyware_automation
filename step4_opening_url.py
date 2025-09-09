# step4_opening_url.py — robust nav + fast scraping + 10 qualifiers (no screenshots)
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from pw_common import make_context, login, label_value, safe_float
import csv, time

# ---------- paths ----------
DEBUG_DIR = Path("data/debug")
IN_CSV = DEBUG_DIR / "rows_sample.csv"   # from step3
OUT_CSV = DEBUG_DIR / "rows_step4.csv"

# ---------- knobs ----------
MAX_QUALIFIERS = 10            # only collect this many
HEADLESS = False               # headless is faster; set False if debugging
PAGE_TIMEOUT = 90_000          # ms; PW pages can be slow
NAV_WAIT_PRIMARY = "commit"    # try very early commit first
NAV_WAIT_FALLBACK = "domcontentloaded"
SHORT_TXT_TIMEOUT = 1500       # ms used in _safe_text
# ----------------------------

LEASE_TOTAL_UNPAID_LABELS = ["Total Unpaid", "Total unpaid", "TOTAL UNPAID", "Balance Due", "Unpaid Balance"]
UNIT_ADDRESS_LABELS = ["Unit Address", "Building Address", "Property Address", "Unit/Building Address"]

BLOCK_DOMAINS = (
    "google-analytics.com", "googletagmanager.com", "g.doubleclick.net",
    "segment.com", "cdn.segment.com", "mixpanel.com", "intercom.io",
    "fullstory.com", "hotjar.com", "optimizely.com", "facebook.net",
)

# ---------- network slimming ----------
def enable_fast_network(context):
    def _route(route):
        req = route.request
        rtype = req.resource_type
        url = req.url
        if rtype in ("image", "media", "font"):
            return route.abort()
        if any(d in url for d in BLOCK_DOMAINS):
            return route.abort()
        return route.continue_()
    context.route("**/*", _route)

# ---------- tiny utils ----------
def first_nonempty_label_value(page, labels):
    for lbl in labels:
        try:
            v = label_value(page, lbl)
            if v:
                return v.strip()
        except Exception:
            continue
    return ""

def _safe_text(loc, timeout=SHORT_TXT_TIMEOUT) -> str:
    try:
        if not loc or (hasattr(loc, "count") and loc.count() == 0):
            return ""
    except Exception:
        return ""
    try:
        return (loc.inner_text(timeout=timeout) or "").strip()
    except Exception:
        return ""

def re_login_if_logged_out(page):
    # Bounce pages often include /pw/logoff.do or login form
    url = (page.url or "").lower()
    if "logoff.do" in url or "login" in url:
        login(page)
        return True
    # also check for a visible login form
    try:
        if page.locator('input[name="username"], input[type="email"]').first.count():
            login(page)
            return True
    except Exception:
        pass
    return False

def smart_goto(page, url: str, max_tries=3):
    """
    Resilient navigation:
      1) Try wait_until='commit'
      2) If needed, wait for 'domcontentloaded'
      3) If bounced/logged out, re-login and retry
    """
    # Keep PW from opening a new tab
    try:
        page.add_init_script("window.open = (u) => { window.location.href = u; };")
    except Exception:
        pass

    for attempt in range(1, max_tries + 1):
        try:
            page.goto(url, wait_until=NAV_WAIT_PRIMARY, timeout=PAGE_TIMEOUT)
            # quick settle; if DOMContentLoaded never fires fast, we still proceed
            try:
                page.wait_for_load_state(NAV_WAIT_FALLBACK, timeout=min(15_000, PAGE_TIMEOUT))
            except PWTimeout:
                pass

            # if logged out, re-login and try again
            if re_login_if_logged_out(page):
                continue

            # small grace
            time.sleep(0.15)
            return True
        except PWTimeout:
            # try one fallback wait style before retrying
            try:
                page.goto(url, wait_until=NAV_WAIT_FALLBACK, timeout=PAGE_TIMEOUT)
                if re_login_if_logged_out(page):
                    continue
                time.sleep(0.15)
                return True
            except PWTimeout:
                if attempt == max_tries:
                    raise
                try:
                    login(page)
                except Exception:
                    pass
                time.sleep(0.25)
                continue
    return False

# ---------- scraping helpers ----------
def scrape_primary_contact_name_from_contacts_table(page) -> str:
    """
    Lease page → Contacts table → find Role == 'Primary', return Name (prefers link text).
    Index-safe and structure-agnostic.
    """
    candidates = [
        "xpath=//h2[normalize-space()='Contacts']/following::table[1]",
        "xpath=//h3[normalize-space()='Contacts']/following::table[1]",
        "xpath=//div[.//text()[normalize-space()='Contacts']]//table[1]",
        "xpath=//table[.//th[normalize-space()='Role'] and .//th[normalize-space()='Name']]",
        "#contactsTable",
    ]
    try:
        page.locator("text=Contacts").first.wait_for(timeout=1500)
    except Exception:
        pass

    for sel in candidates:
        tbl = page.locator(sel).first
        try:
            if not tbl or tbl.count() == 0:
                continue
        except Exception:
            continue

        rows = tbl.locator("tbody tr")
        if rows.count() == 0:
            rows = tbl.locator("tr[position()>1]")
        rc = rows.count()
        if rc == 0:
            continue

        ths = tbl.locator("thead th")
        if ths.count() == 0:
            ths = tbl.locator("tr").first.locator("th, td")

        header_map = {}
        for i in range(ths.count()):
            htxt = _safe_text(ths.nth(i)).lower()
            if htxt:
                header_map[htxt] = i

        role_idx = next((i for k, i in header_map.items() if k == "role" or k.startswith("role")), None)
        name_idx = next((i for k, i in header_map.items() if k == "name" or k.startswith("name")), None)

        # Pass 1: use explicit Role/Name columns if sane
        if role_idx is not None and name_idx is not None:
            for r in range(rc):
                row = rows.nth(r)
                tds = row.locator("td")
                td_count = tds.count()
                if td_count == 0 or role_idx >= td_count or name_idx >= td_count:
                    continue
                txt_role = _safe_text(tds.nth(role_idx)).lower()
                if "primary" in txt_role:
                    name_cell = tds.nth(name_idx)
                    a = name_cell.locator("a").first
                    return (_safe_text(a) or _safe_text(name_cell))

        # Pass 2: heuristic — any td says "Primary", then pick best name in that row
        for r in range(rc):
            row = rows.nth(r)
            role_td = row.locator(
                "xpath=.//td[normalize-space(translate(., 'PRIMARY', 'primary'))='primary' or "
                "contains(translate(normalize-space(.),'PRIMARY','primary'),'primary')]"
            ).first
            if role_td and role_td.count():
                a = row.locator('a[href*="/pw/contacts/contact_detail.do"]').first
                if a.count():
                    return _safe_text(a)
                a2 = row.locator("a").first
                if a2.count():
                    txt = _safe_text(a2)
                    if txt.lower() != "primary":
                        return txt
                tds = row.locator("td")
                texts = []
                for i in range(tds.count()):
                    t = _safe_text(tds.nth(i))
                    if t and t.lower() != "primary" and t != "\xa0":
                        texts.append(t)
                if texts:
                    texts.sort(key=len, reverse=True)
                    return texts[0]

    return ""

def scrape_unit_address(page) -> str:
    addr = first_nonempty_label_value(page, UNIT_ADDRESS_LABELS)
    if addr:
        return addr
    try:
        block = page.locator(
            "xpath=//*[contains(text(),'Address')]/following::*[self::div or self::span or self::td][1]"
        ).first
        if block and block.count():
            t = _safe_text(block)
            if t:
                return t
    except Exception:
        pass
    return ""

def scrape_second_owner_name(page) -> str:
    """
    Portfolio page Owners table:
      - Prefer #ownersTable (compact). If only ONE row → return that. If 2+ → return SECOND.
      - Fallback to headered variants similarly (2nd if present, else 1st).
    """
    try:
        tbl = page.locator("#ownersTable").first
        if tbl and tbl.count():
            rows = tbl.locator("tbody tr")
            rc = rows.count()
            if rc >= 1:
                row = rows.nth(1) if rc >= 2 else rows.nth(0)
                link = row.locator("td.moreInfo a").first
                if not link.count():
                    link = row.locator("a").first
                if link.count():
                    return _safe_text(link)
                tds = row.locator("td")
                texts = []
                for i in range(tds.count()):
                    t = _safe_text(tds.nth(i))
                    if t and t != "\xa0":
                        texts.append(t)
                if texts:
                    texts.sort(key=len, reverse=True)
                    return texts[0]
            return ""
    except Exception:
        pass

    # Headered/other skins
    tbl_candidates = [
        "xpath=//h2[contains(normalize-space(),'Owner')]/following::table[1]",
        "xpath=//h3[contains(normalize-space(),'Owner')]/following::table[1]",
        "xpath=//div[.//text()[contains(.,'Owner')]]//table[1]",
        "xpath=//table[.//th[normalize-space()='Name'] and .//th[contains(.,'Owner') or contains(.,'Contact')]]",
        "xpath=//table[.//th[normalize-space()='Name']]",
    ]
    for sel in tbl_candidates:
        tbl = page.locator(sel).first
        try:
            if not tbl or tbl.count() == 0:
                continue
        except Exception:
            continue

        rows = tbl.locator("tbody tr")
        if rows.count() == 0:
            rows = tbl.locator("tr[position()>1]")
        rc = rows.count()
        if rc == 0:
            continue

        row = rows.nth(1) if rc >= 2 else rows.nth(0)

        ths = tbl.locator("thead th")
        if ths.count() == 0:
            ths = tbl.locator("tr").first.locator("th, td")
        header_map = {}
        for i in range(ths.count()):
            htxt = _safe_text(ths.nth(i)).lower()
            if htxt:
                header_map[htxt] = i
        name_idx = next((i for k, i in header_map.items() if k == "name" or k.startswith("name")), None)

        if name_idx is not None:
            tds = row.locator("td")
            if name_idx < tds.count():
                name_cell = tds.nth(name_idx)
                a = name_cell.locator("a").first
                return (_safe_text(a) or _safe_text(name_cell))

        a = row.locator("a").first
        if a.count():
            return _safe_text(a)

        tds = row.locator("td")
        texts = []
        for i in range(tds.count()):
            t = _safe_text(tds.nth(i))
            if t and t != "\xa0":
                texts.append(t)
        if texts:
            texts.sort(key=len, reverse=True)
            return texts[0]

    return ""

# ---------- main ----------
def main():
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    status = {
        "step": "open_urls_total_unpaid_gt_1000_fast",
        "ok": False,
        "input_csv": str(IN_CSV),
        "output_csv": str(OUT_CSV),
        "rows_in": 0,
        "rows_written": 0,
        "skipped_total_unpaid_le_1000": 0,
        "errors": 0,
        "max_qualifiers": MAX_QUALIFIERS,
        "headless": HEADLESS,
    }

    if not IN_CSV.exists():
        print({**status, "error": f"Input not found: {IN_CSV}"})
        return

    with IN_CSV.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    status["rows_in"] = len(rows)

    out_fields = [
        "total_unpaid",
        "tenant_name",
        "unit_address",
        "owner_name",
        "lease_href",
        "unit_href",
        "portfolio_href",
        "lease_name",
    ]
    out_rows = []

    cache_unit_addr = {}
    cache_owner_name = {}

    with sync_playwright() as p:
        context = make_context(p, headless=HEADLESS, slow_mo=0)
        context.set_default_timeout(PAGE_TIMEOUT)
        enable_fast_network(context)

        page = context.new_page()
        try:
            login(page)

            for idx, r in enumerate(rows):
                if len(out_rows) >= MAX_QUALIFIERS:
                    break

                lease_url = (r.get("lease_href") or "").strip()
                unit_url = (r.get("unit_href") or "").strip()
                portfolio_url = (r.get("portfolio_href") or "").strip()
                if not lease_url:
                    continue

                try:
                    # Lease page
                    smart_goto(page, lease_url)

                    # quick probe for unpaid label
                    for lbl in LEASE_TOTAL_UNPAID_LABELS:
                        try:
                            page.locator(f"xpath=//*[normalize-space()='{lbl}']").first.wait_for(timeout=1500)
                            break
                        except Exception:
                            continue

                    unpaid_text = first_nonempty_label_value(page, LEASE_TOTAL_UNPAID_LABELS)
                    amount = safe_float(unpaid_text)
                    if amount <= 1000.0:
                        status["skipped_total_unpaid_le_1000"] += 1
                        continue

                    tenant_name = scrape_primary_contact_name_from_contacts_table(page).strip()

                    # Unit (cached)
                    unit_address = ""
                    if unit_url:
                        unit_address = cache_unit_addr.get(unit_url, "")
                        if not unit_address:
                            try:
                                smart_goto(page, unit_url)
                                unit_address = scrape_unit_address(page).strip()
                                cache_unit_addr[unit_url] = unit_address
                            except Exception:
                                pass

                    # Portfolio (cached)
                    owner_name = ""
                    if portfolio_url:
                        owner_name = cache_owner_name.get(portfolio_url, "")
                        if not owner_name:
                            try:
                                smart_goto(page, portfolio_url)
                                owner_name = scrape_second_owner_name(page).strip()
                                cache_owner_name[portfolio_url] = owner_name
                            except Exception:
                                pass

                    out_rows.append({
                        "total_unpaid": f"{amount:.2f}",
                        "tenant_name": tenant_name,
                        "unit_address": unit_address,
                        "owner_name": owner_name,
                        "lease_href": lease_url,
                        "unit_href": unit_url,
                        "portfolio_href": portfolio_url,
                        "lease_name": (r.get("lease_name") or "").strip(),
                    })

                except Exception:
                    status["errors"] += 1
                    continue

        finally:
            context.close()

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_fields)
        w.writeheader()
        w.writerows(out_rows)

    status["rows_written"] = len(out_rows)
    status["ok"] = True
    print(status)

if __name__ == "__main__":
    main()
