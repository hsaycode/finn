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
from . import llm, nse, prices
from .net import FetchError
from .store import db, set_meta

PORTFOLIO = (
    "RELIANCE TCS HDFCBANK MARUTI SUNPHARMA LT TITAN ASIANPAINT "
    "DIXON POLYCAB PERSISTENT FEDERALBNK KAYNES ETERNAL IDEA SUZLON ADANIENT JPPOWER"
).split()
ENRICH_DAYS = 45


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
    rows = nse.announcements(symbol, frm, to)
    conn = db()
    new = 0
    for f in rows:
        exists = conn.execute("SELECT 1 FROM filings WHERE id=?", (f.id,)).fetchone()
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
    revenue = {r["symbol"]: r["ttm_revenue"] for r in conn.execute("SELECT symbol, ttm_revenue FROM companies")}
    log(f"enrich: {len(todo)} of {len(rows)} recent filings need their PDF read")

    def work(row):
        try:
            return row["id"], E.enrich(row, revenue.get(row["symbol"]))
        except Exception as e:  # one bad PDF must never stop the run
            return row["id"], {"error": f"{type(e).__name__}: {e}"}

    errors = 0
    # db() is per-thread, so model calls log safely from workers.
    with ThreadPoolExecutor(max_workers=workers if llm.provider() else 2) as pool:
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


def refresh(symbols: list[str] | None = None, days: int = 45, log=print) -> dict:
    syms = symbols or PORTFOLIO
    now = datetime.now()
    frm = now - timedelta(days=days)
    summary = {"new_filings": 0, "failed": []}
    for sym in syms:
        try:
            refresh_company(sym)
            refresh_prices(sym)
            n = ingest_filings(sym, frm, now)
            summary["new_filings"] += n
            log(f"{sym}: +{n} filings")
        except (FetchError, ValueError, KeyError, TypeError) as e:
            summary["failed"].append(sym)
            log(f"{sym}: FAILED {str(e)[:160]}")
    summary.update(enrich_pending(days=min(days, ENRICH_DAYS), symbols=syms, log=log))
    thread_events(syms)
    # "Filings checked 2 minutes ago" has to be true, so a run that reached nothing doesn't count.
    if len(summary["failed"]) < len(syms):
        set_meta("last_refresh", now.isoformat(timespec="seconds"))
    set_meta("last_summary", summary)
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["refresh", "enrich", "thread", "reclassify", "rerank", "people"])
    p.add_argument("--days", type=int, default=45)
    p.add_argument("--symbols")
    a = p.parse_args()
    symbols = a.symbols.split(",") if a.symbols else None
    try:
        if a.command == "refresh":
            print(json.dumps(refresh(symbols, a.days)))
        elif a.command == "enrich":
            print(json.dumps(enrich_pending(a.days, symbols)))
            thread_events(symbols)
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
