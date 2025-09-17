# pw_common.py
import os, re
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
from loguru import logger
from playwright.sync_api import TimeoutError as PWTimeout

BASE_DIR = Path(__file__).resolve().parent
# Load .env from project root (walk up one if running from src/)
env_path = (BASE_DIR / ".env")
if not env_path.exists():
    env_path = (BASE_DIR.parent / ".env")
load_dotenv(env_path)

# ---- ENV / PATHS ----
PW_BASE_URL = os.getenv("PW_BASE_URL", "https://app.propertyware.com/pw/home/home.do").strip()
PW_LIST_URL = os.getenv("PW_LIST_URL") or os.getenv("PW_REPORT_URL", "")
EXCEL_PATH = Path(os.getenv("EXCEL_PATH", BASE_DIR / "data" / "output_excel.xlsx"))
WORD_TEMPLATE_PATH = Path(os.getenv("WORD_TEMPLATE_PATH", BASE_DIR / "templates" / "3Day_Notice_Template.docx"))
NOTICES_DIR = Path(os.getenv("NOTICES_DIR", BASE_DIR / "data" / "notices"))

PW_USERNAME = os.getenv("PW_USERNAME", "").strip()
PW_PASSWORD = os.getenv("PW_PASSWORD", "").strip()

APP_HOST = "app.propertyware.com"

# ---- BROWSER CONTEXT HELPER (persistent Edge profile) ----
def make_context(p, headless=False, slow_mo=60):
    profile_dir = str((BASE_DIR / "edge-profile").resolve())
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        channel="msedge",     # change to "chromium" if you prefer the bundled engine
        headless=headless,
        slow_mo=slow_mo,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
        ],
    )
    ctx.set_default_navigation_timeout(120_000)
    ctx.set_default_timeout(120_000)
    ctx.set_extra_http_headers({"Referer": "https://app.propertyware.com/"})
    return ctx

# ---- LOGIN (suppress popups) ----
def login(page):
    if not PW_USERNAME or not PW_PASSWORD:
        raise RuntimeError("Missing PW_USERNAME or PW_PASSWORD in .env")

    # Keep PW from opening a new window
    page.add_init_script("window.open = (url) => { window.location.href = url; };")

    logger.info(f"Navigating to {PW_BASE_URL}")
    page.goto(PW_BASE_URL, wait_until="domcontentloaded", timeout=120_000)

    # Try a few common login forms used by PW (and some SSO pages)
    # We try explicit selectors first; if not present, we fallback to labeled fields.
    filled_user = False
    filled_pass = False

    for sel in ['input[name="username"]', 'input[type="email"]', 'input#username']:
        if page.locator(sel).count():
            page.fill(sel, PW_USERNAME)
            filled_user = True
            break
    if not filled_user:
        try:
            page.get_by_label(re.compile("email|username", re.I)).fill(PW_USERNAME)
            filled_user = True
        except Exception:
            pass

    for sel in ['input[name="password"]', 'input[type="password"]', 'input#password']:
        if page.locator(sel).count():
            page.fill(sel, PW_PASSWORD)
            filled_pass = True
            break
    if not filled_pass:
        try:
            page.get_by_label(re.compile("password", re.I)).fill(PW_PASSWORD)
            filled_pass = True
        except Exception:
            pass

    # Click a likely submit control
    submit_selectors = [
        'input[type="button"][value="Sign Me In"]',
        'input.login-button',
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Sign In")',
        'button:has-text("Log In")',
    ]
    clicked = False
    for sel in submit_selectors:
        if page.locator(sel).first.count():
            page.click(sel, no_wait_after=True)
            clicked = True
            break
    if not clicked:
        # Press Enter in password field as a last resort
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass

    try:
        page.wait_for_load_state("networkidle", timeout=120_000)
        # Wait for any of the typical post-login markers
        page.wait_for_selector(
            ".x-grid3, .dashboard, nav, .user-avatar, [data-test-id='dashboard'], a[href*='/pw/']",
            timeout=120_000,
        )
    except PWTimeout:
        logger.warning("Login may not have fully loaded a dashboard element.")

    logger.info("Login attempt complete (popups suppressed).")
    return True

# ---- GRID HELPERS ----
def find_grid_context(page):
    logger.debug("Using main frame for grid")
    return page

def map_headers(ctx):
    headers = {}
    hd_cells = ctx.locator(".x-grid3-hd-row td[class*=x-grid3-hd]")
    count = hd_cells.count()
    for i in range(count):
        cell = hd_cells.nth(i)
        txt = (cell.inner_text() or "").strip().lower()
        if txt:
            headers[txt] = i
    logger.debug(f"Header map: {headers}")
    return headers

def cell_text(row, col_idx: int) -> str:
    if col_idx is None:
        return ""
    try:
        cell = row.locator(f"td.x-grid3-td-{col_idx}").first
        return (cell.inner_text() or "").strip()
    except Exception:
        return ""

# ---- URL NORMALIZATION ----
def normalize_pw_href(href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    m = re.search(r'(\/pw\/(?:properties|leases)\/[a-z_]+\.do\?[^#"]+)', href, flags=re.I)
    if m:
        path_q = m.group(1)
        return f"https://{APP_HOST}{path_q}"
    parsed = urlparse(href)
    if parsed.scheme and parsed.netloc:
        if parsed.netloc.lower() != APP_HOST:
            return f"https://{APP_HOST}{parsed.path or ''}{('?' + parsed.query) if parsed.query else ''}"
        return href
    return f"https://{APP_HOST}/{href.lstrip('/')}"

# ---- SAFE LABEL VALUE ----
def _clean_text(txt: str) -> str:
    if not txt:
        return ""
    t = txt.strip()
    if t.startswith("function ") or t.startswith("var ") or "tinyMCE" in t or "ajaxAction" in t:
        return ""
    return t

def label_value(page, label_text: str) -> str:
    candidates = [
        f'xpath=//tr[th[normalize-space()="{label_text}"]]/td[1]',
        f'xpath=//th[normalize-space()="{label_text}"]/following-sibling::td[1]',
        f'xpath=//tr[td[contains(@class,"label")][normalize-space()="{label_text}"]]/td[position()>1][1]',
        f'xpath=//*[self::div or self::span][contains(@class,"label")][normalize-space()="{label_text}"]/following::*[self::div or self::span][1]',
        f'xpath=//dt[normalize-space()="{label_text}"]/following-sibling::dd[1]',
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc and loc.count() and loc.locator("script, style").count() == 0:
                txt = _clean_text(loc.inner_text())
                if txt:
                    return txt
        except Exception:
            continue
    try:
        lab = page.locator(f'xpath=//*[normalize-space()="{label_text}"]').first
        if lab and lab.count():
            sib = lab.locator('xpath=following::*[not(self::script or self::style)][1]').first
            if sib and sib.count() and sib.locator("script, style").count() == 0:
                txt = _clean_text(sib.inner_text())
                if txt:
                    return txt
    except Exception:
        pass
    return ""

# ---- NUMERIC PARSE ----
def safe_float(text: str) -> float:
    if not text:
        return 0.0
    t = re.sub(r"[^\d\.\-]", "", text)
    try:
        return float(t) if t else 0.0
    except Exception:
        return 0.0
