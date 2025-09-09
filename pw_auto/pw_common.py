# pw_common.py  — package-safe paths + robust .env + no duplicate constants
import os, re, time
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
from loguru import logger
from playwright.sync_api import TimeoutError as PWTimeout, Page, Frame, Locator
from importlib.resources import files as _res_files

# ---------------------------
# PATHS / ENV (package-safe)
# ---------------------------

# Package directory (…/pw_auto)
PKG_DIR = Path(__file__).resolve().parent

# User's working directory (where they run CLI/UI). We store data here.
PROJECT_ROOT = Path.cwd()

# Data roots (configurable)
DATA_DIR = Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data"))
DEBUG_DIR = DATA_DIR / "debug"
NOTICES_DIR = Path(os.getenv("NOTICES_DIR", DATA_DIR / "notices"))

# Template: packaged default, override with WORD_TEMPLATE_PATH if set
DEFAULT_TEMPLATE = _res_files("pw_auto.assets.templates") / "3DayNotice_TEMPLATE_PLACEHOLDERS.docx"
WORD_TEMPLATE_PATH = Path(os.getenv("WORD_TEMPLATE_PATH") or str(DEFAULT_TEMPLATE))

# Where to persist Edge profile (must be writable)
PROFILE_DIR = Path(os.getenv("PW_PROFILE_DIR", PROJECT_ROOT / "edge-profile"))

# .env loading: prefer current working dir; fall back to repo root style during dev
# 1) working dir .env
loaded = load_dotenv(PROJECT_ROOT / ".env")
# 2) fallback: parent of package (dev editable installs)
if not loaded:
    load_dotenv(PKG_DIR.parent / ".env")

# ---- ENV / CONSTANTS ----
PW_BASE_URL = os.getenv("PW_BASE_URL", "https://app.propertyware.com/").strip()
PW_LIST_URL = (os.getenv("PW_LIST_URL") or os.getenv("PW_REPORT_URL", "")).strip()
PW_USERNAME = os.getenv("PW_USERNAME", "").strip()
PW_PASSWORD = os.getenv("PW_PASSWORD", "").strip()
EXCEL_PATH = Path(os.getenv("EXCEL_PATH", DATA_DIR / "output_excel.xlsx"))

APP_HOST = "app.propertyware.com"

# Ensure data dirs exist
DEBUG_DIR.mkdir(parents=True, exist_ok=True)
NOTICES_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------
# BROWSER CONTEXT
# ---------------------------
def make_context(p, headless=False, slow_mo=60):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        channel="msedge",         # or "chromium"
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

# ---------------------------
# LOGIN
# ---------------------------
def login(page):
    if not PW_USERNAME or not PW_PASSWORD:
        raise RuntimeError("Missing PW_USERNAME or PW_PASSWORD in .env")

    # Keep PW from opening a new window
    page.add_init_script("window.open = (url) => { window.location.href = url; };")

    logger.info(f"Navigating to {PW_BASE_URL}")
    page.goto(PW_BASE_URL, wait_until="domcontentloaded", timeout=120_000)

    # Try common login fields
    filled_user = False
    for sel in ['input[name="username"]', 'input[type="email"]', 'input#username']:
        if page.locator(sel).count():
            page.fill(sel, PW_USERNAME); filled_user = True; break
    if not filled_user:
        try:
            import re as _re
            page.get_by_label(_re.compile("email|username", _re.I)).fill(PW_USERNAME)
            filled_user = True
        except Exception:
            pass

    filled_pass = False
    for sel in ['input[name="password"]', 'input[type="password"]', 'input#password']:
        if page.locator(sel).count():
            page.fill(sel, PW_PASSWORD); filled_pass = True; break
    if not filled_pass:
        try:
            import re as _re
            page.get_by_label(_re.compile("password", _re.I)).fill(PW_PASSWORD)
            filled_pass = True
        except Exception:
            pass

    # Submit
    submit_selectors = [
        'input[type="button"][value="Sign Me In"]',
        'input.login-button',
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Sign In")',
        'button:has-text("Log In")',
    ]
    for sel in submit_selectors:
        if page.locator(sel).first.count():
            page.click(sel, no_wait_after=True)
            break
    else:
        try: page.keyboard.press("Enter")
        except Exception: pass

    try:
        page.wait_for_load_state("networkidle", timeout=120_000)
        page.wait_for_selector(
            ".x-grid3, .dashboard, nav, .user-avatar, [data-test-id='dashboard'], a[href*='/pw/']",
            timeout=120_000,
        )
    except PWTimeout:
        logger.warning("Login may not have fully loaded a dashboard element.")

    logger.info("Login attempt complete (popups suppressed).")
    return True

# ---------------------------
# GRID HELPERS
# ---------------------------
GRID_HEADER_SELECTORS = [
    ".x-grid3-header",       # ExtJS 3
    ".x-grid-header-ct",     # ExtJS 4+
]
GRID_ROW_SELECTORS = [
    ".x-grid3-body .x-grid3-row",       # ExtJS 3
    ".x-grid-view .x-grid-item",        # ExtJS 4+
    ".x-grid-view table tbody tr",      # fallback
]
GRID_LOADING_MASK = [".x-mask-msg", ".x-grid3-loading", ".x-mask"]
GRID_NO_DATA_TEXTS = ["No data to display", "No records found"]

def _has_any(loc: Locator) -> bool:
    try: return loc.count() > 0
    except Exception: return False

def _frame_has_grid(f: Frame) -> bool:
    for sel in GRID_HEADER_SELECTORS:
        if _has_any(f.locator(sel)): return True
    for sel in GRID_ROW_SELECTORS:
        if _has_any(f.locator(sel)): return True
    return False

def _wait_for_loading_to_clear(ctx: Page | Frame, timeout_ms: int = 120_000):
    ctx.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    try: ctx.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PWTimeout: pass
    for sel in GRID_LOADING_MASK:
        try:
            m = ctx.locator(sel).first
            if m.is_visible(): m.wait_for(state="hidden", timeout=timeout_ms)
        except Exception: continue
    time.sleep(0.5)

def find_grid_context(page: Page, timeout_ms: int = 120_000) -> Frame | Page:
    logger.debug("Scanning for grid context (main page + iframes).")
    try:
        _wait_for_loading_to_clear(page, timeout_ms)
        if _frame_has_grid(page.main_frame):
            logger.debug("Grid found in main page.")
            return page
    except Exception:
        pass

    frames = page.frames
    logger.debug(f"Found {len(frames)} frames. Checking each for grid markers…")
    for f in frames:
        try:
            _wait_for_loading_to_clear(f, timeout_ms)
            if _frame_has_grid(f):
                logger.debug(f"Grid found in frame: {f.url or '<no-url>'}")
                return f
        except Exception as e:
            logger.debug(f"Frame check error: {e}")
            continue

    logger.warning("Grid context not positively identified; falling back to main page.")
    return page

def map_headers(ctx: Page | Frame) -> dict[str, int]:
    headers: dict[str, int] = {}
    _wait_for_loading_to_clear(ctx, 60_000)

    try:
        page_text = ctx.locator("body").inner_text()
        if any(txt.lower() in (page_text or "").lower() for txt in GRID_NO_DATA_TEXTS):
            logger.info("Grid shows a 'no data' message.")
    except Exception:
        pass

    # ExtJS 3
    hd_cells = ctx.locator(".x-grid3-hd-row td[class*=x-grid3-hd]")
    try: count = hd_cells.count()
    except Exception: count = 0
    if count > 0:
        for i in range(count):
            try:
                cell = hd_cells.nth(i)
                txt = (cell.inner_text() or "").strip().lower()
                if txt: headers[txt] = i
            except Exception: continue
        if headers: return headers

    # ExtJS 4+
    ext4_headers = ctx.locator(".x-grid-header-ct .x-column-header")
    try: count2 = ext4_headers.count()
    except Exception: count2 = 0
    if count2 > 0:
        for i in range(count2):
            try:
                cell = ext4_headers.nth(i)
                txt_el = cell.locator(".x-column-header-text")
                txt = (txt_el.inner_text() if _has_any(txt_el) else cell.inner_text()) or ""
                txt = txt.strip().lower()
                if txt: headers[txt] = i
            except Exception: continue
        if headers: return headers

    # Fallback table
    ths = ctx.locator("table thead th")
    try: count3 = ths.count()
    except Exception: count3 = 0
    if count3 > 0:
        for i in range(count3):
            try:
                cell = ths.nth(i)
                txt = (cell.inner_text() or "").strip().lower()
                if txt: headers[txt] = i
            except Exception: continue
        if headers: return headers

    logger.warning("No headers detected with known patterns.")
    return headers

def cell_text(row, col_idx: int) -> str:
    if col_idx is None: return ""
    try:
        cell = row.locator(f"td.x-grid3-td-{col_idx}").first
        return (cell.inner_text() or "").strip()
    except Exception:
        return ""

# ---------------------------
# URL / LABEL UTILITIES
# ---------------------------
def normalize_pw_href(href: str) -> str | None:
    if not href: return None
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

def _clean_text(txt: str) -> str:
    if not txt: return ""
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
                if txt: return txt
        except Exception:
            continue
    # Conservative fallback
    try:
        lab = page.locator(f'xpath=//*[normalize-space()="{label_text}"]').first
        if lab and lab.count():
            sib = lab.locator('xpath=following::*[not(self::script or self::style)][1]').first
            if sib and sib.count() and sib.locator("script, style").count() == 0:
                txt = _clean_text(sib.inner_text())
                if txt: return txt
    except Exception:
        pass
    return ""

def safe_float(text: str) -> float:
    if not text: return 0.0
    t = re.sub(r"[^\d\.\-]", "", text)
    try: return float(t) if t else 0.0
    except Exception: return 0.0
