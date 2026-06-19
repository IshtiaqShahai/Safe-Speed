"""
Take two screenshots of the Streamlit map app:
  1. All regions (default view)
  2. Maharashtra region filtered

Output: docs/screenshots/map_all_regions.png
         docs/screenshots/map_maharashtra.png

Usage:
    python tools/screenshot_map.py
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "http://localhost:8501"
OUT = Path(__file__).parent.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

VIEWPORT = {"width": 1440, "height": 860}


def wait_for_streamlit(page, extra_sleep=12):
    """Wait for Streamlit to finish rendering, then extra time for PyDeck tiles."""
    # networkidle: no more than 0 pending requests for 500ms
    page.wait_for_load_state("networkidle", timeout=90000)
    # Streamlit renders header metrics once data is loaded — wait for any metric
    try:
        page.wait_for_selector("[data-testid='metric-container']", timeout=60000)
    except Exception:
        pass  # app may not have metric containers in all views
    time.sleep(extra_sleep)


def take_screenshots():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = browser.new_context(viewport=VIEWPORT)
        page = ctx.new_page()

        # ── 1. All regions (default) ───────────────────────────────────────────
        print("Opening app ...")
        page.goto(URL, timeout=90000)
        wait_for_streamlit(page, extra_sleep=15)

        out_all = OUT / "map_all_regions.png"
        page.screenshot(path=str(out_all), full_page=False)
        print(f"Saved: {out_all}")

        # ── 2. Maharashtra filter ──────────────────────────────────────────────
        print("Selecting Maharashtra ...")
        # Click the first selectbox (Region) in the sidebar
        selectbox = page.locator("[data-testid='stSelectbox']").first
        selectbox.click()
        time.sleep(1)
        # Select Maharashtra from the dropdown options
        page.get_by_role("option", name="Maharashtra").click()
        wait_for_streamlit(page, extra_sleep=12)

        out_mh = OUT / "map_maharashtra.png"
        page.screenshot(path=str(out_mh), full_page=False)
        print(f"Saved: {out_mh}")

        browser.close()
        print("Done.")


if __name__ == "__main__":
    take_screenshots()
