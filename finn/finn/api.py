"""HTTP API plus the built web app. One process, so there's one thing to deploy."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import classify as C
from . import chat, docs, llm, nse, pipeline, prices, promises, push, research
from . import transcripts as T
from .enrich import numbers_in_source
from .net import FetchError
from .store import db, get_meta

app = FastAPI(title="FINN")
app.add_middleware(GZipMiddleware, minimum_size=1000)  # the portfolio's price history is 180 KB on a phone
WEB = Path(__file__).resolve().parent.parent / "web" / "dist"
SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}$")
_jobs_lock = threading.Lock()
_running: set[str] = set()
# A big bank's annual reports peak near 470 MB, so on a 512 MB host new tickers build one at a time.
MAX_HOLDINGS = 40
_build_slots = threading.BoundedSemaphore(int(os.environ.get("FINN_BUILD_SLOTS", "1")))
RETRY_INCOMPLETE_AFTER = 600


def _holdings(symbols: str | None = None) -> list[str]:
    """Whoever is asking's own holdings if they sent them, or else the default portfolio."""
    if symbols:
        chosen = [s.strip().upper() for s in symbols.split(",") if s.strip()][:MAX_HOLDINGS]
        if not all(SYMBOL.match(s) for s in chosen):
            raise HTTPException(400, "Those don't look like NSE tickers")
        if chosen:
            return chosen
    return [r["symbol"] for r in db().execute("SELECT symbol FROM companies WHERE in_portfolio=1 ORDER BY symbol")]


def _marks(items: list) -> str:
    return ",".join("?" for _ in items)


def _quarters(symbol: str) -> list[nse.Quarter]:
    rows = db().execute("SELECT data FROM quarters WHERE symbol=? ORDER BY period_end DESC", (symbol,)).fetchall()
    return [nse.Quarter(**json.loads(r["data"])) for r in rows]


def _event(items: list[dict]) -> dict:
    """Merge one thread's filings into a single card, led by its most important filing."""
    items = sorted(items, key=lambda r: (-r["score"], r["ts"]))
    lead = items[0]
    newest = max(r["ts"] for r in items)
    detail = json.loads(lead["detail"] or "{}")
    title, reason, pdf = lead["title"], lead["reason"], lead["pdf_url"]
    # Rank a story by its most serious moment, but tell it from its latest one. A crisis that has
    # since been resolved must not read as if it's still live.
    updates = [r for r in items if r["score"] >= C.NOTABLE_AT]
    latest = max(updates, key=lambda r: r["ts"]) if updates else lead
    # An event's time is when the story last moved, not when its paperwork was last filed.
    newest = latest["ts"] if lead["category"] != "results" else lead["ts"]
    if lead["category"] != "results" and latest["id"] != lead["id"]:
        began = datetime.fromisoformat(min(r["ts"] for r in items)).strftime("%-d %b") if os.name != "nt" else datetime.fromisoformat(min(r["ts"] for r in items)).strftime("%#d %b")
        title, pdf = latest["title"], latest["pdf_url"]
        reason = f"Latest in a story that began {began}: {lead['title']}"
        detail = {**detail, **json.loads(latest["detail"] or "{}")}
    return {
        "id": lead["thread"] or lead["id"],
        "symbol": lead["symbol"], "name": lead["name"], "ts": newest, "category": lead["category"],
        "category_label": C.CATEGORIES.get(lead["category"], "Update"),
        "score": lead["score"], "tier": lead["tier"], "title": title, "reason": reason,
        "detail": detail, "pdf_url": pdf, "evidence": C.evidence_for(lead["reason"], lead["category"], detail.get("person")),
        "filings": [{"id": r["id"], "ts": r["ts"], "title": _filing_label(r), "pdf_url": r["pdf_url"], "reason": r["reason"], "score": r["score"]} for r in sorted(items, key=lambda r: r["ts"], reverse=True)],
    }


@app.get("/api/brief")
def brief(hours: int = Query(24, ge=1, le=24 * 60), since: str | None = None, until: str | None = None, symbols: str | None = None):
    """Everything in the window, as ranked events. The hidden count is part of the product."""
    conn = db()
    for value in (since, until):
        if value:
            try:
                datetime.fromisoformat(value)
            except ValueError:
                raise HTTPException(422, "Dates must look like 2026-10-31")
    start = since or (datetime.now() - timedelta(hours=hours)).isoformat()
    # A bare date as the end means the whole of that day.
    end = (f"{until}T23:59:59" if len(until) == 10 else until) if until else "9999"
    if end < start:
        raise HTTPException(422, "The start date is after the end date")
    mine = _holdings(symbols)
    # Pull each thread whole, even if only its latest filing falls inside the window.
    rows = [dict(r) for r in conn.execute(
        f"SELECT f.* FROM filings f WHERE f.symbol IN ({_marks(mine)}) "
        "AND ((f.ts>=? AND f.ts<=?) OR f.thread IN (SELECT thread FROM filings WHERE ts>=? AND ts<=? AND thread IS NOT NULL)) ORDER BY f.ts DESC",
        (*mine, start, end, start, end),
    )]
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["thread"] or r["id"]].append(r)
    # A follow-up (a transcript, an ad) shouldn't drag week-old results back to the top of today's brief.
    events = [_event(g) for g in groups.values() if _lead_in_window(g, start, end)]
    events.sort(key=lambda e: (-e["score"], e["ts"]), reverse=False)

    for e in events:
        _decorate(e)

    top = [e for e in events if e["tier"] == C.TOP]
    notable = [e for e in events if e["tier"] == C.NOTABLE]
    routine = [e for e in events if e["tier"] == C.ROUTINE]
    in_window = sum(1 for r in rows if start <= r["ts"] <= end)
    last_run = get_meta("last_summary") or {}
    oldest = conn.execute("SELECT MIN(ts) m FROM filings").fetchone()["m"]
    meta = {"last_refresh": get_meta("last_refresh"), "window_start": start, "unreached": last_run.get("failed", []), "oldest_filing": oldest[:10] if oldest else None}
    latest_notable = None
    if not top:
        # A quiet weekend shouldn't look broken: show what last mattered instead of a blank screen.
        lead = conn.execute(
            f"SELECT f.id, f.thread FROM filings f WHERE f.symbol IN ({_marks(mine)}) AND f.tier='top' ORDER BY f.ts DESC LIMIT 1", mine
        ).fetchone()
        if lead:
            group = conn.execute("SELECT * FROM filings WHERE thread=?", (lead["thread"],)).fetchall() if lead["thread"] else conn.execute("SELECT * FROM filings WHERE id=?", (lead["id"],)).fetchall()
            latest_notable = _decorate(_event([dict(r) for r in group]))
    return {"top": top, "notable": notable, "routine": routine, "total_filings": in_window, "latest_notable": latest_notable, "meta": meta}


def _filing_label(r: dict) -> str:
    """Results figures only come from code, so a model's summary of a results filing never appears."""
    if r["category"] != "results":
        return r["title"]
    return "Company's press release" if (r["raw_category"] or "").startswith("Press Release") else "Results filing"


def _decorate(e: dict) -> dict:
    """Results get a headline written by code from the filed figures, never by a model."""
    if e["category"] == "results":
        # A model's sense of a results filing's tone can contradict its figures, so the figures speak alone.
        e["detail"].pop("tone", None)
        e["results"] = _card_for(e)
        e["promise_check"] = _promise_check(e)
        if e["results"]:
            e["title"] = research.results_headline(e["results"])
    person = e["detail"].get("person")
    if person and person.get("name") and not person.get("profile"):
        person["profile"] = _profile_from_other_filings(e["symbol"], person["name"])
    return e


def _profile_from_other_filings(symbol: str, name: str) -> list[str]:
    """Companies usually file a person's background once, at appointment. Later filings about them shouldn't lose it."""
    key = name.lower().strip()
    for r in db().execute("SELECT detail FROM filings WHERE symbol=? AND category='management_change' AND detail LIKE '%profile%' ORDER BY ts DESC", (symbol,)):
        other = json.loads(r["detail"] or "{}").get("person") or {}
        if (other.get("name") or "").lower().strip() == key and other.get("profile"):
            return other["profile"]
    return []


def _lead_in_window(group: list[dict], start: str, end: str = "9999") -> bool:
    inside = [r for r in group if start <= r["ts"] <= end]
    return any(r["score"] >= C.NOTABLE_AT for r in inside) or (bool(inside) and all(r["score"] < C.NOTABLE_AT for r in group))


def _card_for(event: dict) -> dict | None:
    qs = _quarters(event["symbol"])
    if not qs:
        return None
    # Show the quarter whose filing is closest to this event, not simply the newest one.
    ts = datetime.fromisoformat(event["ts"])
    eligible = [q for q in qs if q.period_end <= ts.date().isoformat()]
    if not eligible:
        return None
    return research.results_card(event["symbol"], eligible)


def _promise_check(event: dict) -> dict | None:
    """The link between push and pull: judge a new result against what management said it would be."""
    dossier = research.load(event["symbol"])
    card = event.get("results")
    if not dossier or not dossier.get("promises") or not card:
        return None
    end = card["period_end"]
    checked, upcoming = [], []
    for p in dossier["promises"]["promises"]:
        due = promises.parse_period(p.get("period"))
        if not due:
            continue
        if due["end"] == end and p["verdict"] in (promises.DELIVERED, promises.PARTLY, promises.MISSED):
            checked.append(p)
        elif due["end"] > end and p["verdict"] == promises.NOT_DUE and p["kind"] == "financial":
            upcoming.append(p)
    upcoming.sort(key=lambda p: promises.parse_period(p["period"])["end"])
    return {"checked": checked[:4], "upcoming": upcoming[:2]} if checked or upcoming else None


@app.get("/api/portfolio")
def portfolio(symbols: str | None = None):
    conn = db()
    out = []
    mine = _holdings(symbols)
    for c in conn.execute(f"SELECT * FROM companies WHERE symbol IN ({_marks(mine)}) ORDER BY symbol", mine):
        rows = [dict(r) for r in conn.execute("SELECT date, close, volume FROM prices WHERE symbol=? ORDER BY date", (c["symbol"],))]
        sig = prices.signals(rows)
        last = sig[-1] if sig else None
        # So a price move can be shown next to what was filed around it, whatever the brief's window.
        recent = conn.execute(
            "SELECT substr(ts,1,10) d, title, category FROM filings WHERE symbol=? AND score>=? AND ts>=? ORDER BY score DESC",
            (c["symbol"], C.NOTABLE_AT, (datetime.now() - timedelta(days=200)).isoformat()),
        ).fetchall()
        out.append({
            "symbol": c["symbol"], "name": c["name"], "banking": bool(c["banking"]),
            "filings": [{"d": r["d"], "category": r["category"], "title": "Results filed" if r["category"] == "results" else r["title"]} for r in recent],
            "last": last, "series": [{"d": s["date"], "c": s["close"], "m": s["move_pct"], "x": s["vol_x"], "ok": s["enough_history"]} for s in sig[-130:]],
        })
    return {"companies": out, "as_of": max((c["last"]["date"] for c in out if c["last"]), default=None)}


@app.get("/api/research/{symbol}")
def get_research(symbol: str):
    symbol = symbol.upper().strip()
    if not SYMBOL.match(symbol):
        raise HTTPException(400, "That doesn't look like an NSE ticker")
    d = research.load(symbol)
    job = db().execute("SELECT * FROM jobs WHERE symbol=?", (symbol,)).fetchone()
    with _jobs_lock:
        building = symbol in _running
    # The numbers are up while the track record is read, so nobody waits on the free tier's pace.
    if d and building and job and job["status"] == "running":
        return {"status": "ready", "dossier": d, "working": job["step"]}
    if d and not _needs_rebuild(d):
        return {"status": "ready", "dossier": d}
    # A finished job built the dossier that now needs rebuilding, so it says nothing about the rebuild.
    if not job or job["status"] == "ready":
        return {"status": "none"}
    with _jobs_lock:
        active = symbol in _running
    # A job whose thread died with a restart would otherwise spin forever on the client.
    if job["status"] == "running" and not active:
        return {"status": "none"}
    # A failure may just be a bad moment upstream, so it shouldn't stick for good.
    if job["status"] == "failed" and (datetime.now() - datetime.fromisoformat(job["updated"])).total_seconds() > 600:
        return {"status": "none"}
    # A partial record beats an error while the retry is failing.
    if job["status"] == "failed" and d:
        return {"status": "ready", "dossier": d}
    return {"status": job["status"], "step": job["step"], "error": job["error"]}


def _built_without_model(dossier: dict) -> bool:
    """A dossier built with no model configured gets upgraded once a model is available."""
    pr = dossier.get("promises")
    return bool(pr) and pr.get("model") is False and not dossier.get("numbers_first")


def _needs_rebuild(dossier: dict) -> bool:
    if llm.available() and _built_without_model(dossier):
        return True
    # Its track record was still being read when the server stopped, or today's allowance ran out.
    if dossier.get("numbers_first") and llm.available():
        return True
    # Something upstream failed during the build, which a later attempt may get past.
    age = (datetime.now() - datetime.fromisoformat(dossier["generated"])).total_seconds()
    return bool(dossier.get("incomplete")) and age > RETRY_INCOMPLETE_AFTER


def _set_job(symbol: str, status: str, step: str = "", error: str | None = None) -> None:
    conn = db()
    conn.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?)", (symbol, status, step, error, datetime.now().isoformat(timespec="seconds")))
    conn.commit()


def _run_research(symbol: str) -> None:
    try:
        _set_job(symbol, "running", "Waiting for another company to finish")
        with _build_slots:
            log = lambda step: _set_job(symbol, "running", step)
            # On the free tier a track record takes minutes, so the numbers go up first.
            if llm.frugal() and not research.load(symbol):
                with llm.no_model():
                    quick = research.build(symbol, log=log, quick=True)
                research.save(symbol, {**quick, "numbers_first": True})
                log("Reading the earnings calls")
            d = research.build(symbol, log=log)
        # If today's allowance ran out partway, it stays marked to finish later.
        if d.get("promises") and d["promises"].get("model") is False:
            d["numbers_first"] = True
        research.save(symbol, d)
        _set_job(symbol, "ready", "Done")
    except research.UnknownTicker:
        _set_job(symbol, "failed", "", f"Couldn't find {symbol} on NSE. Check the ticker.")
    except FetchError:
        _set_job(symbol, "failed", "", "FINN couldn't reach its data sources just now, so it can't say anything about this company yet. Try again in a few minutes.")
    except Exception as e:  # surface failures to the app rather than leaving it spinning
        _set_job(symbol, "failed", "", f"Research failed: {type(e).__name__}")
    finally:
        with _jobs_lock:
            _running.discard(symbol)


@app.post("/api/research/{symbol}")
def start_research(symbol: str, refresh: bool = False):
    symbol = symbol.upper().strip()
    if not SYMBOL.match(symbol):
        raise HTTPException(400, "That doesn't look like an NSE ticker")
    d = research.load(symbol)
    if not refresh and d and not _needs_rebuild(d):
        return {"status": "ready"}
    with _jobs_lock:
        if symbol not in _running:
            _running.add(symbol)
            _set_job(symbol, "running", "Starting")
            threading.Thread(target=_run_research, args=(symbol,), daemon=True).start()
    return {"status": "running"}


ASK_SYSTEM = (
    "You answer an investor's question using only numbered sentences from a company's earnings calls. Never use outside knowledge. "
    "If the sentences don't answer it, say that plainly and return no ids."
)


@app.get("/api/research/{symbol}/ask")
def ask(symbol: str, q: str = Query(..., min_length=3, max_length=300)):
    """Scoped questions: answers come only from the transcripts, and every quote is verbatim."""
    symbol = symbol.upper().strip()
    if not SYMBOL.match(symbol):
        raise HTTPException(400, "That doesn't look like an NSE ticker")
    dossier = research.load(symbol)
    if not dossier:
        raise HTTPException(404, "Research this company first")
    calls, unread = _calls_for(symbol, dossier)
    if unread and not calls:
        return {"answer": None, "error": "FINN couldn't open this company's transcripts just now, so it can't answer. Try again in a few minutes.", "quotes": []}
    # The company's own name is on every page, so it says nothing about which sentence answers the question.
    own_name = {w for w in re.findall(r"[a-z0-9\-]{3,}", f"{dossier.get('name', '')} {symbol}".lower())}
    terms = {w for w in re.findall(r"[a-z0-9\-]{3,}", q.lower())} - {"what", "did", "they", "say", "said", "about", "the", "how", "and", "for", "are", "was", "were", "has", "have", "will", "does", "management"} - own_name
    scored = []
    for c in calls:
        for s in c.mgmt():
            # A filing's cover letter lands in the transcript's first page, and it isn't anyone speaking.
            if re.search(r"\b(enclosed|scrip code|listing department|transcript of the)\b", s.text, re.I):
                continue
            hit = sum(t in s.text.lower() for t in terms)
            if hit:
                scored.append((hit, -c.index, s, c))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    pack = scored[:24]
    if not pack:
        if not calls:
            return {"answer": "FINN has no readable earnings calls for this company, so it can't answer from them.", "quotes": []}
        # This is a word match, so it can't claim the topic never came up, only that these words didn't.
        read = f"{len(calls)} earnings call{'s' if len(calls) != 1 else ''} FINN read"
        gap = f", and {unread} more couldn't be opened" if unread else ""
        return {"answer": f"Nothing management said on the {read} uses those words{gap}. Try different words.", "quotes": []}
    lines = "\n".join(f"[{s.id}] ({c.label}) {T.mask(s.text, T.mask_terms(dossier['name']))}" for _, _, s, c in pack)
    prompt = (
        f"Question: {q}\n\n"
        'Return JSON: {"answer": "under 40 plain words, or say the calls do not answer this", "ids": ["up to 3 sentence ids that support the answer"]}'
        f"\n\n<source>\n{lines}\n</source>"
    )
    closest = [_quote(s, c) for _, _, s, c in pack[:3]]
    try:
        r = llm.ask_json("ask_v1", llm.CHEAP, ASK_SYSTEM, prompt, max_tokens=400)
    except llm.NoModel:
        # Without a model, the matching sentences are still a useful, honest answer.
        return {"answer": "No model on this server, so here are the closest passages.", "quotes": closest}
    except (llm.BudgetExceeded, ValueError):
        return {"answer": "The model isn't available just now, so here are the closest passages.", "quotes": closest}
    by_id = {s.id: (s, c) for _, _, s, c in pack}
    ids = [i for i in (r.get("ids") or []) if i in by_id] if isinstance(r, dict) else []
    quotes = [_quote(*by_id[i]) for i in ids]
    answer = r.get("answer") if isinstance(r, dict) else None
    # An answer rests on what it quotes, so a figure that isn't in those sentences isn't shown.
    if answer and not numbers_in_source(answer, " ".join(q["text"] for q in quotes)):
        return {"answer": "I couldn't back that up from the calls, so here are the closest passages.", "quotes": closest}
    return {"answer": answer, "quotes": quotes}


def _quote(s, c) -> dict:
    return {"text": s.text, "speaker": s.speaker, "page": s.page, "url": c.url, "call_label": c.label}


def _calls_for(symbol: str, dossier: dict) -> tuple[list[T.Call], int]:
    """The calls that could be read, and how many couldn't: "not mentioned" and "couldn't read" differ."""
    calls, unread = [], 0
    for i, c in enumerate((dossier.get("promises") or {}).get("calls", [])):
        try:
            calls.append(_parsed_call(c["url"], i, c["held"]))
        except Exception:
            unread += 1
    return calls, unread


@lru_cache(maxsize=20)
def _parsed_call(url: str, index: int, held: str) -> T.Call:
    """Parsing takes seconds on a small server, and people ask follow-up questions."""
    return T.parse(docs.load(url, headers=T.headers_for(url)), index, held, url)


@app.get("/api/quote/{symbol}")
def quote(symbol: str):
    """The latest price Yahoo has, for a company on any screen. Falls back to the last stored close."""
    symbol = symbol.upper().strip()
    if not SYMBOL.match(symbol):
        raise HTTPException(400, "That doesn't look like an NSE ticker")
    live = prices.quote(symbol)
    if live:
        return {"live": True, **live}
    row = db().execute("SELECT date, close FROM prices WHERE symbol=? ORDER BY date DESC LIMIT 1", (symbol,)).fetchone()
    if row:
        return {"live": False, "symbol": symbol, "price": row["close"], "as_of": f"close of {row['date']}", "change_pct": None, "today": []}
    raise HTTPException(404, "No price for that ticker")


@app.get("/api/quotes")
def quotes(symbols: str | None = None):
    """Every holding, for the Watch list. Quotes are cached for a minute, so this stays polite."""
    out = []
    mine = _holdings(symbols)
    known = {r["symbol"]: r["name"] for r in db().execute(f"SELECT symbol, name FROM companies WHERE symbol IN ({_marks(mine)})", mine)}
    for c in [{"symbol": s, "name": known.get(s, s)} for s in sorted(mine)]:
        q = prices.quote(c["symbol"])
        out.append({"symbol": c["symbol"], "name": c["name"], "price": q["price"] if q else None, "change_pct": q["change_pct"] if q else None, "as_of": q["as_of"] if q else None, "today": q["today"] if q else []})
    return {"quotes": out}


@app.get("/api/history/{symbol}")
def history(symbol: str, range: str = Query("1y", pattern="^(1mo|6mo|1y|5y)$")):
    symbol = symbol.upper().strip()
    if not SYMBOL.match(symbol):
        raise HTTPException(400, "That doesn't look like an NSE ticker")
    try:
        rows = prices.history(symbol, range)
    except Exception:
        rows = [dict(r) for r in db().execute("SELECT date, close, volume FROM prices WHERE symbol=? ORDER BY date", (symbol,))]
    # Five years of daily closes is 1,200 points, and a phone's chart is 300 pixels wide.
    step = max(1, len(rows) // 300)
    return {"points": [{"d": r["date"], "c": r["close"]} for r in rows[::step]]}


@app.get("/api/compare")
def compare(symbols: str = Query(..., min_length=1, max_length=80)):
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()][:3]
    if not all(SYMBOL.match(s) for s in wanted):
        raise HTTPException(400, "Those don't look like NSE tickers")
    return research.compare(wanted)


@app.get("/api/live")
def live(move: float = Query(5, gt=0, le=50), vol: float = Query(2, gt=0, le=50), symbols: str | None = None):
    """Holdings past your limits right now, on the latest quote against the last 20 sessions' volume. No model involved."""
    conn = db()
    out = []
    mine = _holdings(symbols)
    with ThreadPoolExecutor(max_workers=6) as pool:
        quotes = list(pool.map(prices.quote, mine))
    for sym, q in zip(mine, quotes):
        if not q or q.get("change_pct") is None:
            continue
        recent = [r["volume"] for r in conn.execute("SELECT volume FROM prices WHERE symbol=? ORDER BY date DESC LIMIT 20", (sym,))]
        vol_x = round(q["volume"] / (sum(recent) / len(recent)), 2) if q.get("volume") and len(recent) >= 20 and sum(recent) else None
        if abs(q["change_pct"]) >= move or (vol_x or 0) >= vol:
            out.append({"symbol": sym, "price": q["price"], "move_pct": q["change_pct"], "vol_x": vol_x, "as_of": q["as_of"]})
    out.sort(key=lambda m: -abs(m["move_pct"]))
    return {"moving": out}


@app.get("/api/calendar")
def calendar(symbols: str | None = None, days: int = Query(30, ge=1, le=120)):
    """What's coming up across your holdings, from dates companies have announced to the exchange."""
    conn = db()
    mine = _holdings(symbols)
    today = datetime.now().date()
    last = (today + timedelta(days=days)).isoformat()
    out = []
    for r in conn.execute(f"SELECT * FROM events WHERE symbol IN ({_marks(mine)}) AND date>=? AND date<=? ORDER BY date, symbol", (*mine, today.isoformat(), last)):
        event = dict(r)
        if r["kind"] == "results":
            event["due"] = _promises_due(r["symbol"], r["date"])
        out.append(event)
    return {"events": out}


def _promises_due(symbol: str, meeting: str) -> list[str]:
    """What this company promised for the quarter these results will cover, so you know what to look for."""
    dossier = research.load(symbol)
    claims = []
    for p in ((dossier or {}).get("promises") or {}).get("promises", []):
        due = promises.parse_period(p.get("period"))
        if due and p["verdict"] == promises.NOT_DUE and due["end"] < meeting and (datetime.fromisoformat(meeting) - datetime.fromisoformat(due["end"])).days <= 100:
            claims.append(p["claim"])
    return claims[:3]


@app.post("/api/track/{symbol}")
def track(symbol: str):
    """Follow a company someone added to their holdings. Its filings and prices arrive in the background."""
    symbol = symbol.upper().strip()
    if not SYMBOL.match(symbol):
        raise HTTPException(400, "That doesn't look like an NSE ticker")
    found = prices.listed(symbol)
    if found is False:
        raise HTTPException(404, f"Couldn't find {symbol} on NSE. Check the ticker.")
    if found is None:
        raise HTTPException(503, "Couldn't check that ticker just now. Try again in a minute.")

    def follow():
        try:
            pipeline.track(symbol)
        except Exception:
            pass  # the next refresh tries again, because it's on the tracked list

    threading.Thread(target=follow, daemon=True).start()
    return {"status": "tracking", "symbol": symbol}


@app.get("/api/alerts")
def alerts(move: float = Query(5, gt=0, le=50), vol: float = Query(2, gt=0, le=50), muted: str = "", days: int = Query(14, ge=1, le=60), symbols: str | None = None):
    """The messages FINN would send you: filings that rank top, and sessions that crossed your limits, newest first."""
    conn = db()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    off = {m for m in muted.split(",") if m}
    groups: dict[str, list[dict]] = defaultdict(list)
    mine = _holdings(symbols)
    for r in conn.execute(f"SELECT f.* FROM filings f WHERE f.symbol IN ({_marks(mine)}) AND f.ts>=?", (*mine, since)):
        groups[r["thread"] or r["id"]].append(dict(r))
    out = []
    for g in groups.values():
        if not _lead_in_window(g, since):
            continue
        e = _decorate(_event(g))
        if e["tier"] == C.TOP and e["category"] not in off:
            text = e["reason"]
            person = e["detail"].get("person") or {}
            if person.get("name"):
                # The alert should say who, not only that someone changed.
                earlier = (person.get("career") or person.get("profile") or [None])[0]
                text = f"{person['name']}, {person.get('role') or 'senior management'}" + (f". {earlier}" if earlier else "")
            out.append({"kind": "filing", "ts": e["ts"], "symbol": e["symbol"], "title": e["title"], "text": text, "url": e["pdf_url"], "label": e["category_label"], "tone": e["detail"].get("tone")})
    cutoff = since[:10]
    for sym in mine:
        c = {"symbol": sym}
        rows = [dict(r) for r in conn.execute("SELECT date, close, volume FROM prices WHERE symbol=? ORDER BY date", (sym,))]
        for s in prices.signals(rows)[-(days + 2):]:
            volume = s["vol_x"] if s["enough_history"] else None
            if s["date"] >= cutoff and (abs(s["move_pct"]) >= move or (volume or 0) >= vol):
                text = f"{s['move_pct']:+.1f}%" + (f" on {volume:.1f}x normal volume" if volume else "")
                out.append({"kind": "move", "ts": f"{s['date']}T15:30:00", "symbol": c["symbol"], "title": text, "text": "Crossed your limits at the close", "url": None, "label": "Price and volume", "move": s["move_pct"]})
    out.sort(key=lambda m: m["ts"], reverse=True)
    return {"messages": out[:40]}


@app.post("/api/chat")
def chat_message(
    message: str = Body(..., embed=True, min_length=1, max_length=400), move: float = Body(5, embed=True), vol: float = Body(2, embed=True),
    about: str | None = Body(None, embed=True, max_length=20), symbols: str | None = Body(None, embed=True, max_length=800),
    history: list[dict] = Body([], embed=True, max_length=8),
):
    def ask_about(symbol: str, question: str) -> dict:
        return ask(symbol, question if len(question) >= 3 else f"{question} ?")
    if about and not SYMBOL.match(about.upper()):
        about = None
    return chat.answer(message, ask_about, move, vol, about.upper() if about else None, _holdings(symbols), history)


@app.get("/api/push")
def push_available():
    return {"enabled": push.enabled(), "public_key": push.public_key() if push.enabled() else None}


@app.post("/api/push/subscribe")
def push_subscribe(subscription: dict = Body(..., embed=True), move: float = Body(5, embed=True), vol: float = Body(2, embed=True), muted: list[str] = Body([], embed=True)):
    if not push.enabled():
        raise HTTPException(404, "Notifications aren't set up on this server")
    if not push.subscribe(subscription, move, vol, muted):
        raise HTTPException(400, "That isn't a usable browser subscription")
    return {"subscribed": True}


@app.post("/api/push/unsubscribe")
def push_unsubscribe(endpoint: str = Body(..., embed=True, max_length=800)):
    push.unsubscribe(endpoint)
    return {"subscribed": False}


@app.get("/api/status")
def status():
    conn = db()
    calls = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(cost_usd),0) usd FROM llm_calls").fetchone()
    return {
        "last_refresh": get_meta("last_refresh"),
        "model": llm.provider(), "chat_model": llm.provider_for("ask_v1"),
        "filings": conn.execute("SELECT COUNT(*) n FROM filings").fetchone()["n"],
        "dossiers": [r["symbol"] for r in conn.execute("SELECT symbol FROM dossiers ORDER BY symbol")],
        "llm_calls": calls["n"], "llm_usd": round(calls["usd"], 3),
        "by_task": [dict(r) for r in conn.execute("SELECT task, COUNT(*) calls, ROUND(SUM(cost_usd),3) usd FROM llm_calls GROUP BY task")],
        "holdings": [r["symbol"] for r in conn.execute("SELECT symbol FROM companies WHERE in_portfolio=1 ORDER BY symbol")],
    }


def _refresh_loop() -> None:
    """Optional: on a server, keep filings fresh. Failures keep the last good snapshot."""
    minutes = int(os.environ.get("FINN_REFRESH_MINUTES", "0"))
    if not minutes:
        return
    while True:
        # A free host sleeps when idle and wakes with the shipped snapshot, so the first check can't wait.
        last = get_meta("last_refresh")
        age = datetime.now() - datetime.fromisoformat(last) if last else timedelta(days=45)
        if age >= timedelta(minutes=minutes):
            try:
                pipeline.refresh(days=min(age.days + 2, 45), log=lambda *_: None)
                push.send_new(lambda move, vol, muted: alerts(move, vol, ",".join(muted), 3)["messages"])
            except Exception:
                pass
        time.sleep(minutes * 60)


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=_refresh_loop, daemon=True).start()


if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "No such endpoint")
        target = (WEB / path).resolve()
        if path and target.is_file() and target.is_relative_to(WEB):
            return FileResponse(target)
        return FileResponse(WEB / "index.html")
