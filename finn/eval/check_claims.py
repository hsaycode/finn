"""Is anything on screen unsupported? Checks every line a model wrote against the text it was given.

Numbers from code and quotes from documents can't be invented, and eval/check_receipts.py covers
quotes. This covers the rest: summaries, notes, arguments and briefs. A line fails if a figure in it
(or, for people, a name) isn't in the source shown beside it. A pass means nothing was invented in
those terms. It can't prove a summary's meaning is fair, which is why the sources stay one tap away.

    python eval/check_claims.py
"""
import json
from collections import Counter

from finn import docs, promises as P, research
from finn import transcripts as T
from finn.enrich import grounded, numbers_in_source
from finn.nse import HEADERS
from finn.store import db

tally, problems = Counter(), []


def check(surface: str, symbol: str, text: str | None, ok: bool) -> None:
    if not text:
        return
    tally[surface, "checked"] += 1
    if not ok:
        tally[surface, "failed"] += 1
        problems.append((surface, symbol, text[:120]))


def page_text(url: str, page: int | None) -> str:
    try:
        d = docs.load(url, headers=T.headers_for(url))
    except Exception:
        return ""
    return d.pages[page - 1] if page and 1 <= page <= len(d.pages) else d.text


conn = db()
for row in conn.execute("SELECT symbol FROM dossiers ORDER BY symbol").fetchall():
    sym = row["symbol"]
    d = research.load(sym)
    for p in (d.get("promises") or {}).get("promises", []):
        check("Promise summary", sym, p["claim"], P._note_is_sourced(p["claim"], p["quote"], page_text(p["url"], p.get("page"))))
        if p.get("checked_by") != "code" and p["verdict"] in ("delivered", "partly", "missed"):
            ev = p.get("evidence") or {}
            check("Verdict note", sym, p.get("note"), P._note_is_sourced(p.get("note") or "", ev.get("quote", ""), p["claim"], p["quote"]))
    a = d.get("arguments") or {}
    for side in ("bull", "bear"):
        for x in a.get(side) or []:
            check("Bull or bear point", sym, x["point"], P._note_is_sourced(x["point"], *[s["text"] for s in x["sources"]]))
    b = d.get("business") or {}
    if b.get("description") and b.get("source"):
        check("Business description", sym, b["description"], numbers_in_source(b["description"], page_text(b["source"]["url"], None)))
    for k in d.get("kpis") or []:
        check("Company KPI", sym, f"{k['name']}: {k['value']}", numbers_in_source(k["value"], k["quote"]))
    for r in (d.get("ar_flags") or {}).get("reports", []):
        check("Annual report note", sym, r.get("rpt_note"), numbers_in_source(r.get("rpt_note") or "", page_text(r["url"], r.get("rpt_page"))))
        if r.get("auditor", {}).get("emphasis"):
            check("Annual report note", sym, r["auditor"]["emphasis"], numbers_in_source(r["auditor"]["emphasis"], page_text(r["url"], r["auditor"].get("page"))))

for r in conn.execute("SELECT id, symbol, title, detail, pdf_url FROM filings WHERE enriched=2").fetchall():
    detail = json.loads(r["detail"] or "{}")
    if not r["pdf_url"]:
        continue
    try:
        source = docs.load(r["pdf_url"], headers=HEADERS).text
    except Exception:
        tally["Filing", "source unreadable"] += 1
        continue
    check("Filing headline", r["symbol"], r["title"], numbers_in_source(r["title"], source))
    for key in ("response", "amount_note"):
        check("Filing detail", r["symbol"], detail.get(key), numbers_in_source(detail.get(key) or "", source))
    if detail.get("legal"):
        check("Filing detail", r["symbol"], detail["legal"].get("company_view"), numbers_in_source(detail["legal"].get("company_view") or "", source))
    person = detail.get("person") or {}
    for x in person.get("profile") or []:
        check("Person's background", r["symbol"], x, grounded(x, source))

print(f"{'what':28s} {'checked':>8s} {'failed':>7s}")
for surface in sorted({k[0] for k in tally}):
    print(f"{surface:28s} {tally[surface, 'checked']:8d} {tally[surface, 'failed']:7d}")
print("unreadable filings:", tally["Filing", "source unreadable"])
for p in problems[:60]:
    print("  ", *p)
