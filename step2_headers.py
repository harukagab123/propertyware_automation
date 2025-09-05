# step2_headers.py
from pathlib import Path
from loguru import logger
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from pw_common import login, find_grid_context, map_headers, PW_LIST_URL, make_context

DEBUG_DIR = Path("data/debug")  # fixed casing ("data"), same as step1

def main():
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    out_json = DEBUG_DIR / "headers.json"
    shot = DEBUG_DIR / "step2_grid.png"

    status = {
        "step": "headers",
        "ok": False,
        "headers": {},
        "report_url": PW_LIST_URL,
        "screenshot": str(shot),
        "error": None,
    }

    with sync_playwright() as p:
        # Use the persistent context so your session/cookies carry over between runs
        context = make_context(p, headless=False, slow_mo=60)
        page = context.new_page()

        try:
            # Ensure we’re logged in first
            login(page)

            # Go straight to the report page
            logger.info(f"Navigating to report URL: {PW_LIST_URL}")
            page.goto(PW_LIST_URL, wait_until="domcontentloaded", timeout=120_000)
            page.wait_for_load_state("networkidle", timeout=120_000)

            # Find the grid region and wait for columns/rows
            ctx = find_grid_context(page)
            ctx.wait_for_selector(".x-grid3-header", timeout=60_000)
            ctx.wait_for_selector(".x-grid3-body .x-grid3-row", timeout=60_000)

            # Build header map: {"column name": index}
            headers = map_headers(ctx)
            status["headers"] = headers
            status["ok"] = len(headers) > 0

            # Screenshot for debugging
            ctx.screenshot(path=str(shot), full_page=True)

        except PWTimeout as te:
            status["error"] = f"Timeout waiting for grid: {te}"
            logger.exception(status["error"])
        except Exception as e:
            status["error"] = str(e)
            logger.exception(f"step2_headers error: {e}")
        finally:
            context.close()

    # Write headers.json (even if empty, so you can inspect what happened)
    try:
        import json
        out_json.write_text(json.dumps(status["headers"], indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not write {out_json}: {e}")

    print(status)

if __name__ == "__main__":
    main()
