"""Screenshot a dossier's sections, and exercise interactions, the way a phone user would."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
symbol = sys.argv[2]
scheme = "dark" if "--dark" in sys.argv else "light"
BASE = "http://127.0.0.1:8000"

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1.2, is_mobile=True, has_touch=True, color_scheme=scheme)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{BASE}/research/{symbol}", wait_until="networkidle")
    pg.wait_for_selector("#record", timeout=15000)
    # Expand the first promise so its receipts are visible.
    first = pg.locator(".promise .collapse-head").first
    if first.count():
        first.click()
    pg.wait_for_timeout(200)
    pg.screenshot(path=str(out / f"{symbol}-{scheme}-record.png"))
    for sec in ("dodged", "case", "numbers", "balance", "cash", "notes", "business", "ask"):
        el = pg.locator(f"#{sec}")
        if not el.count():
            print("no section", sec)
            continue
        el.scroll_into_view_if_needed()
        pg.evaluate(f"document.getElementById('{sec}').scrollIntoView({{behavior:'instant',block:'start'}})")
        pg.wait_for_timeout(150)
        pg.screenshot(path=str(out / f"{symbol}-{scheme}-{sec}.png"))
    print("overflow", pg.evaluate("document.documentElement.scrollWidth - innerWidth"), "errors", errors)
    b.close()
