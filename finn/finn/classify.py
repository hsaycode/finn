"""Decide what a filing is, and how much it deserves attention.

Written after reading real filing samples. What that showed:
  * NSE's labels are unreliable both ways. "Credit Rating" contains ESG scores and voluntary
    withdrawals; "General Updates" contains completed acquisitions.
  * Titles are often just the label, so material filings get re-scored once the PDF is read
    (see enrich.py). This first pass decides which PDFs are worth opening at all.
  * Importance depends on size, so anything with a rupee figure is judged against the
    company's own revenue. A Rs 2 crore tax order is noise at TCS and news at a small cap.

The rules below say what a filing is. How much that kind of filing matters is measured:
eval/event_study.py compares each type's market reaction with quiet days, and its scores
in calibration.json replace the rules' starting guesses.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

TOP, NOTABLE, ROUTINE = "top", "notable", "routine"
TOP_AT, NOTABLE_AT = 70, 40

CATEGORIES = {
    "results": "Results", "M&A": "M&A", "fund_raise": "Fund raise", "buyback": "Buyback",
    "management_change": "Management", "litigation": "Legal & regulatory", "insider_trading": "Insider trade",
    "earnings_call": "Earnings call", "investor_meetings": "Investor meet", "credit_rating": "Credit rating",
    "dividend": "Dividend", "bonus_split": "Bonus / split", "capex": "Capex", "orders": "Order win",
    "business_update": "Business update", "rumour": "Rumour check", "press_release": "Press release",
    "postal_ballot": "Postal ballot", "agm_egm": "AGM / EGM", "pledging": "Pledging", "unclassified": "Routine",
}


@dataclass
class Verdict:
    category: str
    score: int
    reason: str
    read_pdf: bool = False


def tier(score: int) -> str:
    return TOP if score >= TOP_AT else NOTABLE if score >= NOTABLE_AT else ROUTINE


# Paperwork that's safe to bury on the label alone.
_ROUTINE_LABELS = {
    "Copy of Newspaper Publication": "Newspaper ad copy",
    "Trading Window": "Trading window closure",
    "ESOP/ESOS/ESPS": "ESOP share allotment",
    "Certificate under SEBI (Depositories and Participants) Regulations, 2018": "Compliance certificate",
    "Monitoring Agency Report": "Monitoring report",
    "Trading Plan under PIT": "Insider trading plan",
    "Amendment to AOA/MOA": "Charter amendment",
    "Corrigendum": "Correction",
    "Change in Auditors": "Auditor change",
    "Change in Company Secretary/Compliance Officer": "Company secretary change",
}

_SENIOR = r"\b(ceo|chief executive|cfo|chief financial|managing director|md\b|whole[- ]?time director|executive director|chairman|chairperson|coo|chief operating|ceo designate|group ceo)\b"
_MGMT_LABEL = re.compile(r"appointment|resignation|cessation|retirement|change in (management|director)|demise", re.I)


_MEASURED = Path(__file__).with_name("calibration.json")
_EVIDENCE: dict[str, dict] = {}
QUIET_BIG_MOVE_PCT: float | None = None
if _MEASURED.exists():
    _study = json.loads(_MEASURED.read_text(encoding="utf-8"))
    _EVIDENCE = {t["reason"]: t for t in _study["types"]}
    QUIET_BIG_MOVE_PCT = _study["big_move_days_quiet_pct"]


def classify(raw: str, text: str, title: str = "") -> Verdict:
    v = rules(raw, text, title)
    measured = _EVIDENCE.get(v.reason)
    return Verdict(v.category, measured["score"], v.reason, v.read_pdf) if measured else v


def evidence_for(reason: str, category: str, person: dict | None = None) -> dict | None:
    """How filings like this have moved stocks, for showing beside a card. After a PDF is read the reason gets more
    specific ("CFO leaving"), so this falls back to the type it belongs to."""
    fallback = {"results": "New quarterly results filed", "orders": "Order win: importance depends on its size against revenue", "M&A": "Deal: importance depends on its size against revenue", "capex": "New capacity or capital spending", "dividend": "Dividend declared or recommended"}
    key = reason if reason in _EVIDENCE else None
    if not key and person and person.get("top_executive"):
        key = "A top executive is leaving" if person.get("action") == "leaving" else "A new top executive"
    t = _EVIDENCE.get(key or fallback.get(category, ""))
    if not t or QUIET_BIG_MOVE_PCT is None:
        return None
    return {"big_move_pct": t["big_move_pct"], "quiet_pct": QUIET_BIG_MOVE_PCT, "lift": t["lift"], "n": t["n"], "volume_x": t["median_volume_x"]}


def rules(raw: str, text: str, title: str = "") -> Verdict:
    label = (raw or "").strip()
    blob = f"{title} {text}".lower()

    # NSE's newer combined filing is the results themselves, and the market treats it that way.
    if label.startswith("Integrated Filing- Financial"):
        return Verdict("results", 90, "New quarterly results filed")
    if label in _ROUTINE_LABELS and not _flags_exception(label, blob):
        return Verdict("unclassified", 5, _ROUTINE_LABELS[label])

    # Exchange-prompted checks: the headline itself is the news, and it's in the feed text.
    if label in ("News Verification", "Rumour Verification - Regulation 30(11)") or label.startswith("Rumour"):
        return Verdict("rumour", 66, "Exchange asked the company to respond to a market report", True)
    if label == "Spurt in Volume":
        return Verdict("rumour", 62, "Exchange queried unusual trading volume", True)

    if label in ("Corporate Insolvency Resolution Process",) or re.search(r"insolvency|nclt|nclat", blob):
        return Verdict("litigation", 60, "Insolvency or tribunal proceedings", True)
    if label.startswith("Action(s)") or re.search(r"show cause|penalty|tax demand|regulatory order|\bcbi\b|enforcement directorate|\bfraud\b", blob):
        return Verdict("litigation", 50, "Regulatory or tax action; importance depends on the amount", True)

    if _MGMT_LABEL.search(label) or re.search(r"\b(appointment|resignation|re-appointment|superannuat)", blob):
        return _management(label, blob)

    # Recordings, decks and newspaper copies all mention "financial results" too; they're follow-ups.
    follow_up = re.search(r"\b(recording|audio|video|transcript|presentation|newspaper|advertisement|earnings call|conference call|con-?call|schedule of|intimation of (?:the )?board|scheduled|to consider|will consider)\b", blob)
    if label == "Outcome of Board Meeting" and re.search(r"financial results|unaudited|audited results", blob):
        return Verdict("results", 90, "New quarterly results filed")
    if label == "Outcome of Board Meeting":
        return Verdict("unclassified", 45, "Board meeting outcome: details are in the filing", True)
    if "financial results" in blob and not follow_up and label in ("General Updates", "Updates", "Dividend"):
        return Verdict("results", 90, "New quarterly results filed")

    if label == "Dividend" and "financial results" not in blob:
        return Verdict("dividend", 42, "Dividend declared or recommended")
    if label == "Record Date":
        return Verdict("dividend", 10, "Record date notice")

    if label.startswith("Credit Rating") or "credit rating" in blob:
        return _rating(label, blob)

    if label == "Bagging/Receiving of orders/contracts" or re.search(r"\b(order win|bags? order|secures? .*order|wins? .*order|contract award)", blob):
        return Verdict("orders", 50, "Order win: importance depends on its size against revenue", True)

    if label in ("Acquisition", "Amalgamation/Merger", "Scheme of Arrangement", "Sale or disposal", "Other Restructuring") or re.search(
        r"\b(acquisition|merger|demerger|joint venture|stake sale|wholly owned subsidiary|step-down subsidiary)\b", blob
    ):
        return _deal(label, blob)

    if label in ("Qualified Institutional Placement", "Issue of Securities", "Allotment of Securities", "Options to purchase securities") or re.search(
        r"\b(qip|qualified institution|rights issue|preferential (issue|allotment)|fund ?rais|ncd|non-convertible|commercial paper|buy-?back)\b", blob
    ):
        return _funding(label, blob)

    if re.search(r"\bbonus\b|stock split|sub-division", blob):
        return Verdict("bonus_split", 62, "Bonus issue or stock split")

    if label == "Commencement of commercial production/operations" or re.search(r"capacity expansion|new plant|greenfield|brownfield|capex", blob):
        return Verdict("capex", 58, "New capacity or capital spending", True)

    if label == "Disclosure under SEBI Takeover Regulations":
        if "31(4)" in blob:
            return Verdict("pledging", 8, "Annual promoter statement: no encumbrance change implied")
        if re.search(r"31\(1\)|31\(2\)|encumb|pledg", blob):
            return Verdict("pledging", 68, "Promoter changed the pledge on their shares", True)
        return Verdict("insider_trading", 55, "A large holder crossed a disclosure threshold", True)
    if re.search(r"\bpledg|encumbr", blob):
        return Verdict("pledging", 68, "Promoter changed the pledge on their shares", True)

    if label == "Committee Meeting Updates":
        # Usually routine, but this is also where QIP pricing lands, so the PDF decides.
        return Verdict("unclassified", 15, "Committee meeting", True)
    if label == "Statement of deviation(s) or variation(s) under Reg. 32":
        return Verdict("fund_raise", 25, "Use-of-proceeds statement; matters only if there is a deviation", True)
    if label == "Giving guarantees/indemnity/ becoming a surety for third party":
        return Verdict("litigation", 45, "New guarantee given on behalf of a third party", True)
    if label == "Suspension of Trading":
        return Verdict("litigation", 80, "Trading suspension notice", True)
    if label == "Disclosure of material issue":
        return Verdict("press_release", 60, "Company flagged this as a material issue", True)

    if label == "Analysts/Institutional Investor Meet/Con. Call Updates" or re.search(r"\b(transcript|recording|earnings call|conference call|con-?call|analyst|investor meet|investor conference|presentation on|investor presentation)\b", blob):
        return _call(blob)
    if label == "Investor Presentation":
        return Verdict("earnings_call", 25, "Investor presentation")

    if re.search(r"\bpostal ballot\b", blob):
        return Verdict("postal_ballot", 8, "Postal ballot paperwork")
    if label == "Shareholders meeting" or re.search(r"\b(agm|annual general|egm|extraordinary general)\b", blob):
        return Verdict("agm_egm", 6, "Shareholder meeting paperwork")

    if re.search(r"(sales|production) (in|for) |monthly (sales|update)|quarterly (business )?update|provisional|business update", blob):
        return Verdict("business_update", 52, "Interim business numbers, ahead of or between results", True)

    if label.startswith("Press Release") or "press release" in blob or "media" in blob:
        return Verdict("press_release", 30, _press_kind(blob), True)
    if label in ("Agreements", "Memorandum of Understanding/Agreements", "Product launch"):
        return Verdict("press_release", 38, "New agreement or product", True)

    if label in ("General Updates", "Updates"):
        # These labels carry no signal, so only the PDF can say what this is.
        return Verdict("unclassified", 30, "Unlabelled update: read the filing to place it", True)
    return Verdict("unclassified", 15, label or "Unlabelled filing")


def _press_kind(blob: str) -> str:
    """Press releases range from a large order to a sponsorship. Their measured reaction decides each kind's rank."""
    kinds = (
        ("Press release: an order or contract", r"\b(order|contract|award(ed)? |wins?\b|secures?|bags?\b|l1\b)"),
        ("Press release: a deal or partnership", r"\b(acqui\w+|merger|stake|joint venture|partner\w*|collaborat\w+|alliance|mou\b|tie-?up)"),
        ("Press release: about results", r"\b((financial|quarterly|annual|q[1-4]|half[- ]year\w*|audited|unaudited) results?|results for the|earnings|net profit|q[1-4] fy)\b"),
        ("Press release: capacity or a plant", r"\b(capacity|plant|facility|commission\w*|expansion|factory|inaugurat\w+)"),
        ("Press release: a product launch", r"\b(launch\w*|unveil\w*|introduc\w+|new product|rolls? out)"),
        ("Press release: people or recognition", r"\b(appoint\w*|ceo|cfo|recogni\w+|ranked|csr\b|sustainab\w+|esg\b|donat\w+|felicitat\w+|conferred|honou?r\w*)"),
    )
    return next((name for name, pattern in kinds if re.search(pattern, blob)), "Press release: general")


def _flags_exception(label: str, blob: str) -> bool:
    return label == "Change in Auditors" and bool(re.search(r"resign", blob))


def _management(label: str, blob: str) -> Verdict:
    leaving = re.search(r"resign|cessation|retire|step(ping|s)? down|demise|superannuat", f"{label} {blob}".lower())
    senior = re.search(_SENIOR, blob)
    independent = re.search(r"independent director|non-executive", blob)

    if re.search(r"re-?appointment|extension of tenure|continuation", blob) and not leaving:
        return Verdict("management_change", 14 if independent else 32, "Re-appointment: no change at the top")
    if re.search(r"internal auditor|secretarial auditor|cost auditor|company secretary|compliance officer", blob):
        return Verdict("unclassified", 10, "Auditor or secretarial appointment")
    if senior:
        if leaving:
            return Verdict("management_change", 92, "A top executive is leaving", True)
        return Verdict("management_change", 86, "A new top executive", True)
    if independent:
        if leaving:
            return Verdict("management_change", 48, "An independent director is leaving", True)
        return Verdict("management_change", 34, "Independent director joining the board", True)
    # Bare labels with empty text: the PDF decides who this actually is.
    return Verdict("management_change", 55, "Management change: details are in the filing", True)


def _rating(label: str, blob: str) -> Verdict:
    if "esg" in blob:
        return Verdict("credit_rating", 5, "ESG score, not a credit rating")
    if re.search(r"withdraw", blob):
        return Verdict("credit_rating", 12, "Rating withdrawn", True)
    if re.search(r"downgrad|negative|rating watch", blob):
        return Verdict("credit_rating", 88, "Credit rating cut or under watch", True)
    if re.search(r"upgrad", blob):
        return Verdict("credit_rating", 68, "Credit rating raised", True)
    if re.search(r"reaffirm|re-affirm", blob):
        return Verdict("credit_rating", 18, "Rating unchanged", True)
    if "Revision" in label:
        return Verdict("credit_rating", 62, "Rating revised: direction is in the filing", True)
    return Verdict("credit_rating", 42, "Rating action: direction is in the filing", True)


def _deal(label: str, blob: str) -> Verdict:
    if re.search(r"dissolv|strike off|struck off|incorporation of|incorporated", blob):
        return Verdict("M&A", 12, "Subsidiary housekeeping")
    if re.search(r"merger|demerger|scheme of arrangement|amalgamation", f"{label} {blob}".lower()):
        return Verdict("M&A", 72, "Merger, demerger or scheme", True)
    return Verdict("M&A", 60, "Deal: importance depends on its size against revenue", True)


def _funding(label: str, blob: str) -> Verdict:
    if re.search(r"buy-?back", blob):
        return Verdict("buyback", 82, "Buyback: the company returning cash, not raising it")
    if re.search(r"esop|esps|employee stock", blob):
        return Verdict("unclassified", 5, "ESOP share allotment")
    if re.search(r"\bqip\b|qualified institution|rights issue|preferential", f"{label} {blob}".lower()):
        return Verdict("fund_raise", 80, "Fresh equity raise, which dilutes holders", True)
    if re.search(r"commercial paper", blob):
        return Verdict("fund_raise", 12, "Commercial paper paperwork")
    if re.search(r"\bncd|non-convertible|debenture", blob):
        return Verdict("fund_raise", 32, "Debt raised through debentures", True)
    return Verdict("fund_raise", 40, "Securities issued: details are in the filing", True)


def _call(blob: str) -> Verdict:
    if "transcript" in blob:
        return Verdict("earnings_call", 38, "Earnings call transcript is out")
    if re.search(r"recording|audio|video", blob):
        return Verdict("earnings_call", 6, "Earnings call recording link")
    if re.search(r"presentation", blob):
        return Verdict("earnings_call", 22, "Investor presentation")
    if re.search(r"earnings|results|con-?call|conference call", blob):
        return Verdict("earnings_call", 15, "Earnings call scheduled")
    return Verdict("investor_meetings", 6, "Meeting with investors")


def size_score(amount_crore: float | None, ttm_revenue: float | None) -> tuple[int, str] | None:
    """Translate a rupee figure into importance, relative to the company's size."""
    if not amount_crore or not ttm_revenue:
        return None
    pct = amount_crore / ttm_revenue * 100
    label = f"{pct:.1f}% of annual revenue" if pct >= 0.1 else "under 0.1% of annual revenue"
    if pct >= 5:
        return 86, label
    if pct >= 2:
        return 74, label
    if pct >= 0.5:
        return 55, label
    if pct >= 0.1:
        return 32, label
    return 12, label
