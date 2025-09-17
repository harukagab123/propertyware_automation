# step6_generate.py
from pathlib import Path
from datetime import datetime, date
import csv, sys, re, calendar, glob
from docxtpl import DocxTemplate

# ---- paths ----
BASE_DIR = Path(__file__).resolve().parent
DEBUG_DIR = BASE_DIR / "data" / "debug"
TEMPLATES_DIR = BASE_DIR / "templates"

today = date.today()
DATE_LONG = today.strftime("%m-%d-%Y")  # for doc contents (e.g., Date field)
DATE_DIR  = today.strftime("%m-%d-%y")  # for folder+filename (two-digit year)

OUT_DIR = BASE_DIR / "notices" / DATE_DIR
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---- helpers ----
def money_fmt(val) -> str:
    try:
        f = float(str(val).replace(",", "").strip())
        return f"{f:,.2f}"
    except Exception:
        return str(val or "")

def first_nonempty(row, *keys, default=""):
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return default

def two_digit_year(y_like) -> str:
    try:
        y = int(str(y_like).strip())
        return f"{y % 100:02d}"
    except Exception:
        return today.strftime("%y")

def parse_mmddyyyy_or_like(s: str):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%m-%d-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None

def subtract_months(d: date, months: int) -> date:
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last_day))

def oneline(s: str) -> str:
    """Collapse newlines/tabs/multiple spaces into a single spaced line."""
    s = str(s or "")
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()

def city_from_tenant_address(addr: str) -> str:
    """
    City is the text immediately BEFORE the first comma.
    '1656 84th Ave Apt 2 Oakland, CA 94621-1748' -> 'Oakland'
    """
    s = (addr or "").strip()
    if not s:
        return ""
    left = s.split(",", 1)[0].strip()
    if not left:
        return ""
    tokens = left.split()
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
        if any(ch.isdigit() for ch in t) or t in stop:
            if city_tokens:
                break
            continue
        city_tokens.append(tok.strip(","))
    return " ".join(reversed(city_tokens)) if city_tokens else (tokens[-1] if tokens else "")

def ensure_unique_path(path: Path) -> Path:
    """Avoid overwriting if multiple rows share TenantName."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 2
    while True:
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
        i += 1

def find_source_csv() -> Path:
    """
    Priority:
      1) CLI arg #1
      2) data/debug/letters_export.csv
      3) data/debug/<today MM-DD-YYYY>_notices.csv
      4) newest *_notices.csv in data/debug
    """
    if len(sys.argv) > 1:
        p = Path(sys.argv[1]).expanduser().resolve()
        if p.exists():
            return p

    p = DEBUG_DIR / "letters_export.csv"
    if p.exists():
        return p

    p = DEBUG_DIR / f"{DATE_LONG}_notices.csv"
    if p.exists():
        return p

    candidates = sorted(
        (Path(x) for x in glob.glob(str(DEBUG_DIR / "*_notices.csv"))),
        key=lambda x: x.stat().st_mtime,
        reverse=True
    )
    if candidates:
        return candidates[0]

    # Return non-existent default to trigger clear error message
    return DEBUG_DIR / "letters_export.csv"

def find_template() -> Path:
    """
    Priority:
      1) CLI arg #2 (explicit template path)
      2) templates/3Day_Notice_Template.docx
      3) templates/3Day Notice Template.docx
      4) first match of templates/3Day*Notice*.docx
      5) if only one .docx in templates/, use it
    """
    # CLI override (argument #2)
    if len(sys.argv) > 2:
        t = Path(sys.argv[2]).expanduser().resolve()
        if t.exists():
            return t

    # Known names
    cands = [
        TEMPLATES_DIR / "3Day_Notice_Template.docx",
        TEMPLATES_DIR / "3Day Notice Template.docx",
    ]
    for c in cands:
        if c.exists():
            return c

    # Pattern search
    globbed = sorted(Path(p) for p in glob.glob(str(TEMPLATES_DIR / "3Day*Notice*.docx")))
    if globbed:
        return globbed[0]

    # Fallback: single docx in templates
    any_docx = sorted(Path(p) for p in glob.glob(str(TEMPLATES_DIR / "*.docx")))
    if len(any_docx) == 1:
        return any_docx[0]

    # If we get here, no usable template was found
    return TEMPLATES_DIR / "3Day_Notice_Template.docx"

# ---- start ----
SRC_CSV = find_source_csv()
if not SRC_CSV.exists():
    raise FileNotFoundError(
        "No source CSV found.\n"
        f"Tried:\n"
        f"  1) CLI arg (if provided)\n"
        f"  2) {DEBUG_DIR / 'letters_export.csv'}\n"
        f"  3) {DEBUG_DIR / (DATE_LONG + '_notices.csv')}\n"
        f"  4) newest '*_notices.csv' in {DEBUG_DIR}\n"
        "Tip: run Step 5 first or pass a path:\n"
        f'  python {Path(__file__).name} "data/debug/{DATE_LONG}_notices.csv"\n'
    )

TEMPLATE_PATH = find_template()
if not TEMPLATE_PATH.exists():
    tried = [
        str(TEMPLATES_DIR / "3Day_Notice_Template.docx"),
        str(TEMPLATES_DIR / "3Day Notice Template.docx"),
        str(TEMPLATES_DIR / "3Day*Notice*.docx"),
        str(TEMPLATES_DIR / "*.docx"),
    ]
    raise FileNotFoundError(
        "Template not found.\n"
        f"Tried (in order):\n  1) CLI arg #2 (if provided)\n  2) {tried[0]}\n  3) {tried[1]}\n"
        f"  4) {tried[2]}\n  5) If exactly one .docx exists at {TEMPLATES_DIR}, use that.\n"
        "Tip: pass the template explicitly (quote paths with spaces):\n"
        f'  python {Path(__file__).name} "{SRC_CSV}" "{TEMPLATES_DIR / "3Day Notice Template.docx"}"\n'
    )

with SRC_CSV.open("r", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

print({
    "step": "generate_docs",
    "template": str(TEMPLATE_PATH),
    "csv": str(SRC_CSV),
    "rows_in": len(rows),
    "output_dir": str(OUT_DIR),
})

for idx, r in enumerate(rows, start=1):
    tenant_name     = first_nonempty(r, "TenantName", "tenant_name")
    tenant_addr_raw = first_nonempty(r, "TenantAddress", "unit_address")
    tenant_addr     = oneline(tenant_addr_raw)  # force single line

    owner_name = first_nonempty(r, "PropertyOwner", "owner_name")
    amount_due = money_fmt(first_nonempty(r, "AmountDue", "total_unpaid"))

    # Doc-visible date fields
    date_str  = first_nonempty(r, "Date") or DATE_LONG
    d_dt      = parse_mmddyyyy_or_like(date_str) or datetime.strptime(DATE_LONG, "%m-%d-%Y")
    day_str   = first_nonempty(r, "Day") or str(int(d_dt.strftime("%d")))
    month_str = first_nonempty(r, "Month") or d_dt.strftime("%B")
    year_two  = two_digit_year(first_nonempty(r, "Year", "year", default=d_dt.year))

    # DateOfUnpaidRent (prefer CSV; else two months back from today)
    due_src = first_nonempty(r, "DateOfUnpaidRent") or subtract_months(today, 2).strftime("%m-%d-%Y")

    county = first_nonempty(r, "County", "building_county")
    city   = first_nonempty(r, "City") or city_from_tenant_address(tenant_addr)

    context = {
        "TenantName": tenant_name,
        "TenantAddress": tenant_addr,  # single line
        "AmountDue": amount_due,
        "DateOfUnpaidRent": due_src,
        "PropertyOwner": owner_name,
        "Date": date_str,
        "Day": day_str,
        "Month": month_str,
        "Year": year_two,   # two digits
        "City": city,       # before first comma
        "County": county,
    }

    doc = DocxTemplate(str(TEMPLATE_PATH))
    doc.render(context)

    # Filename: MM-DD-YY_TenantName.docx in notices/MM-DD-YY/
    safe_name = re.sub(r"[^\w\-. ]", "_", tenant_name or f"row_{idx}")
    file_name = f"{DATE_DIR}_{safe_name}.docx"
    out_path = ensure_unique_path(OUT_DIR / file_name)

    doc.save(str(out_path))

print({"ok": True, "output_dir": str(OUT_DIR)})
