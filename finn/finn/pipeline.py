"""The batch job: pull, classify, read what matters, group into events.

    python -m finn.pipeline refresh            # filings, prices, results for the portfolio
    python -m finn.pipeline refresh --days 45  # with a longer lookback
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from . import classify as C
from . import enrich as E
from . import llm, nse, prices, screener
from .net import FetchError
from .store import db, get_meta, set_meta

PORTFOLIO = (
    "RELIANCE TCS HDFCBANK MARUTI SUNPHARMA LT TITAN ASIANPAINT "
    "DIXON POLYCAB PERSISTENT FEDERALBNK KAYNES ETERNAL IDEA SUZLON ADANIENT JPPOWER"
).split()
ENRICH_DAYS = 45
# SEBI allows 45 days for a quarter and 60 for the year. Past this, a results filing can't be about the last quarter on record.
MAX_DAYS_TO_REPORT = 75
_SOURCE_ERRORS = (FetchError, ValueError, KeyError, TypeError, IndexError)


def ttm(quarters: list[nse.Quarter]) -> tuple[float | None, float | None]:
    last = quarters[:4]
    if len(last) < 4 or any(q.revenue is None for q in last):
        return None, None
    return round(sum(q.revenue for q in last), 2), round(sum(q.pat or 0 for q in last), 2)


def refresh_company(symbol: str, in_portfolio: bool = True) -> list[nse.Quarter]:
    qs = nse.quarters(symbol)
    conn = db()
    for q in qs:
        conn.execute("INSERT OR REPLACE INTO quarters VALUES (?,?,?)", (symbol, q.period_end, json.dumps(q.to_dict())))
    rev, pat = ttm(qs)
    conn.execute(
        "INSERT INTO companies (symbol, name, ttm_revenue, ttm_pat, banking, in_portfolio, updated) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(symbol) DO UPDATE SET ttm_revenue=excluded.ttm_revenue, ttm_pat=excluded.ttm_pat, banking=excluded.banking, "
        "in_portfolio=MAX(in_portfolio, excluded.in_portfolio), updated=excluded.updated",
        (symbol, symbol, rev, pat, int(bool(qs) and qs[0].format in ("banking", "nbfc")), int(in_portfolio), datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return qs


def refresh_prices(symbol: str) -> int:
    rows = prices.history(symbol)
    conn = db()
    conn.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?)", [(symbol, r["date"], r["close"], r["volume"]) for r in rows])
    conn.commit()
    return len(rows)


def ingest_filings(symbol: str, frm: datetime, to: datetime) -> int:
    return _store_filings(nse.announcements(symbol, frm, to))


def _name_key(name: str | None) -> str:
    """NSE writes "Limited" and screener writes "Ltd", so names are compared without either."""
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\b(ltd|limited)\b\.?", "", (name or "").lower().replace("&", "and")))


def _symbols_by_name(symbols: list[str]) -> dict[str, str]:
    marks = ",".join("?" for _ in symbols)
    return {_name_key(r["name"]): r["symbol"] for r in db().execute(f"SELECT symbol, name FROM companies WHERE symbol IN ({marks})", symbols) if r["name"]}


def filings_from_feed(symbols: list[str]) -> int:
    """Today's filings from NSE's archive RSS, for when its API refuses this host."""
    names = _symbols_by_name(symbols)
    wanted = set(symbols)
    rows = []
    for f in nse.feed_filings():
        # A company followed for the first time may have no name yet, but its filing's file name starts with its ticker.
        m = re.search(r"/corporate/([A-Z0-9&-]+?)_", f.pdf_url or "")
        f.symbol = names.get(_name_key(f.name)) or (m[1] if m and m[1] in wanted else "")
        if f.symbol:
            rows.append(f)
    return _store_filings(rows)


def _store_quarter(q: nse.Quarter) -> bool:
    """Adds one quarter. A series stays on one basis, and an estimate never replaces a filed figure."""
    conn = db()
    rows = [json.loads(r["data"]) for r in conn.execute("SELECT data FROM quarters WHERE symbol=? ORDER BY period_end DESC", (q.symbol,))]
    if rows and rows[0]["basis"] != q.basis:
        return False
    same = next((r for r in rows if r["period_end"] == q.period_end), None)
    if same and q.provider == "screener":
        return False
    # Stored rows say how this company reports, which a table's headings can't.
    if rows and q.provider == "screener":
        q.format = rows[0]["format"]
    conn.execute("INSERT OR REPLACE INTO quarters VALUES (?,?,?)", (q.symbol, q.period_end, json.dumps(q.to_dict())))
    return True


def _update_company_totals(symbol: str, in_portfolio: bool, name: str | None = None) -> None:
    conn = db()
    qs = [nse.Quarter(**json.loads(r["data"])) for r in conn.execute("SELECT data FROM quarters WHERE symbol=? ORDER BY period_end DESC", (symbol,))]
    rev, pat = ttm(qs)
    conn.execute(
        "INSERT INTO companies (symbol, name, ttm_revenue, ttm_pat, banking, in_portfolio, updated) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(symbol) DO UPDATE SET ttm_revenue=excluded.ttm_revenue, ttm_pat=excluded.ttm_pat, banking=excluded.banking, "
        "in_portfolio=MAX(in_portfolio, excluded.in_portfolio), updated=excluded.updated",
        (symbol, name or symbol, rev, pat, int(bool(qs) and qs[0].format in ("banking", "nbfc")), int(in_portfolio), datetime.now().isoformat(timespec="seconds")),
    )
    if name:
        conn.execute("UPDATE companies SET name=? WHERE symbol=? AND name=symbol", (name, symbol))
    conn.commit()


def results_from_feed(symbols: list[str]) -> int:
    """Exact XBRL for results filed lately. The feed names the company, and the filing confirms its ticker."""
    names = _symbols_by_name(symbols)
    added = 0
    for r in nse.feed_results():
        symbol = names.get(_name_key(r["name"]))
        if not symbol:
            continue
        q = nse.quarter_from_xbrl(r["xbrl"], r["filed_at"])
        if q and q.symbol == symbol and q.pbt is not None and _store_quarter(q):
            added += 1
            _update_company_totals(symbol, symbol in PORTFOLIO)
    return added


def results_from_screener(symbol: str, in_portfolio: bool, company: screener.Company | None = None) -> list[nse.Quarter]:
    """With NSE out of reach, the quarters a new company needs and any results the feed's short list missed."""
    c = company or screener.company(symbol)
    qs = screener.quarters(c)
    for q in qs:
        _store_quarter(q)
    _update_company_totals(symbol, in_portfolio, c.name)
    return qs


def _behind_on_results(symbol: str) -> bool:
    """A results filing with no matching figures, or no figures at all. Only then is it worth asking screener."""
    conn = db()
    latest = conn.execute("SELECT MAX(period_end) m FROM quarters WHERE symbol=?", (symbol,)).fetchone()["m"]
    if not latest:
        return True
    filed = conn.execute("SELECT MAX(ts) m FROM filings WHERE symbol=? AND category='results'", (symbol,)).fetchone()["m"]
    return bool(filed) and (datetime.fromisoformat(filed[:10]) - datetime.fromisoformat(latest)).days > MAX_DAYS_TO_REPORT


def calendar_from_feed(symbols: list[str]) -> int:
    """Adds newly announced dates. Unlike the API it covers only what was filed lately, so nothing already known is cleared."""
    names = _symbols_by_name(symbols)
    rows = [{**e, "symbol": names[_name_key(e["name"])]} for e in nse.feed_events() if _name_key(e["name"]) in names]
    conn = db()
    conn.executemany("INSERT OR REPLACE INTO events VALUES (:symbol, :date, :kind, :title)", rows)
    # A company can file one meeting more than once, and the board meeting that approves results is the results.
    conn.execute("DELETE FROM events WHERE kind='board' AND EXISTS (SELECT 1 FROM events r WHERE r.kind='results' AND r.symbol=events.symbol AND r.date=events.date)")
    conn.commit()
    return len(rows)


def _store_filings(rows: list[nse.Filing]) -> int:
    conn = db()
    new = 0
    for f in rows:
        # The API and the feed give one filing different ids, but always the same document.
        exists = conn.execute("SELECT 1 FROM filings WHERE id=? OR (pdf_url=? AND symbol=?)", (f.id, f.pdf_url, f.symbol)).fetchone()
        if exists:
            continue
        v = C.classify(f.raw_category, f.text)
        title = E.clean_title(f.text, f.raw_category) or C.CATEGORIES.get(v.category, f.raw_category)
        conn.execute(
            "INSERT INTO filings (id,symbol,name,ts,raw_category,text,pdf_url,industry,category,score,tier,title,reason,detail,enriched) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (f.id, f.symbol, f.name, f.ts, f.raw_category, f.text, f.pdf_url, f.industry, v.category, v.score, C.tier(v.score), title[:180], v.reason, "{}"),
        )
        conn.execute("UPDATE companies SET name=? WHERE symbol=?", (f.name, f.symbol))
        new += 1
    conn.commit()
    return new


def enrich_pending(days: int = ENRICH_DAYS, symbols: list[str] | None = None, workers: int = 6, log=print) -> dict:
    """Read PDFs only for filings classify() says could matter, and only recent ones."""
    conn = db()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    want = 2 if llm.available() else 1
    rows = [dict(r) for r in conn.execute("SELECT * FROM filings WHERE ts>=? AND enriched<? ORDER BY ts DESC", (since, want))]
    if symbols:
        rows = [r for r in rows if r["symbol"] in symbols]
    todo = [r for r in rows if C.classify(r["raw_category"], r["text"] or "").read_pdf]
    if llm.frugal():
        todo = todo[:15]  # the newest first, and the rest next time, so research keeps some allowance
    revenue = {r["symbol"]: r["ttm_revenue"] for r in conn.execute("SELECT symbol, ttm_revenue FROM companies")}
    log(f"enrich: {len(todo)} of {len(rows)} recent filings need their PDF read")

    def work(row):
        try:
            return row["id"], E.enrich(row, revenue.get(row["symbol"]))
        except Exception as e:  # one bad PDF must never stop the run
            return row["id"], {"error": f"{type(e).__name__}: {e}"}

    errors = 0
    # db() is per-thread, so model calls log safely from workers.
    with ThreadPoolExecutor(max_workers=2 if llm.frugal() or not llm.provider() else workers) as pool:
        for i, (fid, out) in enumerate(pool.map(work, todo), 1):
            if "error" in out:
                errors += 1
                log(f"  ! {fid}: {out['error'][:120]}")
                continue
            conn.execute(
                "UPDATE filings SET category=?, score=?, tier=?, title=?, reason=?, detail=?, enriched=? WHERE id=?",
                (out["category"], out["score"], out["tier"], out["title"], out["reason"], json.dumps(out["detail"], ensure_ascii=False), out["enriched"], fid),
            )
            # Commit per row: workers log model calls on their own connections.
            conn.commit()
            if i % 25 == 0:
                log(f"  {i}/{len(todo)}  spent ${llm.spent_usd():.3f}")
    conn.commit()
    return {"read": len(todo), "errors": errors}


def rerank(log=print) -> dict:
    """Apply new scores to every stored filing. Answers about PDFs are cached, so this calls no model for ones already read."""
    conn = db()
    plain = reclassify()
    rows = [dict(r) for r in conn.execute("SELECT * FROM filings WHERE enriched>0")]
    revenue = {r["symbol"]: r["ttm_revenue"] for r in conn.execute("SELECT symbol, ttm_revenue FROM companies")}
    changed = 0
    for r in rows:
        try:
            out = E.enrich(r, revenue.get(r["symbol"]))
        except Exception as e:
            log(f"  ! {r['id']}: {type(e).__name__}")
            continue
        # A person's brief is added later, so re-reading the filing mustn't lose it.
        old = (json.loads(r["detail"] or "{}").get("person")) or {}
        new = out["detail"].get("person")
        if new and old:
            out["detail"]["person"] = {**old, **new}
        changed += out["tier"] != r["tier"]
        conn.execute("UPDATE filings SET category=?, score=?, tier=?, title=?, reason=?, detail=?, enriched=? WHERE id=?",
                     (out["category"], out["score"], out["tier"], out["title"], out["reason"], json.dumps(out["detail"], ensure_ascii=False), out["enriched"], r["id"]))
    conn.commit()
    thread_events()
    return {"plain": plain, "read": len(rows), "tier_changed": changed}


def reclassify() -> int:
    """Apply rule changes to filings whose PDFs weren't read. Nothing here calls a model."""
    conn = db()
    rows = conn.execute("SELECT id, raw_category, text FROM filings WHERE enriched=0").fetchall()
    for r in rows:
        v = C.classify(r["raw_category"], r["text"] or "")
        title = E.clean_title(r["text"] or "", r["raw_category"]) or C.CATEGORIES.get(v.category, r["raw_category"])
        conn.execute("UPDATE filings SET category=?, score=?, tier=?, reason=?, title=? WHERE id=?", (v.category, v.score, C.tier(v.score), v.reason, title[:180], r["id"]))
    conn.commit()
    return len(rows)


def thread_events(symbols: list[str] | None = None) -> None:
    """One event, one card. A results day produces a dozen filings; they're all the same story."""
    conn = db()
    syms = symbols or [r["symbol"] for r in conn.execute("SELECT DISTINCT symbol FROM filings")]
    for sym in syms:
        conn.execute("UPDATE filings SET thread=NULL WHERE symbol=?", (sym,))
        rows = [dict(r) for r in conn.execute("SELECT id, ts, category, title, score, raw_category, text FROM filings WHERE symbol=? ORDER BY ts", (sym,))]
        threads: dict[str, str] = {}
        results = [r for r in rows if r["category"] == "results"]
        anchors: list[tuple[datetime, str]] = []
        for r in results:
            day = datetime.fromisoformat(r["ts"])
            if anchors and (day - anchors[-1][0]).days <= 3:
                threads[r["id"]] = anchors[-1][1]
                continue
            tid = f"results-{sym}-{day:%Y%m%d}"
            anchors.append((day, tid))
            threads[r["id"]] = tid
        companion = {"earnings_call", "dividend", "press_release", "investor_meetings", "unclassified"}
        for r in rows:
            if r["id"] in threads or r["category"] not in companion:
                continue
            t = datetime.fromisoformat(r["ts"])
            for day, tid in anchors:
                hours = (t - day).total_seconds() / 3600
                # A press release only joins if it's about the results: monthly sales news is its own story.
                text = f"{r['title']} {r['text'] or ''}".lower()
                about_results = bool(re.search(r"result|quarter|q[1-4]\b|half[- ]year|earnings", text))
                same_story = r["category"] in ("earnings_call", "dividend") or r["raw_category"] in (
                    "Copy of Newspaper Publication", "Investor Presentation", "Analysts/Institutional Investor Meet/Con. Call Updates",
                ) or (r["raw_category"].startswith("Press Release") and about_results)
                # The transcript follows days later but still belongs to this quarter's story.
                reach = 24 * 21 if "transcript" in (r["title"] or "").lower() else 24 * 5
                if same_story and -30 <= hours <= reach:
                    threads[r["id"]] = tid
                    break
        # Running stories (a QIP, a court case, a takeover) file repeatedly: chain them while they're active.
        last: dict[str, tuple[datetime, str]] = {}
        for r in rows:
            if r["id"] in threads or r["category"] not in ("fund_raise", "litigation", "M&A", "rumour"):
                continue
            t = datetime.fromisoformat(r["ts"])
            prev = last.get(r["category"])
            tid = prev[1] if prev and (t - prev[0]).days <= 12 else f"{r['category']}-{sym}-{t:%Y%m%d}"
            threads[r["id"]] = tid
            last[r["category"]] = (t, tid)
        for fid, tid in threads.items():
            conn.execute("UPDATE filings SET thread=? WHERE id=?", (tid, fid))
    conn.commit()


def watched() -> list[str]:
    """The default portfolio, plus whatever anyone has added to theirs."""
    extra = [r["symbol"] for r in db().execute("SELECT symbol FROM tracked ORDER BY added DESC LIMIT 100")]
    return PORTFOLIO + [s for s in extra if s not in PORTFOLIO]


def track(symbol: str) -> None:
    """Start following a company someone added to their holdings."""
    conn = db()
    conn.execute("INSERT OR REPLACE INTO tracked VALUES (?, ?)", (symbol, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    now = datetime.now()
    try:
        refresh_company(symbol, in_portfolio=False)
        ingest_filings(symbol, now - timedelta(days=60), now)
        refresh_calendar([symbol])
    except _SOURCE_ERRORS:
        results_from_screener(symbol, False)
        filings_from_feed([symbol])
        calendar_from_feed([symbol])
    refresh_prices(symbol)
    thread_events([symbol])


def refresh_calendar(symbols: list[str]) -> int:
    conn = db()
    count = 0
    for sym in symbols:
        try:
            events = nse.upcoming_events(sym)
        except (FetchError, ValueError, KeyError, TypeError):
            continue  # keep what we had: dates a company has announced don't stop being true
        conn.execute("DELETE FROM events WHERE symbol=? AND date>=?", (sym, datetime.now().date().isoformat()))
        conn.executemany("INSERT OR REPLACE INTO events VALUES (:symbol, :date, :kind, :title)", events)
        count += len(events)
    conn.commit()
    return count


def read_backlog(limit: int = 20, model: bool = True, log=print) -> dict:
    """Older filings that were only ranked from their title. Reads the most important first, so a day's
    free allowance goes where it matters, and stops when it runs out. Safe to run again tomorrow."""
    conn = db()
    wanted = "enriched<2" if model else "enriched<1"
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM filings WHERE {wanted} ORDER BY score DESC, ts DESC")]
    todo = [r for r in rows if C.classify(r["raw_category"], r["text"] or "").read_pdf][:limit]
    revenue = {r["symbol"]: r["ttm_revenue"] for r in conn.execute("SELECT symbol, ttm_revenue FROM companies")}
    done = 0

    def read(row):
        try:
            return row["id"], E.enrich(row, revenue.get(row["symbol"]))
        except Exception as e:  # one bad PDF must never stop the run
            log(f"  ! {row['id']}: {type(e).__name__}")
            return row["id"], None

    for row in todo:
        if model and llm.frugal() and not llm.available():
            log("today's free allowance is used up; run again tomorrow")
            break
        if model:
            fid, out = read(row)
        else:
            with llm.no_model():
                fid, out = read(row)
        if out:
            # With no model, or none left, a filing keeps any earlier reading rather than going backwards.
            enriched = max(out["enriched"], row["enriched"] or 0)
            conn.execute("UPDATE filings SET category=?, score=?, tier=?, title=?, reason=?, detail=?, enriched=? WHERE id=?",
                         (out["category"], out["score"], out["tier"], out["title"], out["reason"], json.dumps(out["detail"], ensure_ascii=False), enriched, fid))
            conn.commit()
            done += 1
    return {"read": done, "remaining": len([r for r in rows if C.classify(r["raw_category"], r["text"] or "").read_pdf]) - done}


def refresh(symbols: list[str] | None = None, days: int = 45, log=print) -> dict:
    syms = symbols or watched()
    now = datetime.now()
    frm = now - timedelta(days=days)
    summary = {"new_filings": 0, "failed": [], "prices_failed": [], "sources": {}}
    sources = summary["sources"]
    conn = db()
    marks = ",".join("?" for _ in syms)
    held_until = conn.execute(f"SELECT MAX(ts) m FROM filings WHERE symbol IN ({marks})", syms).fetchone()["m"]
    behind: list[str] = []
    refusals = 0
    for sym in syms:
        # Prices come from Yahoo, so trouble at NSE mustn't hold them back.
        try:
            refresh_prices(sym)
        except _SOURCE_ERRORS as e:
            summary["prices_failed"].append(sym)
            sources["yahoo"] = _reason(e)
            log(f"{sym}: prices FAILED {str(e)[:160]}")
        # Two refusals in a row mean NSE's API won't serve this host today, so the rest needn't wait out their retries.
        if refusals >= 2:
            behind.append(sym)
            continue
        try:
            refresh_company(sym, in_portfolio=sym in PORTFOLIO)
            n = ingest_filings(sym, frm, now)
            summary["new_filings"] += n
            refusals = 0
            log(f"{sym}: +{n} filings")
        except _SOURCE_ERRORS as e:
            behind.append(sym)
            refusals += isinstance(e, FetchError)
            sources["nse_api"] = _reason(e)
            log(f"{sym}: NSE API FAILED {str(e)[:160]}")
    sources.setdefault("yahoo", "ok")
    if len(behind) < len(syms):
        sources.setdefault("nse_api", "ok")
    if behind:
        _catch_up(behind, summary, held_until, now, log)
    else:
        set_meta("filings_gap", None)
    reached = [s for s in syms if s not in summary["failed"]]
    summary.update(enrich_pending(days=min(days, ENRICH_DAYS), symbols=reached or syms, log=log))
    refresh_calendar([s for s in syms if s not in behind])
    # Whatever the free allowance has left over goes to older filings, a little at a time.
    if llm.frugal() and llm.free_room_share() > 0.6:
        read_backlog(limit=5, log=log)
    thread_events(syms)
    # "Filings checked 2 minutes ago" has to be true, so a run that reached nothing doesn't count.
    if reached:
        set_meta("last_refresh", now.isoformat(timespec="seconds"))
    set_meta("last_summary", summary)
    log(f"sources: {sources}")
    return summary


def _reason(e: Exception) -> str:
    """Enough to tell a block from an outage, without a long address."""
    return f"{type(e).__name__}: {str(e).rsplit(': ', 1)[-1][:80]}"


def _catch_up(behind: list[str], summary: dict, held_until: str | None, now: datetime, log) -> None:
    """NSE's API refused these. Its archive feeds carry today's filings and the latest results, and screener fills in the rest."""
    sources = summary["sources"]
    try:
        n = filings_from_feed(behind)
        summary["new_filings"] += n
        sources["nse_feed"] = "ok"
        log(f"feed: +{n} filings for {len(behind)} companies")
        # The feed only goes back to midnight, so anything filed between the last good data and today can't be seen.
        if held_until and held_until[:10] < now.date().isoformat() and not get_meta("filings_gap"):
            set_meta("filings_gap", {"from": held_until[:10], "to": now.date().isoformat()})
    except _SOURCE_ERRORS as e:
        summary["failed"] = list(behind)
        sources["nse_feed"] = _reason(e)
        log(f"feed FAILED {str(e)[:160]}")
    for step in (results_from_feed, calendar_from_feed):
        try:
            log(f"{step.__name__}: {step(behind)}")
        except _SOURCE_ERRORS as e:
            log(f"{step.__name__} FAILED {str(e)[:160]}")
    for sym in behind:
        if not _behind_on_results(sym):
            continue
        try:
            results_from_screener(sym, sym in PORTFOLIO)
            sources["screener"] = "ok"
        except _SOURCE_ERRORS as e:
            sources["screener"] = _reason(e)
            log(f"{sym}: screener FAILED {str(e)[:160]}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["refresh", "enrich", "thread", "reclassify", "rerank", "people", "backlog", "calendar"])
    p.add_argument("--days", type=int, default=45)
    p.add_argument("--symbols")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--rules-only", action="store_true")
    a = p.parse_args()
    symbols = a.symbols.split(",") if a.symbols else None
    try:
        if a.command == "refresh":
            print(json.dumps(refresh(symbols, a.days)))
        elif a.command == "enrich":
            print(json.dumps(enrich_pending(a.days, symbols)))
            thread_events(symbols)
        elif a.command == "backlog":
            print(json.dumps(read_backlog(a.limit, model=not a.rules_only)))
            thread_events(symbols)
        elif a.command == "calendar":
            print(refresh_calendar(symbols or watched()))
        elif a.command == "rerank":
            print(json.dumps(rerank()))
        elif a.command == "people":
            from . import people

            print(people.enrich_all(a.days))
        elif a.command == "reclassify":
            print(reclassify())
            thread_events(symbols)
        else:
            thread_events(symbols)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
