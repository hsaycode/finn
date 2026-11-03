"""The on-demand dossier for any NSE ticker.

Every number is computed by code from filed data. Models only read primary documents, identify
sentences by id, and write the final arguments from a fixed set of cited facts. If a source is
missing, its section says so instead of being filled in.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from . import docs, llm, nse, pipeline, promises as P, screener
from . import transcripts as T
from .enrich import numbers_in_source as E_numbers_in_source
from .net import FetchError
from .store import db

MAX_CALLS = 5


class UnknownTicker(ValueError):
    pass


def transcript_sources(company: screener.Company) -> list[dict]:
    """Transcript links from screener and from the exchange's own filings, newest first.

    Screener's link can be dead or lead to an HTML page. Companies also file transcripts with NSE,
    so each quarter gets a second chance from an even more primary source.
    """
    sources = []
    for t in company.transcripts[:MAX_CALLS + 6]:
        try:
            sources.append({"date": datetime.strptime(t["period"], "%b %Y"), "held": t["period"], "url": t["url"]})
        except ValueError:
            continue
    try:
        now = datetime.now()
        for f in nse.announcements(company.symbol, now - timedelta(days=640), now, ttl=21600):
            if f.pdf_url and re.search(r"trans?a?cript", f"{f.text} {f.pdf_url}", re.I):
                filed = datetime.fromisoformat(f.ts)
                sources.append({"date": filed.replace(day=15, hour=0, minute=0, second=0), "held": filed.strftime("%b %Y"), "url": f.pdf_url})
    except (FetchError, ValueError, KeyError):
        pass
    return sorted(sources, key=lambda x: x["date"], reverse=True)


def load_calls(company: screener.Company, log=lambda *_: None) -> list[T.Call]:
    """The latest earnings calls, one per quarter. Investor days and duplicates are filtered out."""
    calls: list[T.Call] = []
    for src in transcript_sources(company):
        if len(calls) >= MAX_CALLS:
            break
        try:
            log(f"Fetching {src['held']} transcript")
            doc = docs.load(src["url"], headers=T.headers_for(src["url"]))
        except (FetchError, RuntimeError, ValueError):
            continue
        call = T.parse(doc, len(calls), src["held"], src["url"])
        # Many companies file a one-page letter that links to the transcript on their website.
        if len(call.sentences) < 40 and not doc.scanned:
            linked = _follow_pointer(doc)
            if linked:
                doc, src = linked, {**src, "url": linked.url}
                call = T.parse(doc, len(calls), src["held"], src["url"])
        if call.scanned:
            continue
        if not call.period_end or len(call.sentences) < 40 or not call.is_earnings_call:
            continue
        same = next((i for i, c in enumerate(calls) if c.period_end == call.period_end), None)
        if same is not None:
            # Two calls on one quarter: keep the fuller one.
            if len(call.sentences) > len(calls[same].sentences):
                calls[same] = T.parse(doc, same, src["held"], src["url"])
            continue
        calls.append(call)
    return calls


_TRANSCRIPT_LINK = re.compile(r"trans?cript|con-?call|earnings|analyst", re.I)


def _follow_pointer(letter: docs.Doc) -> docs.Doc | None:
    """Only short documents, and only links that look like a transcript. See net.fetch_linked_pdf for the safeguards."""
    if len(letter.pages) > 4:
        return None
    found = list(letter.links) + re.findall(r"https://[^\s\"'<>)]+", letter.text)
    for url in list(dict.fromkeys(u for u in found if _TRANSCRIPT_LINK.search(u) or u.lower().endswith(".pdf")))[:3]:
        try:
            return docs.load_linked(url)
        except (FetchError, RuntimeError, ValueError):
            continue
    return None


def annual_report_calls(c: screener.Company, log=lambda *_: None, years: int = 3) -> list[T.Call]:
    """Every listed company files an annual report, and the brief counts it as a primary source."""
    calls = []
    for ar in c.annual_reports[:years]:
        try:
            log(f"Reading Annual Report {ar['year']}")
            call = T.from_annual_report(docs.load(ar["url"], headers=T.headers_for(ar["url"])), len(calls), ar["year"], ar["url"])
        except (FetchError, RuntimeError, ValueError):
            continue
        if len(call.sentences) >= 40:
            calls.append(call)
    return calls


KPI_SYSTEM = (
    "You pick out the operating numbers a company uses to describe its own business, from what its management said on an earnings call. "
    "Examples are ARPU, subscribers, AUM, CASA, order book, capacity, volumes, attrition, store count. Skip revenue, EBITDA, PBT, PAT and margin, which are shown elsewhere. "
    "Use only the sentences given. No outside knowledge."
)


def company_kpis(call: T.Call, terms: list[str]) -> list[dict]:
    """The numbers this particular company reports. Each is shown with its verbatim sentence."""
    numbered = [s for s in call.mgmt() if re.search(r"\d", s.text) and len(s.text) < 400][:160]
    if not numbered:
        return []
    lines = "\n".join(f"[{s.id}] {T.mask(s.text, terms)}" for s in numbered)
    try:
        r = llm.ask_json(
            "kpis_v1", llm.CHEAP, KPI_SYSTEM,
            'Return a JSON array of up to 6, for the period just reported: {"name": "the metric, under 5 words", "value": "the figure with its unit, as said", "id": "the sentence id"}\n\n<source>\n' + lines[:llm.source_chars(14000)] + "\n</source>",
            max_tokens=500,
        )
    except (llm.NoModel, llm.BudgetExceeded, ValueError):
        return []
    by_id = call.by_id()
    out = []
    for x in r if isinstance(r, list) else []:
        s = by_id.get(x.get("id")) if isinstance(x, dict) else None
        if s and x.get("name") and x.get("value") and re.search(r"\d", str(x["value"])) and E_numbers_in_source(str(x["value"]), s.text):
            out.append({"name": x["name"], "value": str(x["value"]), "quote": s.text, "page": s.page, "url": call.url, "call_label": call.label, "speaker": s.speaker})
    return out[:6]


# Each framework's headings, and words that suggest a sentence might belong to one.
FORCES = {
    "rivalry": ("Competitive rivalry", r"\b(competit\w+|market share|price war|pricing pressure|rival\w*)\b"),
    "entrants": ("Threat of new entrants", r"\b(new entrant\w*|new players?|entry barrier\w*|barriers? to entry|moat)\b"),
    "substitutes": ("Threat of substitutes", r"\b(substitut\w+|alternative\w*|replac\w+|disrupt\w+)\b"),
    "suppliers": ("Supplier power", r"\b(supplier\w*|raw material\w*|input cost\w*|vendor\w*|commodit\w+|supply chain)\b"),
    "buyers": ("Buyer power", r"\b(customer concentration|top (?:\w+ )?customers?|largest customer\w*|anchor customer\w*|client concentration|pricing power|price hike\w*|bargain\w*)\b"),
    "political": ("Political", r"\b(government|policy|PLI|tariff\w*|geopolit\w+|election\w*|war\b|subsid\w+|budget)\b"),
    "economic": ("Economic", r"\b(inflation|interest rate\w*|repo|rupee|currency|forex|macro\w*|recession|consumption|demand environment|slowdown)\b"),
    "social": ("Social", r"\b(consumer (?:behaviou?r|preference\w*|sentiment)|demograph\w+|rural|urbani[sz]\w+|premiumi[sz]\w+|lifestyle|talent|attrition)\b"),
    "technological": ("Technological", r"\b(\bAI\b|artificial intelligence|automation|digital\w*|technolog\w+|5G|software|cloud|R&D)\b"),
    "legal": ("Legal and regulatory", r"\b(regulat\w+|SEBI|RBI|compliance|litigation|court|tribunal|labou?r code|wage code|GST|licen[cs]\w+)\b"),
    "environmental": ("Environmental", r"\b(ESG|carbon|emission\w*|renewable\w*|sustainab\w+|climate|green energy|net zero)\b"),
}
PORTER, PESTLE = ("rivalry", "entrants", "substitutes", "suppliers", "buyers"), ("political", "economic", "social", "technological", "legal", "environmental")
FORCES_SYSTEM = (
    "You sort what a company's management said into strategy headings for an investor. Use only the numbered sentences. No outside knowledge. "
    "Pick a sentence only if it plainly says how that force affects this company, not a passing mention. Leave a heading empty when nothing fits."
)


def strategic_forces(calls: list[T.Call], terms: list[str]) -> dict | None:
    """Porter's five forces and PESTLE, in management's own words only. Analysing a sector from outside knowledge would break
    the primary-sources rule, so a heading management never discussed says so instead of being filled in."""
    pool: dict[str, T.Sentence] = {}
    owner: dict[str, T.Call] = {}
    for call in calls[:3]:
        for s in call.mgmt():
            if 40 <= len(s.text) <= 420 and any(re.search(rx, s.text, re.I) for _, rx in FORCES.values()):
                pool.setdefault(s.id, s)
                owner[s.id] = call
    if len(pool) < 3:
        return None
    lines = "\n".join(f"[{s.id}] {T.mask(s.text, terms)}" for s in list(pool.values())[:170])
    keys = ", ".join(FORCES)
    try:
        r = llm.ask_json(
            "forces_v1", llm.CHEAP, FORCES_SYSTEM,
            f'Return JSON with these keys: {keys}. Each value is an array of at most 2: {{"id": "sentence id", "effect": "headwind", "tailwind" or "neutral"}}\n\n<source>\n' + lines[:llm.source_chars(15000)] + "\n</source>",
            max_tokens=900,
        )
    except (llm.NoModel, llm.BudgetExceeded, ValueError):
        return None
    if not isinstance(r, dict):
        return None
    out: dict[str, list] = {}
    for key, (label, _) in FORCES.items():
        items = []
        for x in r.get(key) or []:
            s = pool.get(x.get("id")) if isinstance(x, dict) else None
            # The words have to belong under the heading, whatever the model filed them under.
            if s and re.search(FORCES[key][1], s.text, re.I):
                call = owner[s.id]
                items.append({"quote": s.text, "speaker": s.speaker, "page": s.page, "url": call.url, "call_label": call.label, "effect": x.get("effect") if x.get("effect") in ("headwind", "tailwind", "neutral") else "neutral"})
        out[key] = items[:2]
    return {"forces": out, "labels": {k: v[0] for k, v in FORCES.items()}, "porter": PORTER, "pestle": PESTLE}


def compare(symbols: list[str]) -> dict:
    """Researched companies side by side, from numbers already computed for each. No model involved."""
    dossiers = {s: load(s) for s in symbols}
    ready = [s for s in symbols if dossiers[s]]

    def signal(d, word, field):
        for s in (d.get("trajectory") or {}).get("signals", []):
            if word in s["label"].lower():
                return s.get(field)
        return None

    def ratio(d, name):
        raw = ((d.get("snapshot") or {}).get("ratios") or {}).get(name) or ""
        m = re.search(r"-?[\d,]+\.?\d*", raw)
        return float(m[0].replace(",", "")) if m else None

    def quarter(d, key, field="yoy"):
        q = (d.get("snapshot") or {}).get("quarter") or {}
        metrics = q.get("metrics") or {}
        if key == "top":
            key = next(iter(metrics), None)
        return (metrics.get(key) or {}).get(field)

    def record(d):
        pr = d.get("promises") or {}
        return f"{pr['tally']['delivered']} of {pr['testable']}" if pr.get("testable") else None

    def last_of(vals):
        vals = [v for v in vals or [] if v is not None]
        return vals[-1] if vals else None

    def last(d, section, key):
        return last_of((d.get(section) or {}).get(key))

    spec = [
        ("Growth", "Top line, latest quarter YoY", lambda d: quarter(d, "top"), "%", 1), ("Growth", "PAT, latest quarter YoY", lambda d: quarter(d, "pat"), "%", 1),
        ("Growth", "Revenue, 3-yr CAGR", lambda d: signal(d, "revenue", "c3") or signal(d, "sales", "c3") or signal(d, "interest", "c3"), "%", 1), ("Growth", "Profit, 3-yr CAGR", lambda d: signal(d, "profit", "c3"), "%", 1),
        ("Profitability", "Operating margin", lambda d: (d.get("trajectory") or {}).get("margin_now"), "%", 1), ("Profitability", "ROCE", lambda d: ratio(d, "ROCE"), "%", 1), ("Profitability", "ROE", lambda d: ratio(d, "ROE"), "%", 1),
        ("Valuation", "P/E", lambda d: ratio(d, "Stock P/E"), "×", 0), ("Valuation", "Market cap, ₹ cr", lambda d: ratio(d, "Market Cap"), "", 0),
        ("Balance sheet", "Debt to equity", lambda d: last(d, "balance_sheet", "de"), "×", -1), ("Balance sheet", "Cash conversion cycle, days", lambda d: last_of(((d.get("balance_sheet") or {}).get("days") or {}).get("ccc")), "", -1),
        ("Cash", "Operating cash to profit, 5 yrs", lambda d: (d.get("cash") or {}).get("cfo_to_pat_5y"), "×", 1),
        ("Credibility", "Promises kept", record, "", 0),
    ]
    rows = []
    for group, name, fn, unit, better in spec:
        values = [fn(dossiers[s]) for s in ready]
        if not any(v is not None for v in values):
            continue
        numeric = [v for v in values if isinstance(v, (int, float))]
        # Marking the better figure only where more or less is plainly better, and never on a tie.
        best = None
        if better and len(numeric) >= 2 and len(set(numeric)) == len(numeric):
            best = values.index(max(numeric) if better > 0 else min(numeric))
        rows.append({"group": group, "name": name, "values": values, "unit": unit, "best": best})
    return {
        "columns": [{
            "symbol": s, "name": dossiers[s]["name"], "format": ((dossiers[s].get("snapshot") or {}).get("quarter") or {}).get("format"), "quarter": ((dossiers[s].get("snapshot") or {}).get("quarter") or {}).get("label"),
            "results_url": ((dossiers[s].get("snapshot") or {}).get("quarter") or {}).get("source"), "tables_url": (dossiers[s].get("sources") or {}).get("screener"),
        } for s in ready],
        "rows": rows, "missing": [s for s in symbols if not dossiers[s]],
    }


def _series(table, *names):
    return (table.get(*names) if table else None) or []


def _cagr(vals: list, years: int) -> float | None:
    v = [x for x in vals if x is not None]
    if len(v) <= years or v[-1 - years] <= 0 or v[-1] <= 0:
        return None
    return round(((v[-1] / v[-1 - years]) ** (1 / years) - 1) * 100, 1)


def _pct_change(a, b):
    return round((a / b - 1) * 100, 1) if a is not None and b else None


def _yearly(table) -> tuple[list[str], int]:
    """Column labels without TTM, and how many trailing columns to drop for annual comparisons."""
    if not table:
        return [], 0
    cols = table.columns
    return cols, 1 if cols and cols[-1] == "TTM" else 0


# Which four numbers describe a company depends on what it files.
HEADLINES = {
    "banking": ("nii", "ppop", "pbt", "pat"), "nbfc": ("nii", "ppop", "pbt", "pat"), "indas": ("revenue", "ebitda", "pbt", "pat"),
    "life_insurer": ("gross_premium", "net_premium", "pbt", "pat"), "general_insurer": ("gross_premium", "net_premium", "underwriting", "pat"),
}
METRIC_LABELS = {"nii": "NII", "ppop": "Pre-provision profit", "pbt": "PBT", "pat": "PAT", "revenue": "Revenue", "ebitda": "EBITDA", "gross_premium": "Gross premium", "net_premium": "Net premium", "underwriting": "Underwriting result"}


def _sector_stats(q: nse.Quarter) -> list[dict]:
    """The ratios this kind of company is judged on, as filed."""
    rows = [
        ("Gross NPA", q.gross_npa_pct, "%"), ("Net NPA", q.net_npa_pct, "%"), ("Credit costs, ₹ cr", round(q.provisions) if q.provisions is not None else None, ""),
        ("Combined ratio", q.combined_ratio, "%"), ("Claims ratio", q.claims_ratio, "%"), ("Solvency", q.solvency, "×"), ("13-month persistency", q.persistency_13m, "%"),
    ]
    return [{"label": a, "value": round(b, 2), "unit": c} for a, b, c in rows if b is not None]


def financial_snapshot(c: screener.Company, quarters: list[nse.Quarter]) -> dict:
    banking = bool(quarters) and quarters[0].format in ("banking", "nbfc")
    latest = quarters[0] if quarters else None
    by_end = {q.period_end: q for q in quarters}

    def compare(q: nse.Quarter, field: str) -> dict:
        now = getattr(q, field)
        y, m, d = q.period_end.split("-")
        yoy = by_end.get(f"{int(y)-1}-{m}-{d}")
        idx = quarters.index(q)
        prev = quarters[idx + 1] if idx + 1 < len(quarters) else None
        return {
            "value": now,
            "qoq": _pct_change(now, getattr(prev, field)) if prev and (getattr(prev, field) or 0) > 0 and (now or 0) > 0 else None,
            "yoy": _pct_change(now, getattr(yoy, field)) if yoy and (getattr(yoy, field) or 0) > 0 and (now or 0) > 0 else None,
            "prev": getattr(prev, field) if prev else None,
            "year_ago": getattr(yoy, field) if yoy else None,
        }

    out = {"banking": banking, "ratios": c.ratios, "market_cap": c.market_cap, "price": c.price}
    if latest:
        keys = HEADLINES.get(latest.format, HEADLINES["indas"])
        out["quarter"] = {
            "format": latest.format, "sector_stats": _sector_stats(latest),
            "built": "Pre-provision profit is calculated from filed lines, because NBFCs don't file it." if latest.format == "nbfc" else None,
            "label": T.quarter_label(latest.period_end), "period_end": latest.period_end, "basis": latest.basis,
            "audited": latest.audited, "source": latest.source_url, "source_label": "screener.in's table" if latest.provider == "screener" else "Filed results (XBRL)",
            "metrics": {k: compare(latest, k) for k in keys},
            "exceptional": latest.exceptional if latest.exceptional else None,
            "margin": round(latest.ebitda / latest.revenue * 100, 1) if latest.ebitda and latest.revenue else None,
            "gross_npa": latest.gross_npa_pct, "net_npa": latest.net_npa_pct,
        }
    return out


def results_card(symbol: str, quarters: list[nse.Quarter]) -> dict | None:
    """The first cut of a results filing: exact figures, plus anything distorting them."""
    if not quarters:
        return None
    snap = financial_snapshot(screener.Company(symbol, symbol, True, "", None, None, None, {}, {}), quarters)
    q = snap.get("quarter")
    if not q:
        return None
    flags = []
    latest = quarters[0]
    if latest.exceptional and latest.pat and abs(latest.exceptional) >= abs(latest.pbt or 0) * 0.1:
        flags.append({
            "kind": "exceptional",
            "text": f"Includes a one-off of ₹{abs(latest.exceptional):,.0f} cr ({'gain' if latest.exceptional > 0 else 'charge'}). Reported profit isn't the run rate.",
        })
    y, m, d = latest.period_end.split("-")
    year_ago = next((x for x in quarters if x.period_end == f"{int(y)-1}-{m}-{d}"), None)
    flags += _base_flags(latest, year_ago)
    return {**q, "flags": flags, "banking": snap["banking"]}


def _base_flags(now: nse.Quarter, base: nse.Quarter | None) -> list[dict]:
    """A year-on-year number can mislead when one side has a big non-operating item in it."""
    if not base or now.format in ("banking", "nbfc"):
        return []
    out = []
    if base.exceptional and base.pbt and abs(base.exceptional) >= abs(base.pbt) * 0.15:
        out.append({"kind": "base_exceptional", "text": f"The year-ago quarter had a one-off of ₹{abs(base.exceptional):,.0f} cr, which skews the comparison."})
    swing = (base.other_income or 0) - (now.other_income or 0)
    if base.pbt and base.pbt > 0 and swing >= base.pbt * 0.25:
        tail = ""
        if now.ebitda and base.ebitda and base.ebitda > 0:
            tail = f" Operating EBITDA changed {(now.ebitda / base.ebitda - 1) * 100:+.0f}%."
        out.append({"kind": "other_income", "text": f"Other income was ₹{base.other_income:,.0f} cr a year ago against ₹{now.other_income or 0:,.0f} cr now, which flatters the base for profit.{tail}"})
    elif now.pbt and now.pbt > 0 and (now.other_income or 0) - (base.other_income or 0) >= now.pbt * 0.25:
        out.append({"kind": "other_income", "text": f"Other income of ₹{now.other_income:,.0f} cr (₹{base.other_income or 0:,.0f} cr a year ago) lifts this quarter's profit."})
    return out


def results_headline(card: dict) -> str:
    """The sentence an investor actually wants, written from the filed figures."""
    m = card["metrics"]
    top_key = next(iter(m), "revenue")
    top_name = METRIC_LABELS.get(top_key, "Revenue")

    def move(x):
        return f"{'up' if x > 0 else 'down'} {abs(x):.0f}%"

    parts = []
    t = m.get(top_key) or {}
    if t.get("yoy") is not None:
        parts.append(f"{top_name} {move(t['yoy'])} on the year")
    pat = m.get("pat") or {}
    if pat.get("value") is not None and pat["value"] < 0:
        parts.append(f"loss of ₹{abs(pat['value']):,.0f} cr")
    elif pat.get("yoy") is not None:
        parts.append(f"profit {move(pat['yoy'])}")
    elif pat.get("value") is not None and (pat.get("year_ago") or 0) < 0:
        parts.append("back to profit from a loss")
    if not parts:
        return f"{card['label']} results filed"
    text = ", ".join(parts)
    if any(f["kind"] == "exceptional" for f in card.get("flags", [])):
        text += ", with a one-off in the numbers"
    return text


def trajectory(c: screener.Company, banking: bool = False) -> dict | None:
    pl = c.tables.get("profit-loss")
    if not pl:
        return None
    cols, drop = _yearly(pl)
    end = len(cols) - drop
    sales = _series(pl, "Sales", "Revenue")[:end]
    opm = _series(pl, "OPM %", "Financing Margin %")[:end]
    profit = _series(pl, "Net Profit")[:end]
    eps = _series(pl, "EPS in Rs")[:end]
    n = 8
    points = {"years": cols[:end][-n:], "sales": sales[-n:], "opm": opm[-n:], "profit": profit[-n:], "eps": eps[-n:]}
    signals = []

    def note(label, vals, kind):
        c3, c5 = _cagr(vals, 3), _cagr(vals, 5)
        if c3 is None and c5 is None:
            return
        direction = "slowing" if c3 is not None and c5 is not None and c3 < c5 - 2 else "accelerating" if c3 is not None and c5 is not None and c3 > c5 + 2 else "steady"
        signals.append({"label": label, "c3": c3, "c5": c5, "direction": direction, "kind": kind})

    note("Revenue", sales, "growth")
    note("Profit", profit, "growth")
    margins = [m for m in opm if m is not None]
    margin_change = round(margins[-1] - margins[-4], 1) if len(margins) >= 4 else None
    if banking:
        # A "financing margin" on consolidated bank numbers isn't a meaningful operating margin.
        points["opm"], margins, margin_change = [], [], None
    return {"points": points, "signals": signals, "banking": banking, "margin_now": margins[-1] if margins else None, "margin_change_3y": margin_change}


def balance_sheet(c: screener.Company, banking: bool) -> dict | None:
    bs = c.tables.get("balance-sheet")
    if not bs:
        return None
    equity = _series(bs, "Equity Capital")
    reserves = _series(bs, "Reserves")
    debt = _series(bs, "Borrowings", "Borrowing")
    net_worth = [(a or 0) + (b or 0) for a, b in zip(equity, reserves)]
    de = [round(d / w, 2) if d is not None and w > 0 else None for d, w in zip(debt, net_worth)]
    r = c.tables.get("ratios")
    out = {
        "columns": bs.columns[-6:], "debt": debt[-6:], "net_worth": net_worth[-6:], "de": de[-6:], "banking": banking,
        "negative_net_worth": bool(net_worth and net_worth[-1] < 0),
        "cwip": _series(bs, "CWIP")[-6:], "deposits": _series(bs, "Deposits")[-6:],
    }
    if r:
        out["days"] = {
            "columns": r.columns[-5:], "debtor": _series(r, "Debtor Days")[-5:], "inventory": _series(r, "Inventory Days")[-5:],
            "payable": _series(r, "Days Payable")[-5:], "ccc": _series(r, "Cash Conversion Cycle")[-5:], "wc": _series(r, "Working Capital Days")[-5:],
        }
    return out


def cash_quality(c: screener.Company, banking: bool) -> dict | None:
    cf, pl = c.tables.get("cash-flow"), c.tables.get("profit-loss")
    if not cf:
        return None
    if banking:
        q = c.tables.get("quarters")
        return {
            "banking": True,
            "note": "A bank's cash flow mostly reflects loan and deposit flows, so it says little about earnings quality. Asset quality is the better test.",
            "npa": {"columns": q.columns[-8:], "gross": _series(q, "Gross NPA %")[-8:], "net": _series(q, "Net NPA %")[-8:]} if q else None,
        }
    cfo = _series(cf, "Cash from Operating")
    fcf = _series(cf, "Free Cash Flow")
    conv = _series(cf, "CFO/EBITDA")
    cols = cf.columns
    profit = dict(zip(pl.columns, _series(pl, "Net Profit"))) if pl else {}
    last5 = list(zip(cols, cfo, fcf))[-5:]
    pat_vals = [profit.get(col) for col, _, _ in last5]
    usable = [(x[1], p) for x, p in zip(last5, pat_vals) if x[1] is not None and p is not None]
    total_cfo, total_pat = sum(a for a, _ in usable), sum(b for _, b in usable)
    ratio = round(total_cfo / total_pat, 2) if total_pat > 0 else None
    verdict = None
    if ratio is not None:
        verdict = "strong" if ratio >= 1 else "adequate" if ratio >= 0.7 else "weak"
    return {
        "columns": [x[0] for x in last5], "cfo": [x[1] for x in last5], "fcf": [x[2] for x in last5], "pat": pat_vals,
        "conversion": conv[-5:], "cfo_to_pat_5y": ratio, "verdict": verdict, "banking": False,
    }


def business(c: screener.Company, quarters: list[nse.Quarter], filings_industry: str | None, log) -> dict:
    latest = quarters[0] if quarters else None
    segs = latest.segments if latest else []
    total = sum(s["revenue"] for s in segs if s["revenue"] > 0)
    mix = []
    for s in segs:
        if total and s["revenue"] > 0 and not re.search(r"elimination|unallocated|inter[- ]?segment", s["name"], re.I):
            mix.append({"name": s["name"], "revenue": s["revenue"], "share": round(s["revenue"] / total * 100, 1)})
    out = {"name": c.name, "industry": filings_industry, "website": c.website, "segments": mix, "description": None, "source": None}
    if not c.annual_reports:
        return out
    ar = c.annual_reports[0]
    try:
        log("Reading the annual report")
        doc = docs.load(ar["url"], headers=T.headers_for(ar["url"]), max_pages=30)
    except (FetchError, RuntimeError, ValueError):
        return out
    if doc.scanned or doc.chars < 500:
        return out
    # Annual reports often open with notices, so pick the pages that describe the business.
    cues = re.compile(r"\b(our business|business overview|about us|who we are|we are a|our segments?|products and services|our products|customers|revenue from)\b", re.I)
    ranked = sorted(range(len(doc.pages)), key=lambda i: (-len(cues.findall(doc.pages[i])), i))[:8]
    pages = "\n".join(f"[page {i + 1}] {T.mask(doc.pages[i], T.mask_terms(c.name))[:1800]}" for i in sorted(ranked))
    try:
        r = llm.ask_json(
            "business_v2", llm.CHEAP,
            "You describe what a listed company does for an investor, using only the annual report text given. No outside knowledge. Refer to it as 'the company'. "
            "Never comment on the text itself or what it omits. If it doesn't describe the business, return null for the description.",
            'Return JSON: {"description": "what it does and how it makes money, under 35 plain words, or null", "page": page number where this is stated}\n\n<source>\n' + pages[:llm.source_chars(16000)] + "\n</source>",
            max_tokens=300,
        )
        # A figure that isn't in the pages it read isn't shown.
        if isinstance(r, dict) and r.get("description") and E_numbers_in_source(r["description"], pages):
            out["description"] = r["description"]
            out["source"] = {"label": f"Annual Report {ar['year']}", "page": r.get("page"), "url": ar["url"]}
    except (llm.NoModel, llm.BudgetExceeded, ValueError):
        pass
    return out


_FLAG_PATTERNS = {
    "contingent": re.compile(r"contingent liabilit", re.I),
    "rpt": re.compile(r"related party transactions?", re.I),
    "auditor": re.compile(r"(qualified opinion|basis for (qualified )?opinion|emphasis of matter|material uncertainty|key audit matter|adverse opinion)", re.I),
}


def business_from_call(c: screener.Company, call: T.Call, log) -> dict | None:
    """Some annual reports are scanned or open with pages of notices. Management's own opening remarks are still primary."""
    speech = call.mgmt()[:60]
    if not speech:
        return None
    terms = T.mask_terms(c.name)
    lines = "\n".join(f"[{s.id}] {T.mask(s.text, terms)}" for s in speech)
    log("Reading what the company says it does")
    try:
        r = llm.ask_json(
            "business_call_v1", llm.CHEAP,
            "You describe what a listed company does for an investor, using only what its management said on an earnings call. No outside knowledge. "
            "Refer to it as 'the company'. If the text doesn't make the business clear, return null for the description.",
            'Return JSON: {"description": "what it does and how it makes money, under 35 plain words, or null", "id": "the sentence id that best shows this"}\n\n<source>\n' + lines[:llm.source_chars(12000)] + "\n</source>",
            max_tokens=300,
        )
    except (llm.NoModel, llm.BudgetExceeded, ValueError):
        return None
    if not isinstance(r, dict) or not r.get("description") or not E_numbers_in_source(r["description"], lines):
        return None
    cited = call.by_id().get(r.get("id")) or speech[0]
    return {"description": r["description"], "source": {"label": f"{call.label} earnings call", "page": cited.page, "url": call.url}}


_UNITS = {"crore": 1.0, "lakh": 0.01, "million": 0.1, "billion": 100.0}


def contingent_liabilities(doc: docs.Doc) -> dict | None:
    """The total from the notes, read from whole pages because it's usually a table low down the page.
    The figure has to be printed on the page it's cited from, or it isn't shown."""
    pages = [(i + 1, p) for i, p in enumerate(doc.pages) if _FLAG_PATTERNS["contingent"].search(p) and len(re.findall(r"\d[\d,]*\.?\d*", p)) >= 4]
    if not pages:
        return None
    # Consolidated accounts come last in an annual report, so the later pages are the ones wanted.
    chosen = pages[-6:]
    text = "\n\n".join(f"[page {i}] {p[:3500]}" for i, p in chosen)
    try:
        r = llm.ask_json(
            "contingent_v1", llm.CHEAP,
            "You read the notes to an Indian company's accounts. Use only what is printed. Use nulls if no total is stated. Don't add figures up yourself.",
            'Return JSON for the latest year\'s consolidated total of contingent liabilities (not commitments), or standalone if that\'s all there is: {"printed": "the total exactly as printed, such as 1,234.56", "unit": "crore", "lakh", "million" or "billion", "basis": "consolidated" or "standalone", "page": n}\n\n<source>\n' + text[:llm.source_chars(16000)] + "\n</source>",
            max_tokens=200,
        )
    except (llm.NoModel, llm.BudgetExceeded, ValueError):
        return None
    if not isinstance(r, dict) or not r.get("printed") or r.get("unit") not in _UNITS or not isinstance(r.get("page"), int):
        return None
    page = dict(chosen).get(r["page"])
    if not page or not E_numbers_in_source(str(r["printed"]), page):
        return None
    try:
        value = float(str(r["printed"]).replace(",", "")) * _UNITS[r["unit"]]
    except ValueError:
        return None
    return {"contingent_crore": round(value, 2), "contingent_page": r["page"], "contingent_basis": r.get("basis")}


def _page_holding(text: str, doc: docs.Doc, candidates: list[int], cited: int | None) -> int | None:
    """Models cite the wrong page now and then. The citation should point at where the figures actually are."""
    ok = lambda n: 1 <= n <= len(doc.pages) and E_numbers_in_source(text, doc.pages[n - 1])
    if cited and ok(cited):
        return cited
    return next((n for n in candidates if ok(n)), cited)


def annual_report_flags(c: screener.Company, log) -> dict | None:
    """Year-on-year changes in the notes, where things go to hide. Best effort; never blocks."""
    if len(c.annual_reports) < 1:
        return None
    reports = []
    # Comparing two years is a luxury the free tier can't always afford.
    for ar in c.annual_reports[:1 if llm.frugal() else 2]:
        try:
            log(f"Reading the notes in Annual Report {ar['year']}")
            d = docs.load(ar["url"], headers=T.headers_for(ar["url"]))
        except (FetchError, RuntimeError, ValueError):
            continue
        if d.scanned:
            continue
        # Only the pages that mention these terms: a 400-page report becomes a dozen pages.
        hits: dict[str, list[int]] = {k: [] for k in _FLAG_PATTERNS}
        for i, page in enumerate(d.pages, 1):
            for k, pat in _FLAG_PATTERNS.items():
                if pat.search(page):
                    hits[k].append(i)
        chosen = []
        for k, pgs in hits.items():
            chosen += pgs[-4:] if k == "contingent" else pgs[:4]
        chosen = sorted(set(chosen))[:12]
        text = "\n".join(f"[page {i}] {d.pages[i-1][:1800]}" for i in chosen)
        reports.append({"year": ar["year"], "url": ar["url"], "text": text, "pages": len(d.pages), "doc": d, "chosen": chosen})
    if not reports:
        return None
    out = []
    for r in reports:
        try:
            x = llm.ask_json(
                "ar_flags_v1", llm.CHEAP,
                "You extract specific disclosures from pages of an Indian company's annual report. Only what is stated. Use null when not stated.",
                'Return JSON: {"auditor": {"opinion": "unmodified"|"qualified"|"adverse"|"not_found", "emphasis": "matter the auditor emphasised, under 15 words, or null", "page": n or null}, '
                '"contingent_crore": consolidated total contingent liabilities in Rs crore if a total is given else null, "contingent_page": n or null, '
                '"rpt_note": "the largest or most notable related-party dealing, under 18 words, or null", "rpt_page": n or null}\n\n<source>\n' + r["text"][:llm.source_chars(14000)] + "\n</source>",
                max_tokens=400,
            )
        except (llm.NoModel, llm.BudgetExceeded, ValueError):
            return None
        if isinstance(x, dict):
            # A figure that isn't printed on the cited page doesn't get shown.
            v, pg = x.get("contingent_crore"), x.get("contingent_page")
            if isinstance(v, (int, float)) and isinstance(pg, int) and 0 < pg <= len(r["doc"].pages):
                digits = re.sub(r"\D", "", f"{v:.0f}")[:3]
                if digits not in re.sub(r"[^\d]", "", r["doc"].pages[pg - 1]):
                    x["contingent_crore"] = None
            else:
                x["contingent_crore"] = None
            if x.get("rpt_note") and not E_numbers_in_source(x["rpt_note"], r["text"]):
                x["rpt_note"] = None
            if x.get("rpt_note"):
                x["rpt_page"] = _page_holding(x["rpt_note"], r["doc"], r["chosen"], x.get("rpt_page"))
                # If its figures aren't on any page it could cite, nobody could check it, so it isn't shown.
                cited_page = x["rpt_page"] if isinstance(x["rpt_page"], int) and 1 <= x["rpt_page"] <= len(r["doc"].pages) else None
                if not cited_page or not E_numbers_in_source(x["rpt_note"], r["doc"].pages[cited_page - 1]):
                    x["rpt_note"] = None
            if not x.get("contingent_crore"):
                x.update(contingent_liabilities(r["doc"]) or {})
            if isinstance(x.get("auditor"), dict) and x["auditor"].get("emphasis") and not E_numbers_in_source(x["auditor"]["emphasis"], r["text"]):
                x["auditor"]["emphasis"] = None
            out.append({"year": r["year"], "url": r["url"], **x})
    change = None
    if len(out) == 2 and out[0].get("contingent_crore") and out[1].get("contingent_crore"):
        change = _pct_change(out[0]["contingent_crore"], out[1]["contingent_crore"])
    return {"reports": out, "contingent_change_pct": change}


DODGE_SYSTEM = (
    "You read analyst questions and management's replies from an earnings call. Flag only replies that clearly avoid a direct question: "
    "the analyst asked for a specific figure or a yes/no, and management changed the subject, declined, or stayed general. A hard but real answer "
    "isn't a dodge. Most replies are fine, and returning nothing is a good answer."
)


def dodged_questions(call: T.Call, terms: list[str]) -> list[dict]:
    """Questions management visibly didn't answer. Quotes come from sentence ids only."""
    pairs = []
    sents = call.sentences
    i = 0
    while i < len(sents):
        s = sents[i]
        if s.role == "analyst" and s.text.endswith("?"):
            j = i + 1
            while j < len(sents) and sents[j].role == "analyst":
                j += 1
            answer = []
            while j < len(sents) and sents[j].role in ("management", "unknown") and len(answer) < 7:
                answer.append(sents[j])
                j += 1
            if answer and re.search(r"\d|guid|margin|target|when|how much|debt|capex|will", s.text, re.I):
                pairs.append((s, answer))
            i = j
        else:
            i += 1
    if not pairs:
        return []
    body = "\n\n".join(
        f"Q {q.id}: {T.mask(q.text, terms)}\n" + "\n".join(f"A {a.id}: {T.mask(a.text, terms)}" for a in ans) for q, ans in pairs[:30]
    )
    try:
        r = llm.ask_json(
            "dodge_v1", llm.CHEAP, DODGE_SYSTEM,
            'Return a JSON array, usually short. Each element: {"q": the question id, "a": the id of the most telling reply sentence, "why": "what was asked and not answered, under 14 words"}\n\n<source>\n' + body[:llm.source_chars(10**9)] + "\n</source>",
            max_tokens=900,
        )
    except (llm.NoModel, llm.BudgetExceeded, ValueError):
        return []
    by_id = call.by_id()
    out = []
    for x in r if isinstance(r, list) else []:
        q, a = by_id.get(x.get("q")), by_id.get(x.get("a"))
        if q and a and q.role == "analyst" and a.role != "analyst":
            out.append({"question": q.text, "asker": q.speaker, "answer": a.text, "answerer": a.speaker, "page": q.page, "why": x.get("why"), "call_label": call.label, "url": call.url})
    return out[:5]


BULLBEAR_SYSTEM = (
    "You write a tight bull and bear case for an investor. You may use ONLY the numbered facts given, which come from filed results and "
    "earnings-call transcripts. Do not use outside knowledge about the company, its sector, its competitors or the economy. Refer to it as 'the company'. "
    "Each point must rest on the facts it cites. Be specific, use the figures, and don't hedge. Never recommend buying or selling."
)


def bull_bear(facts: list[dict], terms: list[str]) -> dict | None:
    if len(facts) < 4:
        return None
    lines = "\n".join(f"[F{i}] {T.mask(f['text'], terms)}" for i, f in enumerate(facts))
    try:
        r = llm.ask_json(
            "bullbear_v1", llm.STRONG, BULLBEAR_SYSTEM,
            'Return JSON: {"bull": [{"point": "under 18 words", "cites": ["F3"]}], "bear": [same], "swing": "the single question that decides between them, under 18 words"}. Give 2 to 3 points each.\n\n<source>\n' + lines + "\n</source>",
            max_tokens=900,
        )
    except (llm.NoModel, llm.BudgetExceeded, ValueError):
        return None
    if not isinstance(r, dict):
        return None

    def resolve(items):
        out = []
        for it in items or []:
            idx = [int(c[1:]) for c in it.get("cites") or [] if re.fullmatch(r"F\d+", str(c)) and int(str(c)[1:]) < len(facts)]
            # An argument with no surviving citation is opinion, and a figure its citations don't contain is
            # invented. Neither gets shown.
            if it.get("point") and idx and P._note_is_sourced(it["point"], *[facts[i]["text"] for i in idx]):
                out.append({"point": it["point"], "sources": [facts[i] for i in idx]})
        return out

    swing = r.get("swing")
    if swing and not P._note_is_sourced(swing, *[f["text"] for f in facts]):
        swing = None
    return {"bull": resolve(r.get("bull")), "bear": resolve(r.get("bear")), "swing": swing}


def collect_facts(snapshot, traj, sheet, cash, pr, tables_url: str | None = None) -> list[dict]:
    """Plain statements computed from filed data, for the final argument to rest on. tables_url is the page holding the multi-year tables."""
    facts = []
    q = snapshot.get("quarter")
    if q:
        for k, m in q["metrics"].items():
            if m["value"] is not None:
                s = f"{q['label']} {k.upper()} ₹{m['value']:,.0f} cr"
                if m["yoy"] is not None:
                    s += f", {m['yoy']:+.1f}% YoY"
                if m["qoq"] is not None:
                    s += f", {m['qoq']:+.1f}% QoQ"
                facts.append({"text": s, "source": "Filed results", "url": q["source"]})
        if q.get("margin"):
            facts.append({"text": f"{q['label']} EBITDA margin {q['margin']}%", "source": "Filed results", "url": q["source"]})
        # Without this, a one-off that sank PBT gets explained away as "costs".
        if q.get("exceptional"):
            kind = "gain" if q["exceptional"] > 0 else "charge"
            facts.append({"text": f"{q['label']} PBT includes a one-off {kind} of ₹{abs(q['exceptional']):,.0f} cr", "source": "Filed results", "url": q["source"]})
    if traj:
        for sig in traj["signals"]:
            if sig["c3"] is not None:
                facts.append({"text": f"{sig['label']} 3-year CAGR {sig['c3']}%" + (f", 5-year {sig['c5']}%" if sig["c5"] is not None else ""), "source": "Annual results", "url": tables_url})
        if traj["margin_change_3y"] is not None:
            facts.append({"text": f"Operating margin {traj['margin_now']}%, {traj['margin_change_3y']:+.1f} points over 3 years", "source": "Annual results", "url": tables_url})
    if sheet:
        if sheet["de"] and sheet["de"][-1] is not None and not sheet["banking"]:
            was = f" (was {sheet['de'][0]} in {sheet['columns'][0]})" if sheet["de"][0] is not None else ""
            facts.append({"text": f"Debt-to-equity {sheet['de'][-1]}{was}", "source": "Balance sheet", "url": tables_url})
        if sheet.get("negative_net_worth"):
            facts.append({"text": "Net worth is negative", "source": "Balance sheet", "url": tables_url})
        days = sheet.get("days")
        if days and days["ccc"] and days["ccc"][-1] is not None:
            was = f" (was {days['ccc'][0]:.0f} in {days['columns'][0]})" if days["ccc"][0] is not None else ""
            facts.append({"text": f"Cash conversion cycle {days['ccc'][-1]:.0f} days{was}", "source": "Ratios", "url": tables_url})
    if cash and not cash.get("banking") and cash.get("cfo_to_pat_5y") is not None:
        facts.append({"text": f"Operating cash flow was {cash['cfo_to_pat_5y']}x net profit over the last five years", "source": "Cash flow", "url": tables_url})
    if pr:
        for p in pr["promises"]:
            if p["verdict"] in (P.DELIVERED, P.MISSED, P.PARTLY):
                facts.append({"text": f"Management said on the {p['call_label']} call: {p['claim']}. Outcome: {p['verdict']}. {p.get('note', '')}", "source": f"{p['call_label']} call, p.{p['page']}", "url": p["url"], "page": p["page"]})
        for p in [x for x in pr["promises"] if x["verdict"] == P.NOT_DUE][:6]:
            facts.append({"text": f"Management's open commitment: {p['claim']}", "source": f"{p['call_label']} call, p.{p['page']}", "url": p["url"], "page": p["page"]})
    return facts


def build(symbol: str, log=lambda *_: None, full: bool = True, quick: bool = False) -> dict:
    symbol = symbol.upper().strip()
    log("Finding the company")
    try:
        c = screener.company(symbol)
    except FetchError as e:
        # Only a real "no such page" means the ticker is wrong. Anything else is our sources being down.
        if e.status == 404:
            raise UnknownTicker(symbol) from e
        raise

    log("Pulling filed results")
    nse_down = False
    try:
        quarters = nse.quarters(symbol)
        pipeline.refresh_company(symbol, in_portfolio=False) if not _in_portfolio(symbol) else None
    except FetchError:
        # Screener's table isn't the filing, but it lets a company get a record while NSE won't answer.
        quarters, nse_down = pipeline.results_from_screener(symbol, _in_portfolio(symbol), c), True
    except (ValueError, KeyError):
        quarters = []
    banking = bool(quarters) and quarters[0].format in ("banking", "nbfc")
    industry = None
    row = db().execute("SELECT industry, name FROM filings WHERE symbol=? AND industry IS NOT NULL LIMIT 1", (symbol,)).fetchone()
    if row:
        industry = row["industry"]

    snapshot = financial_snapshot(c, quarters)
    traj = trajectory(c, banking)
    sheet = balance_sheet(c, banking)
    cash = cash_quality(c, banking)
    biz = business(c, quarters, industry, log)
    if quick:
        # What's already filed and calculated, up in seconds. The record is read behind it.
        return {
            "symbol": symbol, "name": c.name, "consolidated": c.consolidated, "generated": datetime.now().isoformat(timespec="seconds"),
            "business": biz, "snapshot": snapshot, "trajectory": traj, "balance_sheet": sheet, "cash": cash,
            "promises": None, "dodged": [], "ar_flags": None, "arguments": None, "missing": [], "kpis": [], "forces": None,
            "peers": screener.peers(c), "incomplete": nse_down and not quarters,
            "sources": {"screener": c.source_url, "transcripts": [], "annual_reports": c.annual_reports[:2]},
        }

    calls = load_calls(c, log)
    terms = T.mask_terms(c.name)
    if not biz["description"] and calls:
        biz.update(business_from_call(c, calls[0], log) or {})
    earnings_calls = bool(calls)
    if not calls:
        # No calls to hold them to. The annual report is the next best record of what they said.
        calls = annual_report_calls(c, log)
    else:
        for call in calls:
            docs.keep(docs.load(call.url, headers=T.headers_for(call.url)))
    pr = P.run(calls, quarters, c.name, log) if calls else None
    if pr:
        pr["source"] = "earnings_calls" if earnings_calls else "annual_reports"
        pr = P.keep_settled(pr, load(symbol))
    dodged = dodged_questions(calls[0], terms) if earnings_calls else []
    kpis = company_kpis(calls[0], terms) if earnings_calls else []
    forces = strategic_forces(calls, terms) if full and calls else None
    flags = None
    if full:
        try:
            flags = annual_report_flags(c, log)
        except Exception:  # annual reports are huge and varied; this section is a bonus
            flags = None

    log("Writing the bull and bear case")
    facts = collect_facts(snapshot, traj, sheet, cash, pr, c.source_url)
    arguments = bull_bear(facts, terms)

    missing = []
    if nse_down and quarters:
        missing.append("NSE couldn't be reached just now, so these results and the scored promises use screener.in's quarterly table, not the filing itself.")
    elif nse_down:
        missing.append("NSE couldn't be reached just now, so filed results and the promise record are missing. FINN will try again when you next open this company.")
    elif not quarters:
        missing.append("No structured results on NSE, so quarterly figures aren't shown.")
    if not calls:
        missing.append("No earnings calls or readable annual reports were found, so there's no promise record.")
    elif not earnings_calls:
        missing.append("This company has no readable earnings calls, so this record comes from what its annual reports said it would do.")
    elif len(calls) < 3:
        missing.append(f"Only {len(calls)} of the recent earnings calls could be read, so this track record is partial.")
    old = _old_calls_note(calls, quarters) if earnings_calls else None
    if old:
        missing.append(old)
    if pr and not pr["model"]:
        missing.append("No model was available (none configured, or today's free allowance used up), so promises weren't extracted for this company yet. FINN tries again when you next open it.")
    if pr and pr.get("unread_calls"):
        missing.append(f"The model failed on {pr['unread_calls']} of {len(calls)} calls, so this track record is partial. FINN will try again when you next open this company.")

    return {
        "symbol": symbol, "name": c.name, "consolidated": c.consolidated, "generated": datetime.now().isoformat(timespec="seconds"),
        "business": biz, "snapshot": snapshot, "trajectory": traj, "balance_sheet": sheet, "cash": cash,
        "promises": pr, "dodged": dodged, "ar_flags": flags, "arguments": arguments, "missing": missing, "built_by": llm.provider(), "kpis": kpis, "forces": forces, "peers": screener.peers(c),
        "incomplete": (nse_down and not quarters) or bool(pr and pr.get("unread_calls")),
        "sources": {"screener": c.source_url, "transcripts": [x for x in (pr or {}).get("calls", [])], "annual_reports": c.annual_reports[:2]},
    }


def _old_calls_note(calls: list[T.Call], quarters: list[nse.Quarter]) -> str | None:
    """A record built on calls from years ago can't be read as what management is promising now."""
    if not calls or not quarters or not calls[0].period_end:
        return None
    gap = date.fromisoformat(quarters[0].period_end) - date.fromisoformat(calls[0].period_end)
    if gap.days <= 270:
        return None
    return f"The newest earnings call FINN could read is from {calls[0].label}, while filed results run to {T.quarter_label(quarters[0].period_end)}. Treat this track record as history."


def _in_portfolio(symbol: str) -> bool:
    row = db().execute("SELECT in_portfolio FROM companies WHERE symbol=?", (symbol,)).fetchone()
    return bool(row and row["in_portfolio"])


def save(symbol: str, dossier: dict) -> None:
    db().execute("INSERT OR REPLACE INTO dossiers VALUES (?,?,?)", (symbol, json.dumps(dossier, ensure_ascii=False), dossier["generated"]))
    db().commit()


def load(symbol: str) -> dict | None:
    row = db().execute("SELECT data FROM dossiers WHERE symbol=?", (symbol.upper(),)).fetchone()
    return json.loads(row["data"]) if row else None
