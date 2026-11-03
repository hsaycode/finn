"""Drive the real app in a phone-sized browser. Needs the server running on :8000 with data.

    python tests/e2e.py [DOSSIER_SYMBOL]
"""
import os
import sys

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("FINN_E2E_BASE", "http://127.0.0.1:8000")
BROWSER = os.environ.get("FINN_E2E_BROWSER", "chromium")  # webkit is what an iPhone runs
symbol = sys.argv[1] if len(sys.argv) > 1 else "KAYNES"
results = []


def step(name, fn):
    try:
        fn()
        results.append((name, True, ""))
        print(f"PASS  {name}", flush=True)
    except Exception as e:
        results.append((name, False, str(e)))
        print(f"FAIL  {name}\n      {str(e)[:400]}", flush=True)


with sync_playwright() as p:
    b = getattr(p, BROWSER).launch()
    # Firefox has no mobile emulation, so there it's only a phone-sized window.
    phone = {} if BROWSER == "firefox" else {"is_mobile": True}
    ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, **phone)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))

    def finish_brief():
        pg.goto(BASE, wait_until="networkidle")
        pg.get_by_role("button", name="7 days", exact=True).click()
        pg.wait_for_selector(".card")
        n = pg.locator(".card").count()
        assert n > 0, "no top cards"
        for _ in range(n):
            pg.locator(".card:not(.card-done) .done").first.click()
        expect(pg.get_by_text("That's everything.")).to_be_visible()

    def done_persists():
        pg.reload(wait_until="networkidle")
        pg.wait_for_selector(".card")
        expect(pg.get_by_text("That's everything.")).to_be_visible()

    def undo_reopens():
        pg.locator(".done-undo").first.click()
        expect(pg.get_by_text("That's everything.")).to_have_count(0)
        expect(pg.locator(".card:not(.card-done)")).to_have_count(1)

    def buried_is_reachable():
        pg.get_by_role("button", name="Buried for you").click()
        expect(pg.get_by_text("FINN read them so you don't have to.")).to_be_visible()

    def results_card_shows_figures():
        expect(pg.locator(".results").first).to_be_visible()
        expect(pg.locator(".results").first.get_by_text("Revenue", exact=False).first).to_be_visible()

    def budget_moves():
        pg.goto(f"{BASE}/watch", wait_until="networkidle")
        before = int(pg.locator(".budget-num").inner_text())
        pg.locator("#move").fill("15")
        pg.locator("#vol").fill("6")
        after = int(pg.locator(".budget-num").inner_text())
        assert after < before, f"{before} -> {after}"
        pg.locator("#move").fill("5")
        pg.locator("#vol").fill("2")
        assert int(pg.locator(".budget-num").inner_text()) == before

    def mute_demotes():
        pg.goto(BASE, wait_until="networkidle")
        pg.get_by_role("button", name="7 days", exact=True).click()
        pg.wait_for_selector(".card")
        before = pg.locator(".card").count()
        pg.goto(f"{BASE}/watch", wait_until="networkidle")
        pg.get_by_role("button", name="Results", exact=True).click()
        pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_load_state("networkidle")
        after = pg.locator(".card").count()
        assert after < before, f"{before} -> {after}"
        expect(pg.get_by_role("button", name="Worth knowing")).to_be_visible()
        pg.goto(f"{BASE}/watch", wait_until="networkidle")
        pg.get_by_role("button", name="Results", exact=True).click()

    def receipts_open():
        pg.goto(f"{BASE}/research/{symbol}", wait_until="networkidle")
        pg.wait_for_selector("#record")
        pg.locator(".promise .collapse-head").first.click()
        expect(pg.locator(".receipts blockquote").first).to_be_visible()
        expect(pg.locator(".receipts .src").first).to_have_attribute("href", __import__("re").compile("^https://"))

    def pin_a_promise():
        heading = pg.locator("#guidance .promise .collapse-head")
        assert heading.count(), "no open promises"
        heading.first.click()
        pg.get_by_role("button", name="Watch this promise").first.click()
        pg.get_by_role("button", name="Watch", exact=True).click()
        expect(pg.locator(".pins li")).to_have_count(1)
        pg.get_by_role("button", name="Stop watching").click()
        expect(pg.locator(".pins li")).to_have_count(0)

    def bars_respond():
        pg.goto(f"{BASE}/research/{symbol}", wait_until="networkidle")
        fig = pg.locator(".bars").first
        fig.scroll_into_view_if_needed()
        before = fig.locator("figcaption .num").inner_text()
        fig.locator("svg g").first.click()
        assert fig.locator("figcaption .num").inner_text() != before

    def bad_ticker():
        pg.goto(f"{BASE}/research", wait_until="networkidle")
        pg.locator("#ticker").fill("NOSUCHCO123")
        pg.locator("form .primary").click()
        expect(pg.get_by_text("Check the ticker", exact=False)).to_be_visible(timeout=30000)
        expect(pg.locator("#ticker")).to_be_visible()

    def no_sideways_scroll():
        for path in ("/", "/chat", "/watch", "/research", f"/research/{symbol}"):
            pg.goto(BASE + path, wait_until="networkidle")
            assert pg.evaluate("document.documentElement.scrollWidth - innerWidth") == 0, path

    def touch_targets():
        pg.goto(BASE, wait_until="networkidle")
        small = pg.evaluate("""() => [...document.querySelectorAll('.tabs button, .chip, .primary')]
            .filter(e => e.getBoundingClientRect().height < 36).map(e => e.textContent)""")
        assert not small, small

    def offline_keeps_the_last_brief():
        pg.goto(BASE, wait_until="networkidle")
        pg.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller !== null || true")
        pg.evaluate("navigator.serviceWorker.ready")
        pg.reload(wait_until="networkidle")  # controlled by the worker from here on
        pg.wait_for_timeout(500)
        ctx.set_offline(True)
        try:
            pg.reload(wait_until="load")
            pg.wait_for_selector(".lede", timeout=8000)
            assert "Couldn't reach" not in pg.content()
        finally:
            ctx.set_offline(False)

    def custom_dates():
        pg.goto(BASE, wait_until="networkidle")
        pg.get_by_role("button", name="Custom", exact=True).click()
        pg.locator("#date-from").fill("2026-09-01")
        pg.locator("#date-to").fill("2026-10-31")
        pg.wait_for_load_state("networkidle")
        expect(pg.get_by_text("1 Sept", exact=False).or_(pg.get_by_text("1 Sep", exact=False)).first).to_be_visible()
        pg.wait_for_selector(".card", timeout=15000)
        pg.get_by_role("button", name="24 hours", exact=True).click()
        pg.wait_for_load_state("networkidle")
        expect(pg.locator(".daterange")).to_have_count(0)

    def chat_answers():
        pg.goto(f"{BASE}/chat", wait_until="networkidle")
        pg.evaluate("localStorage.removeItem('finn.chat')")
        pg.reload(wait_until="networkidle")
        pg.get_by_role("button", name="What matters today?").click()
        expect(pg.locator(".bubble-finn").first).to_be_visible(timeout=20000)
        pg.locator("#chat-input").fill("Should I buy Dixon?")
        pg.get_by_role("button", name="Send").click()
        expect(pg.get_by_text("I don't make buy or sell calls")).to_be_visible(timeout=20000)
        pg.locator("#chat-input").fill(f"{symbol} results")
        pg.get_by_role("button", name="Send").click()
        expect(pg.get_by_text("from the filed data", exact=False).first).to_be_visible(timeout=20000)
        # A long promise in an answer once widened the page, which zoomed the phone out and hid Send.
        pg.locator("#chat-input").fill("Dixon's track record")
        pg.get_by_role("button", name="Send").click()
        expect(pg.get_by_text("checkable promises", exact=False).first).to_be_visible(timeout=20000)
        assert pg.evaluate("document.documentElement.scrollWidth") <= 390, "answer widened the page"
        pg.get_by_role("button", name="Clear conversation").click()

    def compare_companies():
        pg.goto(f"{BASE}/research", wait_until="networkidle")
        box = pg.locator("#compare")
        box.get_by_role("button", name="DIXON", exact=True).click()
        box.get_by_role("button", name="KAYNES", exact=True).click()
        expect(box.locator(".compare table")).to_be_visible(timeout=10000)
        expect(box.get_by_text("Promises kept", exact=True)).to_be_visible()

    def live_price():
        pg.goto(f"{BASE}/research/{symbol}", wait_until="networkidle")
        expect(pg.locator(".priceblock .price")).to_be_visible(timeout=15000)
        pg.locator(".priceblock").get_by_role("button", name="1Y", exact=True).click()
        expect(pg.locator(".pricechart svg")).to_be_visible(timeout=15000)

    def holdings_list():
        pg.goto(f"{BASE}/watch", wait_until="networkidle")
        expect(pg.locator(".quote-row")).to_have_count(18, timeout=30000)

    def new_sections():
        pg.goto(f"{BASE}/research/DIXON", wait_until="networkidle")
        expect(pg.locator("#peers table")).to_be_visible(timeout=10000)
        expect(pg.locator("#forces")).to_be_visible()
        pg.locator("#forces .collapse-head").first.click()
        expect(pg.locator("#forces .force").first).to_be_visible()

    def since_last_visit():
        pg.goto(BASE, wait_until="networkidle")
        pg.evaluate("localStorage.setItem('finn.brief.seen', JSON.stringify('2026-10-01T00:00:00'))")
        pg.reload(wait_until="networkidle")
        expect(pg.locator(".since li").first).to_be_visible(timeout=10000)
        pg.reload(wait_until="networkidle")
        expect(pg.locator(".since")).to_have_count(0)

    def chat_remembers_the_company():
        pg.goto(f"{BASE}/chat", wait_until="networkidle")
        ask = pg.get_by_placeholder("Ask about a company")
        ask.fill(f"{symbol} track record")
        ask.press("Enter")
        expect(pg.locator(".about")).to_contain_text(symbol)
        follow = pg.get_by_placeholder(f"Ask about {symbol}")
        follow.fill("and its results?")
        follow.press("Enter")
        expect(pg.locator(".bubble-finn").last.locator(".answer-items, table")).to_be_visible()
        pg.get_by_role("button", name=f"Stop talking about {symbol}").click()
        expect(pg.locator(".about")).to_have_count(0)
        pg.get_by_role("button", name="Clear conversation").click()

    def chat_says_hi():
        pg.goto(f"{BASE}/chat", wait_until="networkidle")
        ask = pg.get_by_placeholder("Ask about a company")
        ask.fill(f"{symbol} track record")
        ask.press("Enter")
        expect(pg.locator(".about")).to_contain_text(symbol)
        answers = pg.locator(".bubble-finn")
        count = answers.count()
        follow = pg.get_by_placeholder(f"Ask about {symbol}")
        follow.fill("Hi")
        follow.press("Enter")
        expect(answers).to_have_count(count + 1, timeout=20000)
        said = answers.last.inner_text()
        assert "can't tell which company" not in said and "calls" not in said.split("earnings calls")[0], said
        expect(pg.locator(".about")).to_contain_text(symbol)
        pg.get_by_role("button", name=f"Stop talking about {symbol}").click()
        pg.get_by_role("button", name="Clear conversation").click()

    def moving_now_banner():
        # Market hours can't be waited for, so the clock is held at 11:00 IST on a weekday and the live answer is filled in.
        other = b.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
        live = other.new_page()
        live.clock.set_fixed_time("2026-11-03T05:30:00Z")
        live.route("**/api/live*", lambda r: r.fulfill(json={"moving": [{"symbol": "DIXON", "price": 100.0, "move_pct": 6.2, "vol_x": 2.4, "as_of": "3 Nov, 11:00 IST"}]}))
        live.goto(BASE, wait_until="networkidle")
        banner = live.locator(".movers", has_text="Moving now")
        expect(banner).to_contain_text("DIXON")
        expect(banner).to_contain_text("2.4× vol so far")
        other.close()

    def own_holdings():
        pg.goto(f"{BASE}/watch", wait_until="networkidle")
        pg.wait_for_selector(".quote-row")
        before = pg.locator(".quote-row").count()
        pg.get_by_role("button", name="Edit", exact=True).click()
        first = pg.locator(".quote-row .ticker-plain").first.inner_text()
        pg.get_by_role("button", name=f"Remove {first}").click()
        expect(pg.locator(".quote-row")).to_have_count(before - 1)
        pg.reload(wait_until="networkidle")
        pg.wait_for_selector(".quote-row")
        expect(pg.locator(".quote-row")).to_have_count(before - 1)
        pg.get_by_role("button", name="Edit", exact=True).click()
        box = pg.get_by_placeholder("Add an NSE ticker")
        box.fill("NOSUCHCOMPANY1")
        box.press("Enter")
        expect(pg.get_by_role("status")).to_contain_text("NOSUCHCOMPANY1")
        pg.get_by_role("button", name="Back to the default list").click()
        expect(pg.locator(".quote-row")).to_have_count(before)
        pg.get_by_role("button", name="Done").click()

    def own_holdings_change_the_brief():
        pg.goto(f"{BASE}/watch", wait_until="networkidle")
        pg.evaluate("localStorage.setItem('finn.holdings', JSON.stringify(['TITAN']))")
        pg.goto(BASE, wait_until="networkidle")
        pg.get_by_role("button", name="30 days", exact=True).click()
        pg.wait_for_load_state("networkidle")
        tickers = set(pg.locator(".card .ticker").all_inner_texts())
        assert tickers <= {"TITAN"}, tickers
        pg.evaluate("localStorage.removeItem('finn.holdings')")

    def coming_up():
        pg.goto(BASE, wait_until="networkidle")
        expect(pg.get_by_role("region", name="Coming up")).to_be_visible()

    def company_news_shelf():
        pg.goto(BASE, wait_until="networkidle")
        pg.get_by_role("button", name="30 days", exact=True).click()
        pg.wait_for_load_state("networkidle")
        worth = pg.get_by_role("button", name="Worth knowing")
        if worth.get_attribute("aria-expanded") == "false":
            worth.click()
        expect(pg.get_by_role("button", name="Company news")).to_be_visible()

    def one_page_summary():
        pg.goto(f"{BASE}/research/{symbol}", wait_until="networkidle")
        pg.get_by_role("link", name="One-page summary").click()
        expect(pg.get_by_role("heading", name="Did they do what they said?")).to_be_visible()
        expect(pg.get_by_role("button", name="Print or save as PDF")).to_be_visible()
        assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "sideways scroll"
        pg.emulate_media(media="print")
        expect(pg.locator(".tabs")).to_be_hidden()
        expect(pg.get_by_role("button", name="Print or save as PDF")).to_be_hidden()
        pg.emulate_media(media="screen")

    def tab_routes():
        pg.goto(BASE, wait_until="networkidle")
        expect(pg.locator(".tabs button")).to_have_count(4)
        pg.locator(".tabs button", has_text="Chat").click()
        expect(pg.locator("#chat-input")).to_be_visible()

    step("Finish the brief and reach its end", finish_brief)
    step("Progress survives a reload", done_persists)
    step("Undo reopens a card", undo_reopens)
    step("Results card shows figures", results_card_shows_figures)
    step("Buried filings stay reachable", buried_is_reachable)
    step("Alert budget responds to limits", budget_moves)
    step("Muting a category demotes it, not deletes it", mute_demotes)
    step("A promise opens its verbatim source", receipts_open)
    step("Watch and unwatch a promise", pin_a_promise)
    step("Tapping a bar reads it", bars_respond)
    step("A bad ticker explains itself", bad_ticker)
    step("No sideways scrolling on any screen", no_sideways_scroll)
    step("Controls are big enough to tap", touch_targets)
    step("Last brief survives going offline", offline_keeps_the_last_brief)
    step("Custom dates narrow the brief", custom_dates)
    step("Chat answers, refuses advice and clears", chat_answers)
    step("Companies compare side by side", compare_companies)
    step("A company shows its price and chart", live_price)
    step("Holdings list shows live prices", holdings_list)
    step("Peers and forces appear in a dossier", new_sections)
    step("The brief says what's new since you last looked", since_last_visit)
    step("Chat keeps the company for a follow-up", chat_remembers_the_company)
    step("Chat says hi back and stays on the company", chat_says_hi)
    step("Limits show live moves during market hours", moving_now_banner)
    step("Holdings can be edited and survive a reload", own_holdings)
    step("Your holdings decide what the brief shows", own_holdings_change_the_brief)
    step("The brief shows what's coming up", coming_up)
    step("Press releases get their own shelf", company_news_shelf)
    step("A one-page summary prints cleanly", one_page_summary)
    step("Four tabs route correctly", tab_routes)
    b.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed; page errors: {errors[:3]}")
sys.exit(1 if failed else 0)
