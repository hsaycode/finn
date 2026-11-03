"""NSE: the announcement feed plus structured (XBRL) quarterly results.

Results come from the exact XBRL the company filed, so the headline numbers on a
results card involve no model and no PDF parsing. SEBI moved results to "Integrated
Filing" in early 2025, so history spans two endpoints.
"""
from __future__ import annotations

import hashlib
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime

from bs4 import BeautifulSoup

from .net import fetch, fetch_json

BASE = "https://www.nseindia.com/api"
# NSE's archive host publishes the same filings as RSS, and it's a separate door if a cloud host's address is refused by the API.
ARCHIVE_RSS = "https://nsearchives.nseindia.com/content/RSS"
HEADERS = {"Referer": "https://www.nseindia.com/"}
_IF_TYPE = urllib.parse.quote("Integrated Filing- Financials")


def _dt(s: str | None, fmt: str = "%d-%b-%Y %H:%M:%S") -> datetime | None:
    try:
        return datetime.strptime(s.strip().title() if "%b" in fmt else s.strip(), fmt) if s else None
    except ValueError:
        return None


@dataclass
class Filing:
    id: str
    symbol: str
    name: str
    ts: str  # ISO, IST
    raw_category: str
    text: str
    pdf_url: str | None
    size_kb: float | None = None
    industry: str | None = None


def _kb(s: str | None) -> float | None:
    m = re.match(r"([\d.]+)\s*(KB|MB|Bytes)", s or "")
    if not m:
        return None
    return float(m[1]) * {"KB": 1, "MB": 1024, "Bytes": 1 / 1024}[m[2]]


def _strip_boilerplate(text: str, name: str) -> str:
    """NSE prefixes every line with '<Company> has informed the Exchange ...'."""
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(
        r"^(?:the\s+)?[\w&.,'()\- ]{2,70}?\s+has\s+(?:informed|submitted(?:\s+to)?)\s+(?:the\s+)?Exchange\s*"
        r"(?:regarding|about|that|of|for|on)?\s*",
        "",
        t,
        flags=re.I,
    )
    t = t.strip(" ,'\"‘’“”.")
    return (t[:1].upper() + t[1:]) if t else ""


def announcements(symbol: str | None, frm: datetime, to: datetime, ttl: float = 900) -> list[Filing]:
    q = f"index=equities&from_date={frm:%d-%m-%Y}&to_date={to:%d-%m-%Y}"
    if symbol:
        q += f"&symbol={urllib.parse.quote(symbol)}"
    rows = fetch_json(f"{BASE}/corporate-announcements?{q}", ttl=ttl, headers=HEADERS)
    out = []
    for r in rows:
        ts = _dt(r.get("an_dt"))
        if not ts:
            continue
        name = r.get("sm_name") or r.get("symbol")
        out.append(
            Filing(
                id=str(r.get("seq_id")),
                symbol=r.get("symbol"),
                name=name,
                ts=ts.isoformat(),
                raw_category=(r.get("desc") or "").strip(),
                text=_strip_boilerplate(r.get("attchmntText") or "", name),
                pdf_url=r.get("attchmntFile") or None,
                size_kb=_kb(r.get("attFileSize")),
                industry=r.get("smIndustry"),
            )
        )
    return out


def _feed(name: str, ttl: float) -> list[dict]:
    root = ET.fromstring(fetch(f"{ARCHIVE_RSS}/{name}.xml", ttl=ttl, headers=HEADERS, retries=2))
    return [{k: (item.findtext(k) or "").strip() for k in ("title", "link", "description", "pubDate")} for item in root.iter("item")]


def feed_filings(ttl: float = 600) -> list[Filing]:
    """Today's filings across the market, with the links and labels the API gives. Each has no symbol yet, only a company name."""
    out = []
    for r in _feed("Online_announcements", ttl):
        ts = _dt(r["pubDate"])
        # The API lists documents only. The feed adds fund NAVs with no link and XBRL copies of the same filings.
        if not ts or not r["link"].lower().endswith(".pdf"):
            continue
        text, _, subject = r["description"].partition("|SUBJECT:")
        out.append(Filing(
            id=f"rss-{hashlib.sha256(r['link'].encode()).hexdigest()[:16]}", symbol="", name=r["title"], ts=ts.isoformat(),
            raw_category=subject.strip(), text=_strip_boilerplate(text, r["title"]), pdf_url=r["link"],
        ))
    return out


def feed_results(ttl: float = 600) -> list[dict]:
    """The latest results filed, each with its XBRL. It holds only the last 20, so a busy evening can outrun it."""
    return [{"name": r["title"], "xbrl": r["link"], "filed_at": (_dt(r["pubDate"]) or datetime.min).isoformat()}
            for r in _feed("Integrated_Filing_Financials", ttl) if r["link"].lower().endswith(".xml")]


def quarter_from_xbrl(xbrl_url: str, filed_at: str | None = None) -> Quarter | None:
    """An integrated filing says which company, period and basis it covers, so it needs no index to read."""
    xml = fetch(xbrl_url, ttl=None, headers=HEADERS, retries=2)
    soup = BeautifulSoup(xml, "xml")

    def first(tag: str) -> str:
        el = soup.find(tag)
        return el.text.strip() if el else ""

    symbol, end = first("Symbol"), first("DateOfEndOfReportingPeriod")
    if not symbol or not end:
        return None
    meta = {
        "period_end": end, "basis": "consolidated" if first("NatureOfReportStandaloneConsolidated") == "Consolidated" else "standalone",
        "xbrl": xbrl_url, "audited": first("WhetherResultsAreAuditedOrUnaudited") == "Audited", "filed_at": filed_at,
    }
    return parse_xbrl(xml, symbol, meta)


def feed_events(ttl: float = 3600) -> list[dict]:
    """Newly announced board meetings and ex-dates, for each with the company's name."""
    today = datetime.now().date().isoformat()
    out = []
    for r in _feed("Board_Meetings", ttl):
        m = re.search(r"Meeting Date:\s*([\d]{1,2}-[A-Za-z]{3}-\d{4})", r["description"])
        d = _day(m[1]) if m else None
        if d and d >= today:
            results = bool(re.search(r"financial results", r["description"], re.I))
            out.append({"name": r["title"], "date": d, "kind": "results" if results else "board", "title": "Results due: board meets" if results else "Board meeting"})
    for r in _feed("Corporate_action", ttl):
        m = re.match(r"(.*?)\s*-\s*Ex-Date:\s*(\S+)", r["title"])
        d = _day(m[2]) if m else None
        purpose = re.search(r"PURPOSE:([^|]*)", r["description"])
        if d and d >= today:
            out.append({"name": m[1], "date": d, "kind": "action", "title": f"Goes ex: {(purpose[1].strip().title() if purpose else 'corporate action')}"})
    return out


def _day(value: str | None) -> str | None:
    """NSE writes dates as 30-Oct-2026. Empty ones arrive as a dash."""
    try:
        return datetime.strptime((value or "").strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def upcoming_events(symbol: str, ttl: float = 21600) -> list[dict]:
    """Board meetings and corporate actions the company has already announced. All of it is filed, so none of it is a forecast."""
    q = f"index=equities&symbol={urllib.parse.quote(symbol)}"
    today = datetime.now().date().isoformat()
    out = {}
    for r in fetch_json(f"{BASE}/corporate-board-meetings?{q}", ttl=ttl, headers=HEADERS):
        d = _day(r.get("bm_date"))
        text = " ".join(str(r.get(k) or "") for k in ("bm_purpose", "bm_desc"))
        if d and d >= today:
            results = re.search(r"financial results|results of the company", text, re.I)
            if results or (d, "results") not in out:
                out[d, "results" if results else "board"] = "Results due: board meets" if results else "Board meeting"
            if results:
                out.pop((d, "board"), None)
    for r in fetch_json(f"{BASE}/corporates-corporateActions?{q}", ttl=ttl, headers=HEADERS):
        d = _day(r.get("exDate"))
        if d and d >= today:
            out[d, "action"] = f"Goes ex: {(r.get('subject') or 'corporate action').strip()}"
    return [{"symbol": symbol, "date": d, "kind": k, "title": t} for (d, k), t in sorted(out.items())]


@dataclass
class Quarter:
    symbol: str
    period_end: str  # YYYY-MM-DD
    basis: str  # consolidated | standalone
    format: str  # indas | banking
    revenue: float | None = None  # Rs crore throughout
    other_income: float | None = None
    ebitda: float | None = None
    pbt: float | None = None
    pat: float | None = None  # attributable to the parent's shareholders
    pat_total: float | None = None  # before minority interests
    finance_costs: float | None = None
    depreciation: float | None = None
    exceptional: float | None = None
    ppop: float | None = None  # banks: pre-provision operating profit
    nii: float | None = None  # banks: net interest income
    provisions: float | None = None  # banks and NBFCs
    gross_premium: float | None = None  # insurers
    net_premium: float | None = None
    underwriting: float | None = None
    solvency: float | None = None  # a multiple, such as 1.97
    combined_ratio: float | None = None  # percent
    claims_ratio: float | None = None
    persistency_13m: float | None = None
    gross_npa_pct: float | None = None
    net_npa_pct: float | None = None
    audited: bool = False
    filed_at: str | None = None
    source_url: str | None = None
    months: int = 3
    provider: str = "xbrl"  # screener when NSE couldn't be reached
    segments: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _period_end(s: str) -> str | None:
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(s.strip().title(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def result_filings(symbol: str, ttl: float = 21600) -> list[dict]:
    """Every quarterly XBRL we can find, newest first; the integrated feed wins on overlap."""
    sym = urllib.parse.quote(symbol)
    seen: dict[tuple[str, str], dict] = {}
    integrated = fetch_json(
        f"{BASE}/integrated-filing-results?index=equities&symbol={sym}&period_ended=all&type={_IF_TYPE}",
        ttl=ttl,
        headers=HEADERS,
    ).get("data") or []
    for r in integrated:
        end, basis = _period_end(r.get("qe_Date") or ""), (r.get("consolidated") or "")
        if end and r.get("xbrl") and basis:
            key = (end, "consolidated" if basis == "Consolidated" else "standalone")
            prev = seen.get(key)
            # A later original or revision supersedes an earlier one.
            if not prev or (_dt(r.get("broadcast_Date")) or datetime.min) > datetime.fromisoformat(prev["filed_at"]):
                seen[key] = {
                    "period_end": end,
                    "basis": key[1],
                    "xbrl": r["xbrl"],
                    "audited": r.get("audited") == "Audited",
                    "filed_at": (_dt(r.get("broadcast_Date")) or datetime.min).isoformat(),
                }
    legacy = fetch_json(f"{BASE}/corporates-financial-results?index=equities&symbol={sym}&period=Quarterly", ttl=ttl, headers=HEADERS)
    for r in legacy if isinstance(legacy, list) else []:
        end = _period_end(r.get("toDate") or "")
        if not end or not r.get("xbrl") or r.get("period") != "Quarterly":
            continue
        key = (end, "consolidated" if r.get("consolidated") == "Consolidated" else "standalone")
        if key not in seen:
            seen[key] = {
                "period_end": end,
                "basis": key[1],
                "xbrl": r["xbrl"],
                "audited": r.get("audited") == "Audited",
                "filed_at": (_dt(r.get("broadcastDate") or r.get("broadCastDate")) or datetime.min).isoformat(),
            }
    return sorted(seen.values(), key=lambda r: r["period_end"], reverse=True)


# Tags vary between the Ind-AS and banking taxonomies; first match wins.
_TAGS = {
    "revenue": ["RevenueFromOperations"],
    "other_income": ["OtherIncome"],
    "finance_costs": ["FinanceCosts"],
    "depreciation": ["DepreciationDepletionAndAmortisationExpense"],
    "exceptional": ["ExceptionalItemsBeforeTax", "ExceptionalItems"],
    "pbt": ["ProfitBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax", "ProfitLossBeforeTax", "ProfitOrLossBeforeTax"],
    "pat_total": ["ProfitLossForPeriod", "ProfitLossForThePeriod", "ProfitLossForPeriodBeforeMinorityInterest", "ProfitLossAfterTaxAndExtraordinaryItems", "ProfitLossAfterTax"],
    "pat_parent": ["ProfitOrLossAttributableToOwnersOfParent", "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates"],
    "interest_earned": ["InterestEarned"],
    "interest_expended": ["InterestExpended"],
    "income": ["Income"],
    "ppop": ["OperatingProfitBeforeProvisionAndContingencies"],
    "provisions": ["ProvisionsOtherThanTaxAndContingencies"],
    "segment_pbt": ["SegmentProfitBeforeTax"],
    "impairment": ["ImpairmentOnFinancialInstruments"],
    "gross_premium": ["GrossPremiumIncome", "GrossPremiumsWritten"],
    "net_premium": ["NetPremiumIncome", "NetPremiumWritten"],
    "underwriting": ["UnderwritingProfitOrLoss"],
    "solvency": ["SolvencyRatio"],
    "combined_ratio": ["CombinedRatio", "CombinedOperatingRatio"],
    "claims_ratio": ["IncurredClaimRatio"],
    "persistency_13m": ["PersistencyRatio13ThMonth"],
    "gross_npa_pct": ["PercentageOfGrossNpa"],
    "net_npa_pct": ["PercentageOfNpa"],
}


def _facts(xml: bytes) -> tuple[dict[str, dict[str, str]], dict[str, tuple[str | None, str | None]]]:
    soup = BeautifulSoup(xml, "xml")
    contexts: dict[str, tuple[str | None, str | None]] = {}
    for c in soup.find_all("context"):
        start, end, inst = c.find("startDate"), c.find("endDate"), c.find("instant")
        contexts[c.get("id")] = (start.text.strip() if start else None, (end or inst).text.strip() if (end or inst) else None)
    facts: dict[str, dict[str, str]] = {}
    for el in soup.find_all(True):
        ctx = el.get("contextRef")
        if ctx and el.string is not None:
            facts.setdefault(el.name, {})[ctx] = el.string.strip()
    return facts, contexts


def _months(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    return round((b - a).days / 30.4)


def parse_xbrl(xml: bytes, symbol: str, meta: dict) -> Quarter:
    facts, contexts = _facts(xml)
    end = meta["period_end"]

    # Pick the context holding this quarter alone. Year-to-date and prior-period columns
    # share the same tags, so the date span is what tells them apart.
    def contexts_for(tag: str) -> str | None:
        vals = facts.get(tag) or {}
        best = None
        for ctx in vals:
            s, e = contexts.get(ctx, (None, None))
            if e == end and _months(s, e) == 3:
                if best is None or ctx == "OneD":
                    best = ctx
        return best

    def val(key: str) -> float | None:
        for tag in _TAGS[key]:
            ctx = contexts_for(tag)
            if ctx:
                try:
                    return float(facts[tag][ctx])
                except ValueError:
                    return None
        return None

    def crore(key: str) -> float | None:
        v = val(key)
        return round(v / 1e7, 2) if v is not None else None

    # What a company files decides which numbers describe it. EBITDA means nothing for a lender or an insurer.
    insurer = "SolvencyRatio" in facts and ("GrossPremiumIncome" in facts or "GrossPremiumsWritten" in facts or "IncurredClaims" in facts)
    life = insurer and "IncomeFirstYearPremium" in facts
    banking = "InterestEarned" in facts and "InterestExpended" in facts
    nbfc = "InterestEarned" in facts and not banking
    q = Quarter(
        symbol=symbol,
        period_end=end,
        basis=meta["basis"],
        format="life_insurer" if life else "general_insurer" if insurer else "banking" if banking else "nbfc" if nbfc else "indas",
        audited=meta.get("audited", False),
        filed_at=meta.get("filed_at"),
        source_url=meta.get("xbrl"),
    )
    q.other_income = crore("other_income")
    q.finance_costs = crore("finance_costs")
    q.depreciation = crore("depreciation")
    q.exceptional = crore("exceptional")
    q.pbt = crore("pbt")
    q.pat_total = crore("pat_total")
    q.pat = crore("pat_parent")
    # Some older filings leave the attributable line blank or near zero; don't report that as profit.
    if q.pat is None or (q.pat_total and abs(q.pat) < abs(q.pat_total) * 0.05):
        q.pat = q.pat_total

    if banking:
        earned, spent = crore("interest_earned"), crore("interest_expended")
        q.revenue = crore("income") or earned
        q.nii = round(earned - spent, 2) if earned is not None and spent is not None else None
        q.ppop = crore("ppop")
        q.provisions = crore("provisions")
        # HDFC Bank files minority interest as an exceptional item in alternate quarters, leaving PBT
        # after it. Only undo that when the filing's own segment total confirms it.
        segment_pbt = crore("segment_pbt")
        if q.exceptional and q.pbt is not None and segment_pbt is not None and abs(q.pbt - q.exceptional - segment_pbt) < 1:
            q.pbt = segment_pbt
            q.pat_total = round(q.pat_total - q.exceptional, 2) if q.pat_total is not None else None
            q.exceptional = 0.0
        q.gross_npa_pct = val("gross_npa_pct") or None
        q.net_npa_pct = val("net_npa_pct") or None
    elif nbfc:
        earned, spent = crore("interest_earned"), q.finance_costs
        q.revenue = crore("revenue") or crore("income") or earned
        q.nii = round(earned - spent, 2) if earned is not None and spent is not None else None
        q.provisions = crore("impairment")
        # NBFCs don't file pre-provision profit, so it's built from lines they do file.
        if q.pbt is not None:
            q.ppop = round(q.pbt - (q.exceptional or 0) + (q.provisions or 0), 2)
    elif insurer:
        q.gross_premium, q.net_premium, q.underwriting = crore("gross_premium"), crore("net_premium"), crore("underwriting")
        q.revenue = q.gross_premium or crore("income")
        # Insurers file ratios on different scales: 1.07 or 107 for a combined ratio.
        as_pct = lambda v: round(v * 100, 2) if v is not None and abs(v) < 5 else v
        q.combined_ratio, q.claims_ratio, q.persistency_13m = as_pct(val("combined_ratio")), as_pct(val("claims_ratio")), as_pct(val("persistency_13m"))
        solvency = val("solvency")
        q.solvency = round(solvency * 100, 2) if solvency is not None and solvency < 0.5 else solvency
    else:
        q.revenue = crore("revenue")
        if q.pbt is not None and q.depreciation is not None and q.finance_costs is not None:
            # Operating EBITDA: strip other income and one-offs, since neither is the core business.
            q.ebitda = round(
                q.pbt + q.finance_costs + q.depreciation - (q.other_income or 0) - (q.exceptional or 0),
                2,
            )

    q.segments = _segments(facts, contexts, end)
    if q.revenue is None:
        q.warnings.append("revenue missing from filing")
    if q.pbt is None or q.pat is None:
        q.warnings.append("profit lines missing from filing")
    return q


def _segments(facts, contexts, end) -> list[dict]:
    names, revenue = facts.get("DescriptionOfReportableSegment") or {}, facts.get("SegmentRevenue") or {}
    out = []
    for ctx, v in revenue.items():
        s, e = contexts.get(ctx, (None, None))
        if e != end or _months(s, e) != 3 or ctx not in names:
            continue
        try:
            out.append({"name": names[ctx], "revenue": round(float(v) / 1e7, 2)})
        except ValueError:
            continue
    return sorted(out, key=lambda r: r["revenue"], reverse=True)


def quarters(symbol: str, basis: str = "consolidated", limit: int = 9) -> list[Quarter]:
    """Latest quarters, newest first, falling back to standalone for companies with no consolidated filings."""
    index = result_filings(symbol)
    chosen = [r for r in index if r["basis"] == basis]
    if not chosen:
        chosen = [r for r in index if r["basis"] != basis]
    out = []
    for meta in chosen[:limit]:
        out.append(parse_xbrl(fetch(meta["xbrl"], ttl=None, headers=HEADERS), symbol, meta))
    return out
