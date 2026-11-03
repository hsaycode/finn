"""A short brief on someone joining or leaving, from what the company itself has published.

The filing announcing a change usually carries a profile. The annual report's AGM notice often has
a fuller one: age, qualifications, other boards. Code then adds what no filing says outright: how
often this company's top team has changed, whether this person speaks on its calls, and how the
stock took the news.

Every fact must be traceable. A line whose figures or names aren't in the source text is dropped.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from . import docs, llm, prices, screener
from . import transcripts as T
from .enrich import grounded, numbers_in_source
from .net import FetchError
from .nse import HEADERS
from .store import db

PERSON_SYSTEM = (
    "You pull a person's professional background out of company filings for an investor. Use only the text given. "
    "Never use outside knowledge, even if you recognise the person. Leave out anything the text doesn't state."
)
PERSON_FORMAT = """Return JSON about {name}:
{{"age": a number or null,
 "qualifications": ["degrees and professional qualifications, each as written"],
 "career": ["up to 5 earlier roles, each as 'role, organisation, years if given', most recent first"],
 "other_boards": ["other companies whose board they sit on"],
 "time_at_company": "how long they've been with this company, under 10 words, or null",
 "replaces": "the person they succeed, or null",
 "term": "the length or dates of the appointment, under 10 words, or null",
 "page": the page number holding most of this, or null}}"""
_PROPER = re.compile(r"\b[A-Z][a-zA-Z&.]{2,}\b")
_COMMON = {"The", "And", "For", "With", "Former", "Director", "Executive", "Chief", "Officer", "Managing", "Senior", "Vice", "President", "Head", "Group", "Limited", "Ltd", "Years", "Over", "Since", "From", "Until", "Member", "Independent", "Non", "Chairman", "Chairperson", "General", "Manager", "Board", "Company", "Master", "Bachelor", "Degree", "Business"}


def grounded(text: str, source: str) -> bool:
    """Figures and proper names in a model's line must appear in what it was given."""
    if not numbers_in_source(text, source):
        return False
    low = source.lower()
    return all(w.strip(".").lower() in low for w in _PROPER.findall(text) if w.strip(".") not in _COMMON)


def _pages_about(doc: docs.Doc, name: str, limit: int = 4) -> list[tuple[int, str]]:
    surname = name.strip().split()[-1].lower()
    hits = [(i + 1, p) for i, p in enumerate(doc.pages) if surname in p.lower()]
    return sorted(hits, key=lambda x: -x[1].lower().count(surname))[:limit]


def _latest_report_pages(symbol: str, name: str) -> tuple[list[tuple[int, str]], dict | None]:
    """AGM notices sit at the front of annual reports, and they profile directors up for appointment."""
    try:
        reports = screener.company(symbol).annual_reports
        if not reports:
            return [], None
        doc = docs.load(reports[0]["url"], headers=T.headers_for(reports[0]["url"]), max_pages=120)
    except (FetchError, RuntimeError, ValueError):
        return [], None
    return _pages_about(doc, name), reports[0]


def background(row: dict, person: dict) -> dict:
    """What the filing and the annual report say about this person. Empty if they say nothing."""
    name = person.get("name") or ""
    if len(name.split()) < 2:
        return {}
    sources: list[tuple[str, str, int, str]] = []  # label, url, page, text
    if row.get("pdf_url"):
        try:
            filing = docs.load(row["pdf_url"], headers=HEADERS, max_pages=25)
            sources += [("Filing", row["pdf_url"], p, t) for p, t in _pages_about(filing, name)]
        except (FetchError, RuntimeError, ValueError):
            pass
    pages, report = _latest_report_pages(row["symbol"], name)
    if report:
        sources += [(f"Annual Report {report['year']}", report["url"], p, t) for p, t in pages]
    if not sources:
        return {}
    text = "\n\n".join(f"[{label} p.{p}] {t[:3500]}" for label, _, p, t in sources)[:llm.source_chars(14000)]
    try:
        r = llm.ask_json("person_v1", llm.CHEAP, PERSON_SYSTEM, PERSON_FORMAT.format(name=name) + f"\n\n<source>\n{text}\n</source>", max_tokens=600)
    except (llm.NoModel, llm.BudgetExceeded, ValueError):
        return {}
    if not isinstance(r, dict):
        return {}
    out: dict = {}
    if isinstance(r.get("age"), (int, float)) and 18 < r["age"] < 100 and numbers_in_source(str(int(r["age"])), text):
        out["age"] = int(r["age"])
    for key in ("qualifications", "career", "other_boards"):
        # The format asks for years "if given", and models sometimes report their absence.
        lines = [re.sub(r",?\s*(years not given|organisation as stated|organization as stated)", "", x).strip() for x in r.get(key) or [] if isinstance(x, str)]
        kept = [x for x in lines if x and grounded(x, text)]
        if kept:
            out[key] = kept[:5]
    for key in ("time_at_company", "replaces", "term"):
        if isinstance(r.get(key), str) and r[key].strip() and grounded(r[key], text):
            out[key] = r[key].strip()
    if out:
        page = r.get("page") if isinstance(r.get("page"), int) else sources[0][2]
        where = next((s for s in sources if s[2] == page), sources[0])
        out["source"] = {"label": where[0], "url": where[1], "page": where[2]}
    return out


def context(symbol: str, name: str, ts: str) -> dict:
    """What code can say from FINN's own records, with no model involved."""
    conn = db()
    start = (datetime.fromisoformat(ts) - timedelta(days=365)).isoformat()
    seen = set()
    for r in conn.execute("SELECT detail FROM filings WHERE symbol=? AND category='management_change' AND ts>=? AND ts<=?", (symbol, start, ts)):
        p = json.loads(r["detail"] or "{}").get("person") or {}
        if p.get("top_executive") and p.get("name"):
            seen.add((p["name"].lower().strip(), p.get("action")))
    out: dict = {}
    if len(seen) >= 2:
        out["top_changes_12m"] = len(seen)
    row = conn.execute("SELECT data FROM dossiers WHERE symbol=?", (symbol,)).fetchone()
    calls = (json.loads(row["data"]).get("promises") or {}).get("calls", []) if row else []
    surname = name.strip().split()[-1].lower() if name.strip() else ""
    spoke = 0
    for i, c in enumerate(calls):
        try:
            call = T.parse(docs.load(c["url"], headers=T.headers_for(c["url"])), i, c["held"], c["url"])
        except Exception:
            continue
        spoke += any(surname and surname in (s.speaker or "").lower() for s in call.mgmt())
    if calls and surname:
        out["on_calls"] = {"spoke": spoke, "of": len(calls)}
    rows = [dict(r) for r in conn.execute("SELECT date, close, volume FROM prices WHERE symbol=? ORDER BY date", (symbol,))]
    day = (datetime.fromisoformat(ts) + timedelta(days=1 if ts[11:16] >= "15:30" else 0)).date().isoformat()
    for s in prices.signals(rows):
        if s["date"] >= day:
            out["reaction"] = {"date": s["date"], "move_pct": s["move_pct"], "vol_x": s["vol_x"] if s["enough_history"] else None}
            break
    return out


def enrich_all(days: int = 200, log=print) -> int:
    """Add a brief to every recent change with a named person. Model answers are cached, so re-running is free."""
    conn = db()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    rows = [dict(r) for r in conn.execute("SELECT * FROM filings WHERE category='management_change' AND ts>=? ORDER BY ts DESC", (since,))]
    done = 0
    for row in rows:
        detail = json.loads(row["detail"] or "{}")
        person = detail.get("person")
        if not person or not person.get("name"):
            continue
        extra = background(row, person)
        person.update(extra)
        person["context"] = context(row["symbol"], person["name"], row["ts"])
        conn.execute("UPDATE filings SET detail=? WHERE id=?", (json.dumps(detail, ensure_ascii=False), row["id"]))
        conn.commit()
        done += 1
        log(f"{row['symbol']}: {person['name']} {sorted(extra)}")
    return done
