# step4_extract_details.py
import csv
import os
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright
from loguru import logger
from pw_common import login, normalize_pw_href, label_value, safe_float

BASE_DIR = Path(__file__).resolve().parent
IN_CSV   = BASE_DIR / "data" / "debug" / "rows_sample.csv"
OUT_CSV  = BASE_DIR / "data" / "debug" / "sample_details.csv"

# Labels used on the Lease & Portfolio pages
LEASE_TOTAL_LABELS  = ["Total Unpaid", "Unpaid Balance", "Total Balance"]
LEASE_TENANT_LABELS = ["Tenant", "Lease Name", "Resident"]
PORT_OWNER_LABELS   = ["Owner", "Portfolio Owner", "Owner Name"]

UNPAID_THRESHOLD = 1000  # change this number if you want a different cutoff

# ---------- helpers ----------
def first_label_value(page, labels: list[str]) -> str:
    """Return the first non-empty text found for any of the provided labels."""
    for lbl in labels:
        try:
            v = label_value(page, lbl)
            if v:
                return v
        except Exception:
            pass
    return ""

def extract_tenant_name_from_lease(page) -> str:
    """
    Prefer a contact link on the lease page; fall back to labeled field.
    """
    try:
        a = page.locator('a[href*="/pw/contacts/contact_detail.do"]').first
        if a and a.count():
            t = (a.inner_text() or "").strip()
            if t:
                return t
    except Exception:
        pass
    return (first_label_value(page, LEASE_TENANT_LABELS) or "").strip()

def get_owner_from_portfolio(page) -> str:
    """
    On many Portfolio pages the first link is the portfolio entity and the
    second link is the actual owner contact. Prefer the second when present.
    """
    try:
        links = page.locator('a[href*="/pw/contacts/contact_detail.do"]')
        cnt = links.count()
        if cnt >= 2:
            return (links.nth(1).inner_text() or "").strip()
        if cnt == 1:
            return (links.first.inner_text() or "").strip()
    except Exception:
        pass
    # Fall back to labeled fields or header
    try:
        v = first_label_value(page, PORT_OWNER_LABELS)
        if v:
            return v.strip()
    except Exception:
        pass
    try:
        return (page.locator("h1").first.inner_text() or "").strip()
    except Exception:
        return ""

def safe_goto(page, url: str, timeout=90000) -> bool:
    """Navigate robustly; if bounced to login, try to re-login once."""
    try:
        page.goto(url, wait_until="commit", timeout=timeout)
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
        return True
    except Exception:
        try:
            if "login" in (page.url or "").lower():
                login(page)
                page.goto(url, wait_until="commit", timeout=timeout)
                page.wait_for_load_state("domcontentloaded", timeout=timeout)
                return True
        except Exception:
            pass
        logger.error(f"safe_goto failed for {url}")
        return False

def _safe_write_csv(rows: list[dict]) -> Path:
    """Write to temp then replace (avoids partial writes); if locked, timestamp a new file."""
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_CSV.with_suffix(".tmp.csv")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        # NOTE: Address removed entirely; County kept from step3
        w.writerow([
            "TenantName","AmountDue","County","PropertyOwner","NoticeType",
            "global_index","page_no","row_index_in_page","county","status"
        ])
        for r in rows:
            w.writerow([
                r["TenantName"], r["AmountDue"], r.get("County",""), r["PropertyOwner"], r["NoticeType"],
                r.get("global_index",""), r.get("page_no",""), r.get("row_index_in_page",""),
                r.get("county",""), r.get("status",""),
            ])
    try:
        if OUT_CSV.exists():
            try:
                os.remove(OUT_CSV)
            except PermissionError:
                raise
        os.replace(tmp, OUT_CSV)
        logger.info(f"Wrote CSV: {OUT_CSV} (replaced previous)")
        return OUT_CSV
    except PermissionError:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        alt = OUT_CSV.with_name(f"{OUT_CSV.stem}_{ts}{OUT_CSV.suffix}")
        os.replace(tmp, alt)
        logger.warning(f"CSV locked (open in Excel). Wrote new file: {alt}")
        return alt

# ---------- main ----------
def main():
    status = {"step":"details_no_address", "ok": False, "rows_in": 0, "rows_out": 0, "csv": str(OUT_CSV)}
    if not IN_CSV.exists():
        print({"error": f"Input not found: {IN_CSV}", **status})
        return

    profile_dir = str((Path(__file__).parent / "edge-profile").resolve())

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir, channel="msedge",
            headless=False, slow_mo=60
        )
        context.set_default_navigation_timeout(90000)
        context.set_default_timeout(90000)

        page = context.new_page()
        page.add_init_script("window.open = (url) => { window.location.href = url; };")
        login(page)

        work = page
        rows_out_list = []

        with IN_CSV.open("r", encoding="utf-8", newline="") as f_in:
            r = csv.DictReader(f_in)
            in_rows = list(r)
            status["rows_in"] = len(in_rows)

            for row in in_rows:
                lease_url = normalize_pw_href(row.get("lease_href",""))
                port_url  = normalize_pw_href(row.get("portfolio_href",""))
                if not lease_url:
                    continue

                # 1) LEASE: threshold + tenant
                if not safe_goto(work, lease_url):
                    continue
                total_txt = first_label_value(work, LEASE_TOTAL_LABELS)
                amount_due = safe_float(total_txt) or 0.0
                if amount_due < UNPAID_THRESHOLD:
                    continue
                tenant = extract_tenant_name_from_lease(work)

                # 2) PORTFOLIO: owner
                owner = ""
                if port_url and safe_goto(work, port_url):
                    owner = get_owner_from_portfolio(work)

                # 3) County comes directly from step3 CSV (grid “county” column)
                county_from_grid = (row.get("county","") or "").strip()

                # 4) Append output (NO Address)
                rows_out_list.append({
                    "TenantName": tenant,
                    "AmountDue": f"{amount_due:.2f}",
                    "County": county_from_grid,      # explicit County field
                    "PropertyOwner": owner,
                    "NoticeType": "3-DAY",
                    "global_index": row.get("global_index",""),
                    "page_no": row.get("page_no",""),
                    "row_index_in_page": row.get("row_index_in_page",""),
                    "county": row.get("county",""),  # original grid county echoed through
                    "status": row.get("status",""),
                })

        out_path = _safe_write_csv(rows_out_list)
        status.update({"rows_out": len(rows_out_list), "ok": len(rows_out_list) > 0, "csv": str(out_path)})
        context.close()

    print(status)

if __name__ == "__main__":
    main()
