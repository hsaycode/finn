"""Is every quote on screen really in the document, on the page FINN says?

Opens the source PDF behind every quote in every shipped dossier and looks for the quote on its
cited page.

    python eval/check_receipts.py
"""
import re
import unicodedata
from collections import Counter

from finn import docs, research
from finn import transcripts as T
from finn.store import db


def norm(text: str) -> str:
    # NFKC because PDFs store "fi" as a single ligature, which transcript parsing has already expanded.
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", text).lower())


def quotes_in(d: dict):
    pr = d.get("promises") or {}
    for p in pr.get("promises", []):
        yield "promise", p.get("quote"), p.get("pages") or [p.get("page")], p.get("url")
        ev = p.get("evidence") or {}
        yield "evidence", ev.get("quote"), ev.get("pages") or [ev.get("page")], ev.get("url")
    for m in pr.get("moves", []):
        yield "goalpost", m.get("from_quote"), m.get("from_pages") or [m.get("from_page")], m.get("from_url")
        yield "goalpost", m.get("to_quote"), m.get("to_pages") or [m.get("to_page")], m.get("to_url")
    for k in d.get("kpis") or []:
        yield "company figure", k["quote"], [k["page"]], k["url"]
    for items in ((d.get("forces") or {}).get("forces") or {}).values():
        for x in items:
            yield "force", x["quote"], [x["page"]], x["url"]
    for x in d.get("dodged") or []:
        yield "dodged", x.get("question"), [x.get("page")], x.get("url")
        yield "dodged", x.get("answer"), [x.get("page")], x.get("url")


pages_of: dict[str, list[str] | None] = {}


def pages(url: str) -> list[str] | None:
    if url not in pages_of:
        try:
            pages_of[url] = [norm(p) for p in docs.load(url, headers=T.headers_for(url)).pages]
        except Exception:
            pages_of[url] = None
    return pages_of[url]


tally, problems = Counter(), []
symbols = [r["symbol"] for r in db().execute("SELECT symbol FROM dossiers ORDER BY symbol")]
for sym in symbols:
    for kind, quote, pages_cited, url in quotes_in(research.load(sym)):
        if not quote or not url:
            continue
        tally["quotes"] += 1
        ps = pages(url)
        if ps is None:
            tally["source unreadable"] += 1
            problems.append((sym, kind, "source unreadable", pages_cited, quote[:90]))
            continue
        # Marked gaps split a quote into fragments, and each one has to be there on its own.
        fragments = [norm(f) for f in quote.split("[…]")]
        cited = [n - 1 for n in pages_cited if 1 <= n <= len(ps)]
        joined = "".join(ps[i] + (ps[i + 1] if i + 1 < len(ps) else "") for i in cited)
        if all(any(f in ps[i] for i in cited) for f in fragments):
            tally["verbatim on the cited page"] += 1
        # A sentence can run across a page break, so look at the join before calling it wrong.
        elif all(f in joined for f in fragments):
            tally["verbatim, runs onto the next page"] += 1
        elif all(any(f in x for x in ps) for f in fragments):
            tally["verbatim, wrong page"] += 1
            problems.append((sym, kind, "wrong page", pages_cited, quote[:90]))
        elif all(f in "".join(ps) for f in fragments):
            tally["verbatim, spans pages"] += 1
        else:
            tally["NOT FOUND"] += 1
            problems.append((sym, kind, "NOT FOUND", pages_cited, quote[:90]))

print(f"{len(symbols)} dossiers, {len(pages_of)} source documents")
for k, v in tally.most_common():
    print(f"  {k:36s} {v}")
for p in problems:
    print("  ", *p)
