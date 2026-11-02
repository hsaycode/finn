"""Render the PNG app icons from icon.svg."""
from pathlib import Path
from playwright.sync_api import sync_playwright
pub = Path(__file__).resolve().parent.parent / "web" / "public"
svg = (pub / "icon.svg").read_text()
with sync_playwright() as p:
    b = p.chromium.launch()
    for size in (192, 512):
        pg = b.new_context(viewport={"width": size, "height": size}).new_page()
        pg.set_content(f'<body style="margin:0;background:#1B1A17"><div style="width:{size}px;height:{size}px">{svg}</div></body>')
        pg.screenshot(path=str(pub / f"icon-{size}.png"))
    b.close()
