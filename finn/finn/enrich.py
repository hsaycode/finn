"""Open the PDFs that earned it, pull out what a card needs, and re-score on the real facts.

The rules-only path (subject line plus regex) always runs, so a card still has a real title with
no model configured. A cheap model then adds structure when one is available.
"""
from __future__ import annotations

import re

from . import classify as C
from . import docs, llm
from .net import FetchError
from .nse import HEADERS

BOILERPLATE = re.compile(
    r"(disclosure|intimation|submission|outcome|announcement|information)?\s*(under|pursuant to|in terms of|as per|of)?\s*"
    r"(regulation\s*\d+[\w()]*)?.{0,30}?(sebi|securities and exchange board)?.{0,60}?"
    r"(listing obligations|lodr|disclosure requirements).{0,60}?(regulations?,?\s*\d{4})?\)?",
    re.I,
)


def clean_title(text: str, raw_category: str) -> str:
    """Make the feed's line presentable, or return '' when it's only the label again."""
    t = re.sub(r"\s+", " ", text or "").strip(" ,.'\"‘’“”")
    t = re.sub(r"^(?:the\s+)?[\w&.,'()\- ]{2,70}?\s+has\s+(?:informed|submitted(?:\s+to)?)\s+(?:the\s+)?Exchange\s*(?:regarding|about|that|of|for|on)?\s*", "", t, flags=re.I)
    m = re.match(r"a press release dated .*?\btitled\s*[\"“']?\s*(.*)$", t, flags=re.I) or re.match(r"a press release dated [^,]*,?\s*[\"“']?(.*)$", t, flags=re.I)
    if m:
        t = m[1]
    t = re.sub(r"^press release\s*[-–:]\s*", "", t, flags=re.I)
    t = re.sub(r"^(?:titled|press release titled)\s+", "", t, flags=re.I)
    t = re.sub(r"^the exchange has sought clarification from .*? with respect to (?:recent )?news item captioned\s*", "", t, flags=re.I)
    t = t.strip(" ,.'\"‘’“”")
    if not t or t.lower() == (raw_category or "").lower() or len(t) < 4:
        return ""
    if re.fullmatch(r"(general updates?|updates?|disclosure under regulation 30.*|outcome of board meeting.*)", t, flags=re.I):
        return ""
    return t[:1].upper() + t[1:]


def subject_line(text: str) -> str:
    """Nearly every filing is a letter with a 'Sub:' or 'Re:' line: that's the real title."""
    m = re.search(
        r"(?im)^\s*(?:sub(?:ject)?|re)\s*\.?\s*[:\-–]\s*(.+?)(?=\n\s*\n|\n\s*(?:dear|ref\b|sir|madam|pursuant|we |this is|with reference|in terms)|\Z)",
        text[:6000],
        re.S,
    )
    if not m:
        return ""
    s = re.sub(r"\s+", " ", m[1]).strip(" .:-–")
    # 'Disclosure under Regulation 30 of ... Regulations, 2015 - X' is really just X.
    tail = re.split(r"regulations?,?\s*\d{4}\s*\)?\s*(?:\(.*?\)\s*)?[-–:]\s*", s, flags=re.I)
    s = tail[-1] if len(tail) > 1 and len(tail[-1]) > 6 else s
    if re.search(r"listing obligations|lodr", s, re.I) and len(s) < 220 and len(tail) == 1:
        return ""
    return s[:170]


def first_substance(text: str) -> str:
    """The first sentence that actually says something, for filings with an empty subject."""
    m = re.search(
        r"(?:we (?:wish|would like|hereby|are pleased)?\s*(?:to\s+)?(?:inform|intimate|submit|write)[^.]{0,40}?(?:that|about|regarding)\s+|this is to (?:inform|intimate)[^.]{0,30}?that\s+)(.{25,260}?\.)(?:\s|$)",
        re.sub(r"\s+", " ", text[:6000]),
        re.I,
    )
    if not m:
        return ""
    s = m[1].strip()
    return s[:1].upper() + s[1:]


def amount_crore_rule(text: str) -> float | None:
    """Largest explicit rupee figure, as a rules-only stand-in when no model is configured."""
    best = None
    for m in re.finditer(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(crores?|cr\b|lakhs?|lacs?|million|billion)", text[:9000], re.I):
        v = float(m[1].replace(",", ""))
        unit = m[2].lower()
        cr = v if unit.startswith("cr") else v / 100 if unit[0] == "l" else v / 10 if unit == "million" else v * 100
        best = max(best or 0, cr)
    return best


CARD_SYSTEM = (
    "You read one stock-exchange filing from an Indian listed company and extract facts for an investor's "
    "alert card. Use only what the filing states. Do not guess, and do not add context from outside knowledge. "
    "Use null for anything not stated."
)
KINDS = (
    "results M&A fund_raise buyback management_change litigation insider_trading earnings_call investor_meetings "
    "credit_rating dividend bonus_split capex orders business_update rumour press_release postal_ballot agm_egm pledging routine"
).split()
CARD_SCHEMA = """Return JSON with exactly these keys:
{"headline": "what happened, at most 14 words, plain language, no company name, no legal jargon or regulation numbers",
 "kind": the single best fit of: results (financial results or the board outcome approving them, even if other decisions were taken too), M&A (acquisition, merger, demerger, JV, stake sale), fund_raise (raising equity or debt), buyback, management_change, litigation (lawsuits, tax or regulatory orders, insolvency), insider_trading, earnings_call, investor_meetings, credit_rating (a credit rating agency's action only, never an ESG score), dividend, bonus_split, capex (new plant or capacity), orders (order or contract wins), business_update (interim numbers such as monthly sales or quarterly provisional data), rumour (reply to a news or rumour query), press_release (other company news, product launches, partnerships), postal_ballot, agm_egm, pledging, routine (compliance paperwork, statutory auditor appointments, no-deviation statements, internal housekeeping),
 "tone": "positive", "negative" or "neutral": is this fact on its face good or bad for shareholders? Use neutral when unclear. Never predict the share price,
 "amount_crore": the rupee size in Rs crore of the transaction or dispute THIS filing announces (order value, deal price, tax demand or penalty, funds raised, capex approved), or null. Never balance-sheet totals, loan books, cumulative historic investment, profit figures or a ceiling on bank facilities that are merely being rated. Convert from lakh or million. If only a size band is given such as 'Major: Rs 5,000-10,000 crore', use its lower bound,
 "amount_note": "very short description of amount_crore, e.g. 'tax demand plus penalty' or 'lower end of Major band'", or null,
 "funding_type": "equity" if new shares are sold (QIP, rights, preferential), "debt" if bonds, NCDs or loans, else null,
 "person": null, or {"name": str, "role": str, "action": "joining"|"leaving"|"reappointed"|"role_change", "top_executive": true only for CEO, MD, deputy MD, CFO, COO, chairperson or whole-time director, "independent_director": bool, "effective": str or null, "reason": stated reason for leaving or null, "profile": up to 3 short strings on background taken from the filing's profile, [] if none},
 "rating": null, or {"agency": str, "action": "upgraded"|"downgraded"|"reaffirmed"|"assigned"|"withdrawn"|"outlook_changed", "rating": str, "outlook": str or null},
 "legal": null, or {"authority": str, "nature": str, "company_view": "what the company says about impact, in under 12 words" or null},
 "deviation": for a use-of-proceeds statement, true if money was used differently from what was promised, false if not; else null,
 "auditor_resigned": true only if a statutory auditor quit before its term ended, else false,
 "response": for a rumour or news verification, "what the company says in reply, in under 15 words", else null}"""


def extract_card(pdf_text: str) -> dict | None:
    # The two sizes hold answers from paid and free models. Either one saves reading the filing again.
    for size in (9000, 5000):
        hit = llm.cached("card_v3", llm.CHEAP, CARD_SYSTEM, f"{CARD_SCHEMA}\n\n<source>\n{pdf_text[:size]}\n</source>")
        if isinstance(hit, dict):
            return hit
    prompt = f"{CARD_SCHEMA}\n\n<source>\n{pdf_text[:llm.source_chars(9000, 5000)]}\n</source>"
    try:
        out = llm.ask_json("card_v3", llm.CHEAP, CARD_SYSTEM, prompt, max_tokens=900)
    except (llm.NoModel, llm.BudgetExceeded):
        return None
    except (ValueError, RuntimeError):
        return None
    return out if isinstance(out, dict) else None


# Only these announce a transaction whose size can be compared with revenue.
SIZED = {"orders": "Order worth", "M&A": "Deal worth", "litigation": "Amount at stake is", "fund_raise": "Raise equal to", "capex": "Spend equal to", "buyback": "Buyback equal to"}
_ON_RECLASSIFY = {
    "press_release": (32, "Company news"), "business_update": (52, "Interim business numbers, ahead of or between results"),
    "dividend": (42, "Dividend declared or recommended"), "earnings_call": (22, "Earnings call material"),
    "investor_meetings": (6, "Meeting with investors"), "postal_ballot": (8, "Postal ballot paperwork"), "agm_egm": (6, "Shareholder meeting paperwork"),
    "M&A": (60, "Deal: size not stated"), "orders": (50, "Order win: size not stated"), "capex": (58, "New capacity or capital spending"),
    "fund_raise": (40, "Securities issued"), "litigation": (45, "Legal or regulatory action: no amount stated"),
    "buyback": (82, "Buyback: the company returning cash, not raising it"), "bonus_split": (62, "Bonus issue or stock split"),
    "rumour": (66, "Company responding to a market report"), "pledging": (68, "Promoter changed the pledge on their shares"),
    "insider_trading": (55, "Large holder or insider transaction"), "management_change": (42, "Management change"),
    "credit_rating": (42, "Rating action"),
}


# A free model said a chairman and a new CFO weren't top executives. Who holds the job isn't a matter of opinion.
_TOP_ROLE = re.compile(r"\b(chief executive|ceo|(deputy |joint )?managing director|chief financial|cfo|chief operating|coo|chair(man|person|woman)|whole[- ]time director)\b", re.I)


def _names_top_role(*texts: str) -> bool:
    return any(_TOP_ROLE.search(t or "") and not re.search(r"committee|subsidiar", t, re.I) for t in texts)


def rescore(v: C.Verdict, card: dict | None, amount: float | None, ttm_revenue: float | None, *, trust_amount: bool = True, source_text: str = "results") -> tuple[str, int, str]:
    """Re-judge with the filing's actual content. Returns (category, score, reason)."""
    category, score, reason = v.category, v.score, v.reason
    card = card or {}
    kind = card.get("kind")
    # Answering the exchange about a market report ends a story the investor saw begin, so it can't be paperwork.
    # A free model called Sun Pharma's reply routine, which left its card saying the response was still awaited.
    if v.category == "rumour" and kind == "routine":
        kind = "rumour"
    if kind in KINDS:
        category = "unclassified" if kind == "routine" else kind
    # A board outcome approving results usually declares a dividend too. Results are the news.
    if category == "dividend" and v.category == "results" and re.search(r"financial results|results for the", source_text, re.I):
        return "results", 90, "New quarterly results filed"

    if kind == "routine":
        return category, min(score, 15), reason if score <= 15 else "Routine paperwork"
    if card.get("auditor_resigned"):
        return "litigation", 84, "Statutory auditor quit before its term ended"
    if card.get("deviation") is True:
        return "fund_raise", 82, "Money raised was used differently from what was promised"
    if card.get("deviation") is False:
        return "fund_raise", 10, "Raised funds used as promised"

    headline = (card.get("headline") or "").lower()
    # A model calling something "results" isn't enough. Reliance's Jio Platforms listing was filed
    # under that, which ranked it wrongly and folded it into the results story.
    # Its text also mentioned a meeting "scheduled" to consider results, so the headline is the test.
    if category == "results" and not re.search(r"result|quarter|half[- ]year|profit|revenue|earnings|financial", headline or source_text.lower()):
        return "press_release", 45, "Company news"
    if category == "results":
        # A notice that the board *will* consider results isn't the results.
        if re.search(r"\b(to meet|will meet|to consider|will consider|scheduled|intimation of)\b", headline):
            return category, 20, "Results date announced"
        return category, 90, "New quarterly results filed"
    if re.search(r"\breminder\b|call money", headline):
        return category, 12, "Reminder to shareholders"
    if v.category != category and category in _ON_RECLASSIFY:
        score, reason = _ON_RECLASSIFY[category]

    person = card.get("person")
    if isinstance(person, dict) and person.get("name") and category in ("management_change", "unclassified"):
        category = "management_change"
        who = person.get("role") or "Executive"
        action = person.get("action")
        if action == "reappointed":
            score, reason = (14 if person.get("independent_director") else 32), f"{who} re-appointed: no change at the top"
        elif person.get("top_executive") or _names_top_role(who, source_text):
            # Top tier because the brief names these as material. Their measured reaction is modest, so results outrank them.
            score, reason = (74 if action == "leaving" else 72), f"{who} {'leaving' if action == 'leaving' else 'appointed'}"
        elif person.get("independent_director"):
            score, reason = (48 if action == "leaving" else 34), f"Independent director {'leaving' if action == 'leaving' else 'joining'}"
        else:
            score, reason = (42 if action in ("joining", "leaving") else 30), f"Senior management change: {who}"

    rating = card.get("rating")
    if isinstance(rating, dict) and rating.get("action") and category == "credit_rating":
        score, reason = {
            "downgraded": (72, "Credit rating cut"), "upgraded": (40, "Credit rating raised"),
            "outlook_changed": (56, "Rating outlook changed"), "assigned": (34, "New rating assigned"),
            "reaffirmed": (16, "Rating unchanged"), "withdrawn": (12, "Rating withdrawn"),
        }.get(rating["action"], (score, reason))

    sized = C.size_score(amount, ttm_revenue) if (trust_amount and category in SIZED) else None
    if sized:
        s, label = sized
        floor = {"M&A": 30, "fund_raise": 30, "capex": 30}.get(category, 0)
        funding = card.get("funding_type")
        pct = amount / ttm_revenue * 100
        if category == "fund_raise" and funding == "equity":
            s = max(s, 55)  # new shares dilute holders, whatever the size, though the measured reaction is modest
        elif category == "fund_raise" and funding == "debt":
            s = 80 if pct >= 25 else min(s, 62)  # routine for large companies until it's huge
        score = max(s, floor)
        reason = f"{SIZED[category]} {label}"
    # Size is the wrong test here: being admitted to insolvency matters whatever the sum.
    legal = card.get("legal") if isinstance(card.get("legal"), dict) else {}
    blob = f"{headline} {legal.get('nature') or ''}".lower()
    if category == "litigation" and re.search(r"insolvency|\bcirp\b|\bnclt\b|winding up", blob) and re.search(r"admit", blob):
        score, reason = 90, "Insolvency proceedings admitted: serious at any amount"
    elif category == "fund_raise" and card.get("funding_type") == "equity":
        if "subsidiary" in headline:
            score, reason = 66, "A subsidiary is selling shares"
        else:
            # A large raise keeps the rank its size earned. An unsized one takes the measured floor.
            reason = reason if sized else "Fresh equity raise, which dilutes holders"
            score = max(score if sized else 0, 55)

    return category, score, reason


def numbers_in_source(headline: str, source: str) -> bool:
    """Every figure in a model-written headline must be printed in the filing it came from."""
    flat = source.replace(",", "")
    for n in re.findall(r"\d+(?:\.\d+)?", headline.replace(",", "")):
        if not re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", flat):
            return False
    return True


_PROPER = re.compile(r"\b[A-Z][a-zA-Z&.]{2,}\b")
_COMMON = {"The", "And", "For", "With", "Former", "Director", "Executive", "Chief", "Officer", "Managing", "Senior", "Vice", "President", "Head", "Group", "Limited", "Ltd", "Years", "Over", "Since", "From", "Until", "Member", "Independent", "Non", "Chairman", "Chairperson", "General", "Manager", "Board", "Company", "Master", "Bachelor", "Degree", "Business"}


def grounded(text: str, source: str) -> bool:
    """Figures and proper names in a model's line must appear in what it was given."""
    if not numbers_in_source(text, source):
        return False
    low = source.lower()
    return all(w.strip(".").lower() in low for w in _PROPER.findall(text) if w.strip(".") not in _COMMON)


def _keep_only_sourced(card: dict, source: str) -> dict:
    """Whatever a model wrote about a filing has to be supported by that filing, or it isn't shown."""
    out = dict(card)
    for key in ("response", "amount_note"):
        if out.get(key) and not numbers_in_source(str(out[key]), source):
            out[key] = None
    if isinstance(out.get("legal"), dict) and out["legal"].get("company_view") and not numbers_in_source(out["legal"]["company_view"], source):
        out["legal"] = {**out["legal"], "company_view": None}
    if isinstance(out.get("person"), dict):
        person = dict(out["person"])
        person["profile"] = [x for x in person.get("profile") or [] if isinstance(x, str) and grounded(x, source)]
        if person.get("reason") and not grounded(str(person["reason"]), source):
            person["reason"] = None
        out["person"] = person
    return out


def enrich(row: dict, ttm_revenue: float | None) -> dict:
    """Returns the updated fields for one filing."""
    v = C.classify(row["raw_category"], row["text"] or "")
    title = clean_title(row["text"] or "", row["raw_category"])
    detail: dict = {}
    pdf_text = ""
    if v.read_pdf and row.get("pdf_url"):
        try:
            d = docs.load(row["pdf_url"], headers=HEADERS, max_pages=6)
            pdf_text = d.text
            detail["pages"] = len(d.pages)
            if d.scanned:
                detail["scanned"] = True
        except (FetchError, RuntimeError, ValueError):
            detail["pdf_error"] = True

    subject = subject_line(pdf_text) if pdf_text else ""
    if subject:
        # The real subject can change the classification, e.g. a "Credit Rating" that's an ESG score.
        v = C.classify(row["raw_category"], row["text"] or "", subject)
    card = extract_card(pdf_text) if pdf_text and not detail.get("scanned") else None
    if card:
        card = _keep_only_sourced(card, pdf_text)
    # A regex can't tell an order's value from a loan book, so without a model the figure
    # is shown as a hint but never allowed to move a filing's rank.
    rule_only = card is None
    if rule_only:
        amount = amount_crore_rule(pdf_text) if pdf_text else None
    else:
        amount = card.get("amount_crore")
        amount = float(amount) if isinstance(amount, (int, float)) and amount > 0 else None

    category, score, reason = rescore(v, card, amount, ttm_revenue, trust_amount=not rule_only, source_text=f"{row.get('text') or ''} {subject}")
    model_headline = (card or {}).get("headline")
    if model_headline and not numbers_in_source(model_headline, pdf_text):
        model_headline = None
    headline = model_headline or title or subject or first_substance(pdf_text) or C.CATEGORIES.get(category, row["raw_category"])
    if card:
        for key in ("person", "rating", "legal", "response", "amount_note", "tone", "funding_type"):
            if card.get(key):
                detail[key] = card[key]
    if amount:
        detail["amount_crore"] = amount
    detail["read_by"] = "model" if card else ("rules" if pdf_text else "feed")
    return {
        "category": category, "score": score, "tier": C.tier(score), "title": headline[:180],
        "reason": reason, "detail": detail, "enriched": 2 if card else 1,
    }
