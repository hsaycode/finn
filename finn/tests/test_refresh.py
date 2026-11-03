"""What happens when NSE's API refuses the server. Render's address was refused for all 18 holdings, which froze the live app."""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from finn import api, chat, nse, pipeline, research, screener
from finn.net import FetchError
from finn.store import db, get_meta, set_meta

client = TestClient(api.app)
TODAY = datetime.now().replace(microsecond=0)


def filing(name="Aaa Limited", link="https://nsearchives.nseindia.com/corporate/AAA_1.pdf", label="Press Release", text="New order won", when=TODAY):
    return nse.Filing(id=f"rss-{link[-9:]}", symbol="", name=name, ts=when.isoformat(), raw_category=label, text=text, pdf_url=link)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for t in ("filings", "companies", "quarters", "prices", "dossiers", "events", "meta"):
        db().execute(f"DELETE FROM {t}")
    db().execute("INSERT INTO companies (symbol,name,in_portfolio) VALUES ('AAA','Aaa Limited',1), ('BBB','Bbb Ltd',1)")
    db().commit()
    monkeypatch.setattr(pipeline, "PORTFOLIO", ["AAA", "BBB"])
    monkeypatch.setattr(pipeline, "enrich_pending", lambda **k: {"read": 0, "errors": 0})
    monkeypatch.setattr(pipeline, "refresh_prices", lambda s: 0)
    monkeypatch.setattr(pipeline, "refresh_calendar", lambda s: 0)
    monkeypatch.setattr(nse, "feed_results", lambda **k: [])
    monkeypatch.setattr(nse, "feed_events", lambda **k: [])
    monkeypatch.setattr(pipeline, "_behind_on_results", lambda s: False)


def nse_refuses(*a, **k):
    raise FetchError("https://www.nseindia.com/api/x: HTTP 403", 403)


def test_the_feed_stands_in_when_the_api_refuses(monkeypatch):
    monkeypatch.setattr(pipeline, "refresh_company", nse_refuses)
    monkeypatch.setattr(nse, "feed_filings", lambda: [filing(), filing("Someone Else Limited", "https://nsearchives.nseindia.com/corporate/ZZZ_1.pdf")])
    s = pipeline.refresh(["AAA", "BBB"], days=1, log=lambda *_: None)
    assert s["new_filings"] == 1 and s["failed"] == []
    assert s["sources"]["nse_api"].startswith("FetchError") and s["sources"]["nse_feed"] == "ok"
    assert [r["symbol"] for r in db().execute("SELECT symbol FROM filings")] == ["AAA"]
    assert get_meta("last_refresh")


def test_screeners_ltd_matches_nses_limited(monkeypatch):
    monkeypatch.setattr(nse, "feed_filings", lambda: [filing("Bbb Limited", "https://nsearchives.nseindia.com/corporate/QQQ_1.pdf")])
    assert pipeline.filings_from_feed(["BBB"]) == 1


def test_a_new_companys_filing_is_known_by_its_file_name(monkeypatch):
    db().execute("UPDATE companies SET name='BBB' WHERE symbol='BBB'")
    monkeypatch.setattr(nse, "feed_filings", lambda: [filing("Bbb Holdings Limited", "https://nsearchives.nseindia.com/corporate/BBB_03112026_x.pdf")])
    assert pipeline.filings_from_feed(["BBB"]) == 1


def test_one_filing_from_two_doors_is_one_filing(monkeypatch):
    link = "https://nsearchives.nseindia.com/corporate/AAA_1.pdf"
    monkeypatch.setattr(nse, "announcements", lambda *a, **k: [nse.Filing(id="123", symbol="AAA", name="Aaa Limited", ts=TODAY.isoformat(), raw_category="Press Release", text="New order won", pdf_url=link)])
    assert pipeline.ingest_filings("AAA", TODAY, TODAY) == 1
    monkeypatch.setattr(nse, "feed_filings", lambda: [filing(link=link)])
    assert pipeline.filings_from_feed(["AAA"]) == 0
    assert db().execute("SELECT COUNT(*) n FROM filings").fetchone()["n"] == 1


def test_prices_keep_coming_while_nse_refuses(monkeypatch):
    done = []
    monkeypatch.setattr(pipeline, "refresh_prices", lambda s: done.append(s))
    monkeypatch.setattr(pipeline, "refresh_company", nse_refuses)
    monkeypatch.setattr(nse, "feed_filings", nse_refuses)
    pipeline.refresh(["AAA", "BBB"], days=1, log=lambda *_: None)
    assert done == ["AAA", "BBB"]


def test_two_refusals_stop_the_rest_waiting_out_retries(monkeypatch):
    asked = []

    def refuse(symbol, **k):
        asked.append(symbol)
        nse_refuses()

    monkeypatch.setattr(pipeline, "refresh_company", refuse)
    monkeypatch.setattr(nse, "feed_filings", lambda: [])
    db().execute("INSERT INTO companies (symbol,name,in_portfolio) VALUES ('CCC','Ccc Limited',1), ('DDD','Ddd Limited',1)")
    pipeline.refresh(["AAA", "BBB", "CCC", "DDD"], days=1, log=lambda *_: None)
    assert asked == ["AAA", "BBB"]


def test_filings_the_feed_cant_see_are_admitted_to(monkeypatch):
    # The feed starts at midnight, so a wake-up days after the snapshot leaves a hole.
    old = (TODAY - timedelta(days=4)).isoformat()
    db().execute("INSERT INTO filings (id,symbol,name,ts,raw_category,text,category,score,tier,title,reason,detail) VALUES ('x','AAA','Aaa',?, 'Updates','t','press_release',50,'notable','t','r','{}')", (old,))
    db().commit()
    monkeypatch.setattr(pipeline, "refresh_company", nse_refuses)
    monkeypatch.setattr(nse, "feed_filings", lambda: [])
    pipeline.refresh(["AAA"], days=1, log=lambda *_: None)
    gap = client.get("/api/brief").json()["meta"]["gap"]
    assert gap == {"from": old[:10], "to": TODAY.date().isoformat()}
    # A later run must not narrow it, because that hole is still there.
    pipeline.refresh(["AAA"], days=1, log=lambda *_: None)
    assert client.get("/api/brief").json()["meta"]["gap"]["from"] == old[:10]


def test_reaching_the_api_again_clears_the_gap(monkeypatch):
    set_meta("filings_gap", {"from": "2026-01-01", "to": "2026-01-05"})
    monkeypatch.setattr(pipeline, "refresh_company", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "ingest_filings", lambda *a: 0)
    pipeline.refresh(["AAA"], days=1, log=lambda *_: None)
    assert get_meta("filings_gap") is None


def test_status_says_which_source_answered(monkeypatch):
    monkeypatch.setattr(pipeline, "refresh_company", nse_refuses)
    monkeypatch.setattr(nse, "feed_filings", lambda: [])
    pipeline.refresh(["AAA"], days=1, log=lambda *_: None)
    sources = client.get("/api/status").json()["sources"]
    assert "403" in sources["nse_api"] and sources["nse_feed"] == "ok" and sources["yahoo"] == "ok"


RSS = b"""<rss><channel>
<item><title>Aaa Limited</title><link>https://nsearchives.nseindia.com/corporate/AAA_1.pdf</link>
<description>Aaa Limited has informed the Exchange about New order win |SUBJECT: Press Release</description><pubDate>03-Nov-2026 18:52:12</pubDate></item>
<item><title>Aaa Limited</title><link>https://nsearchives.nseindia.com/corporate/xbrl/AAA_1.xml</link><description>x |SUBJECT: Outcome-XBRL</description><pubDate>03-Nov-2026 18:52:12</pubDate></item>
<item><title>Some Fund</title><link></link><description>x |SUBJECT: Declaration of NAV</description><pubDate>03-Nov-2026 18:52:12</pubDate></item>
</channel></rss>"""


def test_reading_the_feed_keeps_documents_and_their_labels(monkeypatch):
    monkeypatch.setattr(nse, "fetch", lambda *a, **k: RSS)
    rows = nse.feed_filings()
    assert len(rows) == 1
    assert rows[0].raw_category == "Press Release" and rows[0].text == "New order win" and rows[0].ts == "2026-11-03T18:52:12"


XBRL = b"""<xbrl>
<context id="Q"><period><startDate>2026-07-01</startDate><endDate>2026-09-30</endDate></period></context>
<Symbol contextRef="Q">AAA</Symbol><DateOfEndOfReportingPeriod contextRef="Q">2026-09-30</DateOfEndOfReportingPeriod>
<NatureOfReportStandaloneConsolidated contextRef="Q">Consolidated</NatureOfReportStandaloneConsolidated>
<WhetherResultsAreAuditedOrUnaudited contextRef="Q">Unaudited</WhetherResultsAreAuditedOrUnaudited>
<RevenueFromOperations contextRef="Q">1000000000</RevenueFromOperations><ProfitBeforeTax contextRef="Q">200000000</ProfitBeforeTax><ProfitLossForPeriod contextRef="Q">150000000</ProfitLossForPeriod>
</xbrl>"""


def test_an_integrated_filing_says_what_it_covers(monkeypatch):
    monkeypatch.setattr(nse, "fetch", lambda *a, **k: XBRL)
    q = nse.quarter_from_xbrl("https://nsearchives.nseindia.com/corporate/xbrl/x.xml", "2026-11-03T18:00:00")
    assert (q.symbol, q.period_end, q.basis, q.audited, q.revenue, q.pbt) == ("AAA", "2026-09-30", "consolidated", False, 100.0, 20.0)


def test_results_from_the_feed_are_stored_only_for_the_right_company(monkeypatch):
    monkeypatch.setattr(nse, "feed_results", lambda **k: [{"name": "Aaa Limited", "xbrl": "u", "filed_at": "2026-11-03T18:00:00"}, {"name": "Bbb Limited", "xbrl": "u", "filed_at": "2026-11-03T18:00:00"}])
    monkeypatch.setattr(nse, "fetch", lambda *a, **k: XBRL)
    # Both resolve to one XBRL here, whose symbol is AAA, so the one claiming to be Bbb is refused.
    assert pipeline.results_from_feed(["AAA", "BBB"]) == 1
    assert [r["symbol"] for r in db().execute("SELECT symbol FROM quarters")] == ["AAA"]
    assert db().execute("SELECT ttm_revenue FROM companies WHERE symbol='AAA'").fetchone()["ttm_revenue"] is None


def _company(rows, columns=("Jun 2026", "Sep 2026")):
    table = screener.Table(list(columns), rows)
    return screener.Company("AAA", "Aaa Ltd", True, "", None, None, None, {}, {"quarters": table}, source_url="https://www.screener.in/company/AAA/consolidated/")


def test_screeners_table_becomes_quarters():
    qs = screener.quarters(_company({"Sales": [90.0, 100.0], "Operating Profit": [9.0, 12.0], "Profit before tax": [8.0, 10.0], "Net Profit": [6.0, 7.0]}))
    assert [q.period_end for q in qs] == ["2026-09-30", "2026-06-30"]
    assert (qs[0].revenue, qs[0].ebitda, qs[0].pat, qs[0].format, qs[0].provider) == (100.0, 12.0, 7.0, "indas", "screener")


def test_a_lenders_table_gives_net_interest_income():
    q = screener.quarters(_company({"Revenue": [100.0, 110.0], "Interest": [60.0, 70.0], "Financing Profit": [5.0, 6.0], "Profit before tax": [9.0, 9.0], "Net Profit": [7.0, 7.0], "Gross NPA %": [1.2, 1.3]}))[0]
    assert (q.format, q.nii, q.gross_npa_pct, q.ebitda) == ("banking", 40.0, 1.3, None)


def _store(symbol="AAA", end="2026-09-30", **kw):
    fields = {"basis": "consolidated", "format": "indas", "revenue": 100.0, "pbt": 20.0, "pat": 15.0, **kw}
    return pipeline._store_quarter(nse.Quarter(symbol=symbol, period_end=end, **fields))


def test_an_estimate_never_replaces_a_filed_figure():
    assert _store(revenue=100.0)
    assert not _store(revenue=999.0, provider="screener")
    assert _store(end="2026-12-31", provider="screener", revenue=120.0)
    db().commit()
    assert json.loads(db().execute("SELECT data FROM quarters WHERE period_end='2026-09-30'").fetchone()["data"])["revenue"] == 100.0


def test_a_series_stays_on_one_basis():
    assert _store(basis="consolidated")
    assert not _store(end="2026-12-31", basis="standalone")


def test_a_screener_quarter_shows_where_it_came_from():
    _store(provider="screener", source_url="https://www.screener.in/company/AAA/consolidated/")
    db().commit()
    card = research.results_card("AAA", [nse.Quarter(**json.loads(r["data"])) for r in db().execute("SELECT data FROM quarters")])
    assert card["source_label"] == "screener.in's table" and "screener.in" in card["source"]
    said = chat._results("AAA")
    assert "from screener.in's table" in said["text"] and said["items"][-1]["url"] == card["source"]


def _results_filing(when):
    db().execute(
        "INSERT INTO filings (id,symbol,name,ts,raw_category,text,pdf_url,category,score,tier,title,reason,detail) VALUES ('r','AAA','Aaa',?,'Outcome of Board Meeting','t','https://x/r.pdf','results',90,'top','Results filing','New quarterly results filed','{}')",
        (when,),
    )
    db().commit()


def test_new_results_dont_wear_the_last_quarters_numbers():
    _store(end=(TODAY - timedelta(days=110)).date().isoformat())
    _results_filing(TODAY.isoformat())
    event = client.get("/api/brief").json()["top"][0]
    assert event["results"] is None and event["title"] == "Results filing"


def test_results_filed_on_time_keep_their_numbers():
    _store(end=(TODAY - timedelta(days=30)).date().isoformat())
    _results_filing(TODAY.isoformat())
    assert client.get("/api/brief").json()["top"][0]["results"] is not None


def test_a_story_near_a_price_move_is_told_from_its_latest_filing():
    # Sun Pharma's card said its response was awaited, after it had replied.
    for fid, hours, title in (("q", 30, "The response is awaited"), ("a", 20, "The company clarifies")):
        ts = (TODAY - timedelta(hours=hours)).isoformat()
        db().execute(
            "INSERT INTO filings (id,symbol,name,ts,raw_category,text,pdf_url,category,score,tier,title,reason,detail,thread) VALUES (?,'AAA','Aaa',?,'News Verification','t',?,'rumour',66,'notable',?,'r','{}','story')",
            (fid, ts, f"https://x/{fid}.pdf", title),
        )
    db().commit()
    rows = next(c for c in client.get("/api/portfolio").json()["companies"] if c["symbol"] == "AAA")["filings"]
    assert {r["title"] for r in rows} == {"The company clarifies"} and {r["url"] for r in rows} == {"https://x/a.pdf"}


def test_the_research_list_names_each_company_and_its_record():
    research.save("AAA", {"symbol": "AAA", "name": "Aaa Limited", "generated": TODAY.isoformat(), "promises": {"tally": {"delivered": 2, "not_due": 5}, "testable": 4}})
    research.save("ZZZ", {"symbol": "ZZZ", "name": "Zzz Limited", "generated": TODAY.isoformat(), "promises": None})
    got = {c["symbol"]: c for c in client.get("/api/companies").json()["companies"]}
    assert got["AAA"] == {"symbol": "AAA", "name": "Aaa Limited", "held": True, "kept": 2, "checked": 4, "open": 5}
    assert got["ZZZ"]["held"] is False and got["ZZZ"]["checked"] == 0


def test_every_kind_of_argument_fact_can_be_opened():
    traj = {"signals": [{"label": "Revenue", "c3": 10, "c5": 12}], "margin_change_3y": 1.0, "margin_now": 20}
    sheet = {"de": [0.5, 0.4], "columns": ["FY25", "FY26"], "banking": False, "days": {"ccc": [40, 30], "columns": ["FY25", "FY26"]}, "negative_net_worth": True}
    cash = {"banking": False, "cfo_to_pat_5y": 1.1}
    facts = research.collect_facts({}, traj, sheet, cash, None, "https://www.screener.in/company/AAA/")
    assert facts and all(f.get("url") for f in facts)


def test_a_price_move_in_chat_cites_yahoo(monkeypatch):
    rows = [{"date": f"2026-10-{d:02d}", "close": 100.0, "volume": 10.0} for d in range(1, 25)] + [{"date": "2026-10-25", "close": 120.0, "volume": 90.0}]
    db().executemany("INSERT INTO prices VALUES ('AAA',?,?,?)", [(r["date"], r["close"], r["volume"]) for r in rows])
    item = chat._moved(5, 2, ["AAA"])["items"][0]
    assert item["url"] == "https://finance.yahoo.com/quote/AAA.NS" and item["source"] == "Yahoo Finance"


def test_a_definition_says_it_has_no_source(monkeypatch):
    monkeypatch.setattr(chat, "_converse", lambda *a, **k: "Profit before certain costs.")
    assert chat.answer("What is EBITDA?", lambda *a: {})["unsourced"] is True
