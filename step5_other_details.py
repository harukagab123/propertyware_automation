# step5_other_details.py
# Build a CSV of 3-Day Notice variables (no document generation).
#
# Output file: data/debug/<MM-DD-YYYY>_notices.csv
# Fields: TenantName, TenantAddress, AmountDue, DateOfUnpaidRent, PropertyOwner,
#         Date, Day, Month, Year(YY), City, County (from Step 3 "building_county" via lease_href)

from pathlib import Path
from datetime import date
import calendar
import csv
import sys
from typing import Dict, List, Tuple
import re
import difflib

# ---- locations ----
BASE_DIR = Path(__file__).resolve().parent
DEBUG_DIR = BASE_DIR / "data" / "debug"
IN_STEP4 = DEBUG_DIR / "rows_step4.csv"   # total_unpaid, tenant_name, unit_address, owner_name, lease_href (added), ...
IN_STEP3 = DEBUG_DIR / "rows_sample.csv"  # unit/portfolio/lease names + lease_href + building_county
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

# ---- date helpers (stdlib only) ----
def subtract_months(d: date, months: int) -> date:
    y = d.year
    m = d.month - months
    while m <= 0:
        m += 12
        y -= 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last_day))

today = date.today()
DATE_STR = today.strftime("%m-%d-%Y")          # Date (MM-DD-YYYY)
DAY_STR = str(int(today.strftime("%d")))       # Day without leading zero
MONTH_STR = today.strftime("%B")               # Month name (e.g., September)
YEAR_STR = today.strftime("%y")                # Year as YY (two digits)
DATE_UNPAID_STR = subtract_months(today, 2).strftime("%m-%d-%Y")  # DateOfUnpaidRent

OUT_CSV = DEBUG_DIR / f"{DATE_STR}_notices.csv"

# ---- utils ----
def money_fmt(val) -> str:
    try:
        f = float(str(val).replace(",", "").strip())
        return f"{f:,.2f}"
    except Exception:
        return str(val)

def read_csv(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def split_aliases(lease_name: str) -> List[str]:
    # split 'A & B', 'A and B', 'A, B', 'A/B'
    s = (lease_name or "").strip()
    if not s:
        return []
    parts = re.split(r"\s*(?:&|/|,| and )\s*", s, flags=re.I)
    return [p for p in (p.strip() for p in parts) if p]

def city_from_tenant_address(addr: str) -> str:
    """
    Extract the city as the trailing word(s) immediately before the first comma.
    Handles cases like:
      '1656 84th Ave Apt 2 Oakland, CA 94621-1748' -> 'Oakland'
      '123 Main St San Jose, CA 95112'            -> 'San Jose'
      'Quezon City, NCR'                          -> 'Quezon City'
    Heuristic: walk tokens backwards from the first-comma segment and
    collect city tokens until we hit a street/unit token or a token with digits.
    """
    s = (addr or "").strip()
    if not s:
        return ""
    # Part before the first comma
    left = s.split(",", 1)[0].strip()
    if not left:
        return ""

    tokens = left.split()
    # Common non-city tokens seen before the city name
    stop = {
        "st","street","ave","avenue","rd","road","blvd","boulevard",
        "hwy","highway","pkwy","parkway","trl","trail","ter","terrace",
        "ln","lane","dr","drive","ct","court","cir","circle","pl","place",
        "way","aly","alley","apt","unit","ste","suite","bldg","fl","floor",
        "rm","room","#"
    }

    city_tokens = []
    for tok in reversed(tokens):
        t = tok.strip(" .,#").lower()
        # Stop when we hit a token with digits or a street/unit keyword
        if any(ch.isdigit() for ch in t) or t in stop:
            # If we've already started collecting city tokens, break.
            if city_tokens:
                break
            # Otherwise skip and keep moving left (e.g., 'Apt', '2')
            continue
        city_tokens.append(tok.strip(","))
    if city_tokens:
        return " ".join(reversed(city_tokens))
    # Fallback: last token before the comma
    return tokens[-1] if tokens else ""


# ---- Step 3 (County) maps ----
def build_maps_step3(step3_rows: List[dict]) -> Tuple[Dict[str, str], Dict[str, str], List[Tuple[str, str]]]:
    """
    Returns:
      - by_href: { lease_href -> building_county }
      - alias_map: { alias_name_lower -> building_county } for fallback
      - lease_names: list of (lease_name_lower, county) for fuzzy fallback
    """
    by_href: Dict[str, str] = {}
    alias_map: Dict[str, str] = {}
    lease_names: List[Tuple[str, str]] = []

    for r in step3_rows:
        lease_href = (r.get("lease_href") or "").strip()
        lease_name = (r.get("lease_name") or "").strip()
        county = (r.get("building_county") or "").strip()
        if county:
            if lease_href and lease_href not in by_href:
                by_href[lease_href] = county
            if lease_name:
                lk = norm(lease_name)
                lease_names.append((lk, county))
                # also index aliases for fallback
                for alias in split_aliases(lease_name):
                    ak = norm(alias)
                    if ak and ak not in alias_map:
                        alias_map[ak] = county
    return by_href, alias_map, lease_names

def lookup_county(lease_href: str, tenant_name: str,
                  by_href: Dict[str, str],
                  alias_map: Dict[str, str],
                  lease_names: List[Tuple[str, str]]) -> str:
    # 1) exact by href
    if lease_href and lease_href in by_href:
        return by_href[lease_href]

    # 2) alias match on tenant name (if tenant name is one of the lease aliases)
    tk = norm(tenant_name)
    if tk and tk in alias_map:
        return alias_map[tk]

    # 3) fuzzy against full lease names
    candidates = [ln for (ln, _) in lease_names]
    best = difflib.get_close_matches(tk, candidates, n=1, cutoff=0.60) if tk else []
    if best:
        best_key = best[0]
        for ln, county in lease_names:
            if ln == best_key:
                return county

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

    step4_rows = read_csv(IN_STEP4)
    if not step4_rows:
        status["error"] = f"No input rows in {IN_STEP4}"
        print(status)
        sys.exit(1)

    step3_rows = read_csv(IN_STEP3)
    by_href, alias_map, lease_names = build_maps_step3(step3_rows)

    out_fields = [
        "TenantName",
        "TenantAddress",
        "AmountDue",
        "DateOfUnpaidRent",
        "PropertyOwner",
        "Date",
        "Day",
        "Month",
        "Year",   # two digits
        "City",   # from TenantAddress (text before first comma)
        "County", # Building County from Step 3
    ]

    out_rows = []
    for r in step4_rows:
        tenant_name = (r.get("tenant_name") or "").strip()
        tenant_addr = (r.get("unit_address") or "").strip()
        amount_due = money_fmt(r.get("total_unpaid") or "")
        owner_name = (r.get("owner_name") or "").strip()
        lease_href = (r.get("lease_href") or "").strip()   # <-- added in Step 4

        county = lookup_county(lease_href, tenant_name, by_href, alias_map, lease_names)
        city = city_from_tenant_address(tenant_addr)

        out_rows.append({
            "TenantName": tenant_name,
            "TenantAddress": tenant_addr,
            "AmountDue": amount_due,
            "DateOfUnpaidRent": DATE_UNPAID_STR,
            "PropertyOwner": owner_name,
            "Date": DATE_STR,
            "Day": DAY_STR,
            "Month": MONTH_STR,
            "Year": YEAR_STR,  # YY
            "City": city,
            "County": county,
        })

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        writer.writerows(out_rows)

    status["rows_in"] = len(step4_rows)
    status["rows_written"] = len(out_rows)
    status["ok"] = True
    print(status)

if __name__ == "__main__":
    main()
