"""Phone-sized screenshots of the running app, for checking layout by eye.

    python scripts/shots.py OUT_DIR name=/path name2=/path2 [--dark] [--full] [--set key=json]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

args = sys.argv[1:]
out = Path(args[0])
out.mkdir(parents=True, exist_ok=True)
flags = [a for a in args[1:] if a.startswith("--")]
pages = [a.split("=", 1) for a in args[1:] if not a.startswith("--")]
BASE = "http://127.0.0.1:8000"

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(
        viewport={"width": 390, "height": 844}, device_scale_factor=1.5, is_mobile=True, has_touch=True,
        color_scheme="dark" if "--dark" in flags else "light",
    )
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    for name, path in pages:
        hours = None
        if "#" in path:
            path, hours = path.split("#", 1)
        pg.goto(BASE + path, wait_until="networkidle")
        if hours:
            pg.get_by_role("button", name=hours, exact=True).click()
            pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(400)
        over = pg.evaluate("document.documentElement.scrollWidth - innerWidth")
        pg.screenshot(path=str(out / f"{name}.png"), full_page="--full" in flags)
        print(name, "overflow-x", over, "height", pg.evaluate("document.documentElement.scrollHeight"))
    print("errors", errors[:5])
    b.close()
