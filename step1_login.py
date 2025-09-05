# step1_login.py
from pathlib import Path
from loguru import logger
from playwright.sync_api import sync_playwright
from pw_common import login, PW_BASE_URL, PW_LIST_URL, make_context

DEBUG_DIR = Path("data/debug")

def main():
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    status = {
        "step": "login",
        "ok": False,
        "current_url": None,
        "screenshot": str(DEBUG_DIR / "step1_login.png"),
        "base_url": PW_BASE_URL,
        "report_url": PW_LIST_URL,
    }

    with sync_playwright() as p:
        context = make_context(p, headless=False, slow_mo=80)
        page = context.new_page()
        try:
            ok = login(page)
            if ok:
                logger.info("Login successful, navigating to report URL…")
                page.goto(PW_LIST_URL, wait_until="networkidle", timeout=120_000)
                status["ok"] = True
                status["current_url"] = page.url
                page.screenshot(path=status["screenshot"], full_page=True)
        except Exception as e:
            logger.exception(f"Login flow raised: {e}")
        finally:
            context.close()

    print(status)

if __name__ == "__main__":
    main()
