# main.py
from pathlib import Path
import sys
import csv
from datetime import datetime
from docx import Document

BASE_DIR = Path(__file__).resolve().parent
DEBUG_DIR = BASE_DIR / "data" / "debug"
ROWS_CSV = DEBUG_DIR / "rows_sample.csv"
DETAILS_CSV = DEBUG_DIR / "sample_details.csv"
NOTICES_DIR = BASE_DIR / "data" / "notices"
NOTICES_DIR.mkdir(parents=True, exist_ok=True)


def _count_csv_rows(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            rd = csv.reader(f)
            rows = list(rd)
            return max(0, len(rows) - 1)  # minus header
    except Exception:
        return 0

def run_step3():
    print("\n=== Step 3: Collect row links (all pages) ===")
    # Import-and-run to keep same process (easier than subprocess)
    import step3_rows_sample as s3
    s3.main()
    n = _count_csv_rows(ROWS_CSV)
    print(f"[step3] wrote: {ROWS_CSV}  rows={n}")
    if n == 0:
        print("!! No qualifying rows found in step3. Check filters (counties, status) or login.")
    return n

def run_step4():
    print("\n=== Step 4: Extract lease/owner/address (unpaid >= $1000) ===")
    import step4_extract_details as s4
    s4.main()
    n = _count_csv_rows(DETAILS_CSV)
    print(f"[step4] wrote: {DETAILS_CSV}  rows={n}")
    if n == 0:
        print("!! No qualifying rows found in step4 (maybe all AmountDue < 200?).")
    return n

def run_step5():
    print("\n=== Step 5: Generate 3-Day Notice DOCX ===")
    import step5_generate_notices as s5
    s5.main()

    today_folder = NOTICES_DIR / datetime.now().strftime("%m-%d-%Y")
    if today_folder.exists():
        docs = list(today_folder.glob("*.docx"))
        print(f"[step5] notices in: {today_folder}  count={len(docs)}")
        if docs:
            print("  e.g.:")
            for d in docs[:5]:
                print(f"   - {d.name}")
    else:
        print("!! No notices folder found for today. Check step5 logs above.")

def main():
    print(">>> Propertyware 3-Day Notice automation — starting…")
    print(f"Project: {BASE_DIR}")

    # Step 3
    n3 = run_step3()
    if n3 == 0:
        print("\nStopping because Step 3 produced no rows.")
        return

    # Step 4
    n4 = run_step4()
    if n4 == 0:
        print("\nStopping because Step 4 produced no qualifying details.")
        return

    # Step 5
    run_step5()

    print("\nAll done ✅")

if __name__ == "__main__":
    # Ensure local imports resolve when run as script
    sys.path.insert(0, str(BASE_DIR))
    main()

# Install Playwright
# RUN: pip install playwright
# RUN: python -m playwright install msedge

try:
    import playwright
except ImportError:
    print("Playwright is not installed. Please install it by running:")
    print("pip install playwright")
