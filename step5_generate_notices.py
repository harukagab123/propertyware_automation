# step5_other_details.py
from pathlib import Path
from datetime import date
import calendar
import csv
import sys
from typing import Dict, List, Tuple
import re
import difflib

BASE_DIR = Path(__file__).resolve().parent
DEBUG_DIR = BASE_DIR / "data" / "debug"
IN_STEP4 = DEBUG_DIR / "rows_step4.csv"   # now includes lease_href + lease_name (after Step 4 patch)
IN_STEP3 = DEBUG_DIR / "rows_sample.csv"  # includes lease_href, lease_name, and Building County (various header spellings)
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

def subtract_months(d: date, months: int) -> date:
    y = d.year
    m = d.month - months
    while m <= 0:
        m += 12
        y -= 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last_day))

today = date.today()
DATE_STR = today.strftime("%m-%d-%Y")
DAY_STR = str(int(today.strftime("%d")))
MONTH_STR = today.strftime("%B")
YEAR_STR = today.strftime("%Y")
DATE_UNPAID_STR = subtract_months(today, 2).strftime("%m-%d-%Y")

OUT_CSV = DEBUG_DIR / f"{DATE_STR}_notices.csv"

# ---------- helpers ----------
def read_csv(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def money_fmt(val) -> str:
    try:
        f = float(str(val).replace(",", "").strip())
        return f"{f:,.2f}"
    except Exception:
        return str(val)

def pick_first_key(d: dict, keys: List[str]) -> str:
    """Return d[k] for the first k present, with case-insensitive lookup and whitespace-normalized keys."""
    if not d:
        return ""
    # build CI map
    ci_map = {norm(k): k for k in d.keys()}
    for k in keys:
        real = ci_map.get(norm(k))
        if real is not None:
            return (d.get(real) or "").strip()
    return ""

# ---------- build maps from Step 3 ----------
COUNTY_KEYS = [
    "building_county", "building county", "Building County", "BuildingCounty",
    "Buildingcounty", "BuildingCounty ", "Buildingbuilding_county"
]

def build_maps_from_step3(rows3: List[dict]) -> Tuple[Dict[str, str], Dict[str, str], List[Tuple[str, str]]]:
    """
    Returns:
      - county_by_href: { lease_href -> county }
      - county_by_lease_name: { normalized lease_name -> county }
      - lease_names_list: [(normalized lease_name, county)] for fuzzy fallback
    """
    county_by_href: Dict[str, str] = {}
    county_by_lease_name: Dict[str, str] = {}
    lease_names_list: List[Tuple[str, str]] = []

    for r in rows3:
        lease_href = pick_first_key(r, ["lease_href", "LeaseHref", "lease url", "lease_url"])
        lease_name = pick_first_key(r, ["lease_name", "Lease Name", "lease", "tenant", "tenant_name"])
        county = pick_first_key(r, COUNTY_KEYS)
        if county:
            if lease_href and lease_href not in county_by_href:
                county_by_href[lease_href] = county
            if lease_name:
                lk = norm(lease_name)
                if lk and lk not in county_by_lease_name:
                    county_by_lease_name[lk] = county
                    lease_names_list.append((lk, county))
    return county_by_href, county_by_lease_name, lease_names_list

def lookup_county(lease_href: str, lease_name: str,
                  county_by_href: Dict[str, str],
                  county_by_lease_name: Dict[str, str],
                  lease_names_list: List[Tuple[str, str]]) -> str:
    # 1) exact by href
    if lease_href and lease_href in county_by_href:
        return county_by_href[lease_href]
    # 2) exact by normalized lease name
    lk = norm(lease_name)
    if lk and lk in county_by_lease_name:
        return county_by_lease_name[lk]
    # 3) fuzzy by lease name (light)
    if lk:
        candidates = [n for (n, _) in lease_names_list]
        best = difflib.get_close_matches(lk, candidates, n=1, cutoff=0.75)
        if best:
            best_key = best[0]
            for n, c in lease_names_list:
                if n == best_key:
                    return c
    return ""

def main():
    status = {
        "step": "build_notice_variables_csv",
        "ok": False,
        "input_step4": str(IN_STEP4),
        "input_step3": str(IN_STEP3),
        "output_csv": str(OUT_CSV),
        "rows_in": 0,
        "rows_written": 0,
        "error": None,
    }

    step4 = read_csv(IN_STEP4)
    if not step4:
        status["error"] = f"No input rows in {IN_STEP4}"
        print(status); sys.exit(1)

    step3 = read_csv(IN_STEP3)
    county_by_href, county_by_lease_name, lease_names_list = build_maps_from_step3(step3)

    out_fields = [
        "TenantName", "TenantAddress", "AmountDue", "DateOfUnpaidRent",
        "PropertyOwner", "Date", "Day", "Month", "Year", "County"
    ]

    out_rows = []
    for r in step4:
        tenant_name   = pick_first_key(r, ["tenant_name", "TenantName"])
        tenant_addr   = pick_first_key(r, ["unit_address", "TenantAddress", "address"])
        amount_due    = money_fmt(pick_first_key(r, ["total_unpaid", "AmountDue"]))
        owner_name    = pick_first_key(r, ["owner_name", "PropertyOwner"])
        lease_href    = pick_first_key(r, ["lease_href", "LeaseHref", "lease url", "lease_url"])
        lease_name    = pick_first_key(r, ["lease_name", "Lease Name", "lease", "tenant", "tenant_name"])

        county = lookup_county(lease_href, lease_name, county_by_href, county_by_lease_name, lease_names_list)

        out_rows.append({
            "TenantName": tenant_name,
            "TenantAddress": tenant_addr,
            "AmountDue": amount_due,
            "DateOfUnpaidRent": DATE_UNPAID_STR,
            "PropertyOwner": owner_name,
            "Date": DATE_STR,
            "Day": str(int(DAY_STR)),   # ensure no leading zero
            "Month": MONTH_STR,
            "Year": YEAR_STR,
            "County": county,
        })

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_fields)
        w.writeheader()
        w.writerows(out_rows)

    status["rows_in"] = len(step4)
    status["rows_written"] = len(out_rows)
    status["ok"] = True
    print(status)

if __name__ == "__main__":
    main()
