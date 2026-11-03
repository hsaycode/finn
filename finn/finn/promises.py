"""What management promised, against what it delivered.

Division of labour:
  cheap model  picks forward-looking commitments out of pre-filtered sentences, by id
  code         checks every number against the cited sentence, and scores financial
               promises against the XBRL results
  strong model judges only non-financial promises (a plant, a launch), from later calls, by id
"""
from __future__ import annotations

import re
from datetime import date

from . import llm
from . import transcripts as T
from .nse import Quarter

DELIVERED, MISSED, PARTLY, NOT_DUE, VAGUE, NO_EVIDENCE = "delivered", "missed", "partly", "not_due", "vague", "no_evidence"
METRICS = ["revenue_growth_yoy", "revenue_growth_qoq", "revenue", "ebitda_margin", "ebit_margin", "pat_margin", "ebitda", "pat", "other"]

EXTRACT_SYSTEM = (
    "You analyse earnings-call transcripts for an investor checking whether management keeps its word. "
    "You see numbered sentences spoken by management. Identify explicit forward-looking commitments: what they said "
    "WILL happen. Ignore history, results already reported, industry commentary, government policy assumptions and "
    "anything said by an analyst. Work only from the sentences given; never use outside knowledge."
)
EXTRACT_FORMAT = """Return a JSON array, possibly empty. Each element:
{"ids": ["sentence ids that state the commitment, 1 to 3 of them"],
 "claim": "the commitment in under 16 plain words, naming what it's about. A sentence can lean on the text just before it: 'should hit 7,000 crores' after a line on exports is an exports target, not revenue",
 "scope": "company" if the figure is for the whole company; "segment" if it is for one business line, product, subsidiary or geography,
 "material": true if an investor would care: company revenue, profit, margin, capex, debt, working capital, cash flow or a major plant or business. false for minor operational detail,
 "kind": "financial" if it names a figure for revenue, growth, margin or profit; "operational" for a dated milestone or physical target such as a plant opening, capacity, volumes, capex or debt; "vague" for direction with no testable figure or date (e.g. 'margins will improve'),
 "metric": one of revenue_growth_yoy, revenue_growth_qoq, revenue, ebitda_margin, ebit_margin, pat_margin, ebitda, pat, other. Use other for anything else, and always use other when scope is segment. A margin counts as ebitda, ebit or pat only if the words say so: contribution, gross or unnamed margin is other,
 "low": lower bound of the figure as a number, or null. Use null for relative targets such as '2x market growth',
 "high": upper bound as a number, or null. For a single figure set low and high equal,
 "unit": "%", "crore" or "other", or null,
 "period": when it falls due, written exactly like "Q3 FY27", "H2 FY27", "FY27", "FY27 exit" or "FY29". Resolve relative dates such as 'next quarter' or 'this fiscal' using the call's quarter given above. null if no timeframe, or if 'this year' can't mean the fiscal year because the rest of the sentence runs past its end, such as 'over the next few quarters' with one quarter left,
 "basis": "constant currency", "organic" or another stated qualifier, or null}
Merge sentences that state one commitment. Prefer precision: leave out anything you're unsure is a commitment.
Text marked "just before" is context only: never cite it as a commitment and don't give its id."""

JUDGE_SYSTEM = (
    "You check whether a company did what its management said it would, using only what management said on later earnings "
    "calls, shown as numbered sentences. Do not use outside knowledge. Be strict: a promise is delivered only if a later "
    "sentence directly says it happened; a restatement of the plan isn't delivery, and neither is something you can only infer "
    "from a related figure. If the sentences don't settle it directly, the verdict is no_evidence. "
    "Beating the target in the good direction is delivered. Use partly only when the result came late or short in the bad direction. "
    "If a statement was only a forecast of something outside management's control, the verdict is no_evidence. "
    "Check the subject first: a later figure or plant counts only if it's the same business line, product, plant or joint venture as "
    "the promise. Text marked 'just before' shows what a sentence is about. A telecom figure says nothing about an exports target."
)
JUDGE_FORMAT = """Return a JSON array with one element per promise:
{"key": the promise's key,
 "verdict": "delivered", "partly" (done late or short of the promise), "missed" (later sentences show it did not happen by the due date), "on_track" (due date not reached and later calls say it's progressing), or "no_evidence",
 "evidence": ["ids of the sentences that show this, at most 2, most direct first"],
 "note": "under 16 plain words explaining the verdict"}"""


def _in_batches(lines: list[str], separator: str = "\n") -> list[str]:
    """One request normally. On the free tier a large one is rejected outright, so the work is split."""
    limit = llm.source_chars(10**9)
    batches, current, size = [], [], 0
    for line in lines:
        if current and size + len(line) > limit:
            batches.append(separator.join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + len(separator)
    if current:
        batches.append(separator.join(current))
    return batches


def _in_context(call: T.Call, s: T.Sentence, terms: list[str]) -> str:
    before, _ = call.nearby(s, width=120 if llm.frugal() else 220)
    text = T.mask(s.text, terms)
    return f'(just before: "{T.mask(before, terms)}") {text}' if before else text


_MARGIN_WORDS = {
    "ebitda_margin": r"\b(ebitda|pbdit|operating (profit )?margin)",
    "ebit_margin": r"\b(ebit|pbit|operating (profit )?margin)\b",
    "pat_margin": r"\b(pat|net (profit )?margin|profit margin)\b",
}


def _margin_named(metric: str, call: T.Call, first: T.Sentence, quote: str) -> bool:
    """Code scores margins against filed EBITDA, so an unnamed 23% must not be scored as if it were EBITDA.
    Suzlon's 22-23% was a contribution margin, and it read as a miss against 18% EBITDA."""
    if metric not in _MARGIN_WORDS:
        return True
    window = f"{call.nearby(first, before=3, width=400)[0]} {quote}".lower()
    return bool(re.search(_MARGIN_WORDS[metric], window)) and not re.search(r"\b(contribution|gross) margin", quote.lower())


_FEW_QUARTERS = re.compile(r"\bnext (few|couple of|two|three|four|[2-9]) quarters\b", re.I)


def _period_fits(period: str | None, quote: str, said_end: str) -> bool:
    """'Over the next few quarters' can't fall due at the end of the fiscal year that has one quarter left.
    Reliance's 'current year' meant 2026, and read as FY26 it became a false miss."""
    due = parse_period(period)
    if not due or not said_end:
        return True
    # A promise can't fall due in a quarter already over when it was made. On a Q4 call, "current fiscal" is the next one.
    if due["end"] <= said_end:
        return False
    if not _FEW_QUARTERS.search(quote):
        return True
    a, b = date.fromisoformat(said_end), date.fromisoformat(due["end"])
    return (b.year - a.year) * 12 + b.month - a.month > 3


def _figures(text: str) -> set[str]:
    """The numbers in a sentence, leaving out quarter and year labels, which notes restate in their own format."""
    text = re.sub(r"\b(Q[1-4]|H[12])\s*(FY)?\s*'?\d*|\bFY\s*'?\d+|\b20\d\d\b", " ", text, flags=re.I)
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", text) if len(n.replace(",", "").replace(".", "")) >= 2}


def _note_is_sourced(note: str, *sources: str) -> bool:
    """A figure in the explanation that isn't in anything shown beside it can't be checked by the reader."""
    shown = _figures(" ".join(sources))
    return _figures(note) <= shown


def parse_period(p: str | None) -> dict | None:
    """Due date of a period string. 'exit' promises are tested on the final quarter alone."""
    if not p:
        return None
    s = p.upper().replace("'", "").strip()
    m = re.search(r"Q([1-4])\s*FY\s*(\d{2,4})", s)
    if m:
        return {"end": T.fy_quarter_end(int(m[1]), int(m[2]) % 100), "scope": "quarter"}
    m = re.search(r"H([12])\s*FY\s*(\d{2,4})", s)
    if m:
        fy = int(m[2]) % 100
        return {"end": T.fy_quarter_end(2 if m[1] == "1" else 4, fy), "scope": "half"}
    m = re.search(r"FY\s*(\d{2,4})", s)
    if m:
        return {"end": T.fy_quarter_end(4, int(m[1]) % 100), "scope": "exit" if "EXIT" in s else "year"}
    return None


def extract(call: T.Call, mask_terms: list[str]) -> list[dict]:
    """Forward-looking commitments from one call. Every number is verified against its source."""
    cands = T.candidates(call)
    if not cands:
        return []
    out = []
    for lines in _in_batches([f"[{s.id}] {_in_context(call, s, mask_terms)}" for s in cands]):
        prompt = f"This call discusses {call.label} (India's fiscal year ends in March).\n\n{EXTRACT_FORMAT}\n\n<source>\n{lines}\n</source>"
        part = llm.ask_json("promise_extract_v3", llm.CHEAP, EXTRACT_SYSTEM, prompt, max_tokens=5000)
        out += part if isinstance(part, list) else []
    by_id = call.by_id()
    promises = []
    for item in out if isinstance(out, list) else []:
        if not isinstance(item, dict):
            continue
        sents = [by_id[i] for i in item.get("ids") or [] if i in by_id]
        sents = [s for s in sents if s.role != "analyst"]
        if not sents or not item.get("claim"):
            continue
        low, high = _num(item.get("low")), _num(item.get("high"))
        cited = T.quote_from(sents)
        quote_text = cited["quote"]
        # A figure in the summary that isn't in what was said is the model's, not management's.
        if not _note_is_sourced(item["claim"], quote_text, *call.nearby(sents[0])):
            continue
        # A figure that isn't in the cited sentence is the model's invention. Drop the figure, keep it honest.
        if low is not None and not _figure_in(low, quote_text):
            low, high = None, None
        if high is not None and not _figure_in(high, quote_text):
            high = low
        kind = item.get("kind") if item.get("kind") in ("financial", "operational", "vague") else "vague"
        if kind == "financial" and low is None:
            # e.g. '$1 billion by FY28': still a dated commitment, just not one code can score.
            kind = "operational" if item.get("period") else "vague"
        # The timeline can sit in the next sentence: "this year we'll commission it... in the next few quarters".
        around = " ".join(call.nearby(sents[0], before=1, after=1))
        period = item.get("period") if _period_fits(item.get("period"), f"{around} {quote_text}", call.period_end) else None
        scope = "segment" if item.get("scope") == "segment" else "company"
        metric = item.get("metric") if item.get("metric") in METRICS else "other"
        if not _margin_named(metric, call, sents[0], quote_text):
            metric = "other"
        promises.append({
            "call": call.index, "call_label": call.label, "said_period": call.period_end,
            "claim": item["claim"], "kind": kind,
            "metric": "other" if scope == "segment" else metric, "scope": scope, "material": item.get("material") is not False,
            "low": low, "high": high if high is not None else low, "unit": item.get("unit"),
            "period": period, "basis": item.get("basis"),
            **cited, "url": call.url,
            "ids": [s.id for s in sents], "_before": call.nearby(sents[0])[0],
        })
    return promises


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
        return float(m[0]) if m else None
    return None


def _figure_in(v: float, text: str) -> bool:
    t = text.replace(",", "")
    forms = {f"{v:g}", f"{v:.1f}", f"{int(v)}" if v == int(v) else f"{v:g}"}
    return any(re.search(rf"(?<![\d.]){re.escape(f)}(?![\d])", t) for f in forms)


def actual(p: dict, quarters: dict[str, Quarter]) -> dict | None:
    """Compute the delivered figure from the filed results. Returns None when it can't be tested."""
    due = parse_period(p.get("period"))
    if not due:
        return None
    end, scope = due["end"], due["scope"]
    metric = p["metric"]
    if metric == "other" or end not in quarters:
        return None

    def span(n: int, end_: str) -> list[Quarter] | None:
        y, m = int(end_[:4]), int(end_[5:7])
        out = []
        for _ in range(n):
            key = f"{y}-{m:02d}-{31 if m in (3, 12) else 30}"
            if key not in quarters:
                return None
            out.append(quarters[key])
            m -= 3
            if m <= 0:
                m += 12
                y -= 1
        return out

    n = {"quarter": 1, "exit": 1, "half": 2, "year": 4}[scope]
    cur = span(n, end)
    if not cur:
        return None
    rev = sum(q.revenue or 0 for q in cur)
    label = {"quarter": T.quarter_label(end), "exit": f"{T.quarter_label(end)} (exit)", "half": f"H{'1' if end[5:7]=='09' else '2'} {T.quarter_label(end)[3:]}", "year": T.quarter_label(end)[3:]}[scope]

    def pct(x):
        return round(x / rev * 100, 1) if rev else None

    if metric == "revenue_growth_yoy":
        y, m = int(end[:4]) - 1, end[5:]
        prior = span(n, f"{y}-{m}")
        if not prior:
            return None
        base = sum(q.revenue or 0 for q in prior)
        return {"value": round((rev / base - 1) * 100, 1), "unit": "%", "label": label} if base else None
    if metric == "revenue_growth_qoq":
        q = span(2, end)
        return {"value": round((q[0].revenue / q[1].revenue - 1) * 100, 1), "unit": "%", "label": label} if q and q[1].revenue else None
    if metric == "revenue":
        return {"value": round(rev, 1), "unit": "crore", "label": label}
    if any(q.format == "banking" for q in cur) and metric in ("ebitda_margin", "ebit_margin", "ebitda"):
        return None  # EBITDA isn't a bank metric
    if metric == "ebitda_margin":
        return {"value": pct(sum(q.ebitda or 0 for q in cur)), "unit": "%", "label": label}
    if metric == "ebit_margin":
        return {"value": pct(sum((q.ebitda or 0) - (q.depreciation or 0) for q in cur)), "unit": "%", "label": label}
    if metric == "pat_margin":
        return {"value": pct(sum(q.pat or 0 for q in cur)), "unit": "%", "label": label}
    if metric == "ebitda":
        return {"value": round(sum(q.ebitda or 0 for q in cur), 1), "unit": "crore", "label": label}
    if metric == "pat":
        return {"value": round(sum(q.pat or 0 for q in cur), 1), "unit": "crore", "label": label}
    return None


def _scope_doubt(p: dict, a: dict) -> bool:
    """A percent target wildly off the company-wide figure was probably about a narrower measure."""
    if a.get("unit") != "%" or a.get("value") is None or p.get("low") is None:
        return False
    growth = p["metric"].startswith("revenue_growth")
    high = p["high"] if p.get("high") is not None else p["low"]
    above, below = a["value"] - high, p["low"] - a["value"]
    return above > (10 if growth else 1.5) or below > (15 if growth else 5)


def score_financial(p: dict, a: dict) -> dict:
    """Code, not a model, decides this. Tolerance absorbs rounding in what was said aloud."""
    low = p["low"]
    tol = 0.3 if a["unit"] == "%" else abs(low) * 0.01
    v = a["value"]
    if v is None:
        return {"verdict": NO_EVIDENCE, "note": "Couldn't compute this from the filed results"}
    gap = round(v - low, 1)
    shown = f"{v:g}%" if a["unit"] == "%" else f"₹{v:,.0f} cr"
    if v >= low - tol:
        return {"verdict": DELIVERED, "actual": shown, "note": f"Filed results show {shown} for {a['label']}"}
    short = f"{abs(gap):g} pts" if a["unit"] == "%" else f"₹{abs(gap):,.0f} cr"
    near = (a["unit"] == "%" and abs(gap) <= 1.0) or (a["unit"] != "%" and abs(gap) <= abs(low) * 0.05)
    return {"verdict": PARTLY if near else MISSED, "actual": shown, "note": f"Filed results show {shown} for {a['label']}, {short} short"}


_STOP = set("the a an of to and in on for by we our will be is are at with from that this as it its it's next year quarter fy company have has been would should expect expected target".split())


def _terms(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9\-]{2,}", s.lower()) if w not in _STOP}


def evidence_for(p: dict, later_calls: list[T.Call], limit: int = 6) -> list[T.Sentence]:
    """Sentences from later calls that discuss the same thing. The model only sees these."""
    terms = _terms(p["claim"]) | _terms(p["quote"])
    scored = []
    for call in later_calls:
        for s in call.mgmt():
            overlap = len(terms & _terms(s.text))
            if overlap >= 3:
                scored.append((overlap, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:limit]]


def judge_operational(promises: list[dict], calls: list[T.Call], latest_period: str, mask_terms: list[str]) -> None:
    """Strong model, on a tiny evidence pack. Mutates promises with a verdict."""
    today_label = T.quarter_label(latest_period) if latest_period else "the latest quarter"
    work, seen = [], {}
    for i, p in enumerate(promises):
        later = [c for c in calls if c.index < p["call"]]
        ev = evidence_for(p, later, limit=3 if llm.frugal() else 6)
        p["_key"] = f"p{i}"
        if ev:
            work.append((p, ev))
            seen.update({s.id: s for s in ev})
        else:
            p.update(_no_later(p, latest_period))
    if not work:
        return
    # On the free tier these go a few at a time. Each promise's evidence travels with it.
    limit = llm.source_chars(10**9)
    groups, current, size = [], [], 0
    for item in work:
        cost = 700 + sum(len(s.text) + 150 for s in item[1])
        if current and size + cost > limit:
            groups.append(current)
            current, size = [], 0
        current.append(item)
        size += cost
    groups.append(current)
    for group in groups:
        _judge_group(group, calls, today_label, latest_period, mask_terms)


def _judge_group(work: list, calls: list[T.Call], today_label: str, latest_period: str, mask_terms: list[str]) -> None:
    seen = {s.id: s for _, ev in work for s in ev}
    blocks = []
    for p, ev in work:
        said = T.mask(p["quote"], mask_terms)[:300]
        before = T.mask(p.get("_before") or "", mask_terms)
        blocks.append(f'{p["_key"]}: said on the {p["call_label"]} call: "{T.mask(p["claim"], mask_terms)}"; due: {p.get("period") or "unstated"}\nwords used: (just before: "{before}") {said}\nevidence ids: {", ".join(s.id for s in ev)}')
    by_index = {c.index: c for c in calls}
    lines = "\n".join(f"[{s.id}] ({quarter_of(calls, s.id)} call) {_in_context(by_index[int(s.id[1:].split('.')[0])], s, mask_terms)}" for s in seen.values())
    prompt = f"The latest reported quarter is {today_label}.\n\n{JUDGE_FORMAT}\n\nPROMISES\n" + "\n\n".join(blocks) + f"\n\n<source>\n{lines}\n</source>"
    try:
        out = llm.ask_json("promise_judge_v4", llm.STRONG, JUDGE_SYSTEM, prompt, max_tokens=6000)
    except (llm.NoModel, llm.BudgetExceeded, ValueError):
        for p, _ in work:
            p.update({"verdict": NO_EVIDENCE, "note": "Needs a model to judge. Showing the promise only."})
        return
    results = {r.get("key"): r for r in out if isinstance(r, dict)} if isinstance(out, list) else {}
    for p, ev in work:
        r = results.get(p["_key"])
        if not r:
            p.update(_no_later(p, latest_period))
            continue
        verdict = {"on_track": NOT_DUE}.get(r.get("verdict"), r.get("verdict"))
        if verdict not in (DELIVERED, PARTLY, MISSED, NOT_DUE, NO_EVIDENCE):
            verdict = NO_EVIDENCE
        evid = [seen[i] for i in r.get("evidence") or [] if i in seen]
        # A verdict with no surviving source sentence isn't one we can show.
        if verdict in (DELIVERED, PARTLY, MISSED) and not evid:
            verdict = NO_EVIDENCE
        # One quarter's progress can't settle a promise that isn't due yet. An announced slip still can.
        due = parse_period(p.get("period"))
        if verdict in (DELIVERED, PARTLY) and due and latest_period and due["end"] > latest_period:
            verdict = NOT_DUE
        verdict = _consistent(verdict, p["claim"], r.get("note") or "")
        # With no due date there's nothing to have missed yet.
        if verdict in (MISSED, PARTLY) and not p.get("period"):
            verdict = NO_EVIDENCE
        # The judging rules are written for the model, and now and then it quotes them back.
        note = re.sub(r"[;,]?\s*this is not a bad-direction[^.]*", "", r.get("note") or "", flags=re.I).strip()
        cited = None
        if evid:
            call = next(c for c in calls if c.index == int(evid[0].id[1:].split(".")[0]))
            cited = {**T.quote_from(evid), "url": call.url, "call_label": call.label, "ids": [s.id for s in evid]}
        if verdict in (DELIVERED, PARTLY, MISSED) and not _note_is_sourced(note, cited["quote"], p["claim"], p["quote"]):
            verdict, note = NO_EVIDENCE, "The sentences shown don't contain the figures this would need."
        p["verdict"] = verdict
        p["note"] = note
        # "Fully operational" isn't delivered by "operational and ramping up". The qualifier was the promise.
        if verdict == DELIVERED and cited and not _qualifier_met(p["claim"], cited["quote"]):
            verdict, note = NO_EVIDENCE, f"The later call says less than was promised: {note}"
            p["note"] = note
        if verdict == DELIVERED and cited and _only_a_plan(cited["quote"]):
            verdict = NO_EVIDENCE
            p["note"] = "The later call restates the plan and doesn't say it happened."
        p["verdict"] = verdict
        if cited:
            p["evidence"] = cited


REVIEW_SYSTEM = (
    "You review another analyst's verdicts on whether a company kept a promise. Try to overturn each one. Using only the text shown, ask: "
    "what exactly was promised, reading the words around it? Is the evidence about that same business line, product, plant or joint venture, again "
    "reading the words around it? Does it directly show this outcome by the due date, and does that due date fit how it was actually said? "
    "Overturn it if the evidence is about something else, only implies the outcome, contradicts the verdict, or if the verdict's own explanation admits "
    "it isn't confirmed. Being overturned is not a criticism: an honest 'can't tell' is better than a wrong verdict. Don't use outside knowledge."
)


def _with_surroundings(calls: list[T.Call], ids: list[str], quote: str, terms: list[str]) -> str:
    call = next((c for c in calls if ids and c.index == int(ids[0][1:].split(".")[0])), None)
    first = call and call.by_id().get(ids[0])
    if not first:
        return T.mask(quote, terms)[:600]
    before, after = call.nearby(first, before=2, after=1)
    return f'[before: {T.mask(before, terms)}] {T.mask(quote, terms)[:600]} [after: {T.mask(after, terms)}]'


def second_opinion(promises: list[dict], terms: list[str], calls: list[T.Call] | None = None) -> None:
    """A separate pass tries to overturn each model-made verdict. A false 'delivered' costs more than a 'can't verify'."""
    settled = [p for p in promises if p.get("verdict") in (DELIVERED, PARTLY, MISSED) and p.get("evidence")]
    # Three at a time on the free tier, because each carries the text around it.
    step = 3 if llm.frugal() else max(1, len(settled))
    for start in range(0, len(settled), step):
        _review_group(settled[start:start + step], terms, calls)


SETTLED = (DELIVERED, PARTLY, MISSED)


def _review_group(settled: list[dict], terms: list[str], calls: list[T.Call] | None) -> None:
    if not settled:
        return
    blocks = []
    for i, p in enumerate(settled):
        blocks.append(
            f"[r{i}] Promise on the {p['call_label']} call, due {p.get('period') or 'unstated'}: {T.mask(p['claim'], terms)}\n"
            f"Words used: {_with_surroundings(calls or [], p.get('ids') or [], p['quote'], terms)}\n"
            f"Verdict: {p['verdict']}. Explanation: {T.mask(p.get('note') or '', terms)}\n"
            f"Evidence ({p['evidence']['call_label']} call): {_with_surroundings(calls or [], p['evidence'].get('ids') or [], p['evidence']['quote'], terms)}"
        )
    prompt = 'Return a JSON array with one element per verdict: {"key": "r0", "upheld": true or false, "why": "under 14 words, if overturned"}\n\n<source>\n' + "\n\n".join(blocks) + "\n</source>"
    try:
        out = llm.ask_json("promise_review_v2", llm.STRONG, REVIEW_SYSTEM, prompt, max_tokens=3000)
    except (llm.NoModel, llm.BudgetExceeded, ValueError):
        out = None
    upheld = set()
    for r in out if isinstance(out, list) else []:
        m = re.fullmatch(r"r(\d+)", str(r.get("key"))) if isinstance(r, dict) else None
        if m and int(m[1]) < len(settled) and r.get("upheld") is False:
            p = settled[int(m[1])]
            p["overturned"] = {"verdict": p["verdict"], "note": p.get("note")}
            p["verdict"] = NO_EVIDENCE
            p["note"] = r.get("why") or "Later calls don't settle this directly."
        elif m and int(m[1]) < len(settled) and r.get("upheld") is True:
            upheld.add(int(m[1]))
    # The free models make more mistakes, so on them silence doesn't count as agreement.
    if llm.frugal():
        for i, p in enumerate(settled):
            if i not in upheld and p["verdict"] in SETTLED:
                p["overturned"] = {"verdict": p["verdict"], "note": p.get("note")}
                p["verdict"] = NO_EVIDENCE
                p["note"] = "A second reading didn't confirm this, so it stays unverified."


def _consistent(verdict: str, claim: str, note: str) -> str:
    """A verdict mustn't contradict its own explanation, a mistake the judging model does sometimes make.
    Limited to measures where higher is plainly better: above a working-capital target is a miss."""
    bad_when_high = re.search(r"\b(working capital|debt|cost|expense|capex|days|tax|receivable|inventor)", claim, re.I)
    if bad_when_high:
        return verdict
    if verdict == PARTLY and re.search(r"\b(above|exceed\w*|beat|ahead of|better than)\b", note, re.I) and re.search(r"\b(revenue|sales|growth|margin|profit|order|ebitda|volume)", claim, re.I):
        return DELIVERED
    if verdict == DELIVERED and re.search(r"\b(no later sentence|unconfirmed|not confirm\w*|doesn't confirm|isn't stated|not stated)\b", note, re.I):
        return NO_EVIDENCE
    if verdict == DELIVERED and re.search(r"\b(below|short of|fell short|less than|missed|not reached|lower than)\b", note, re.I):
        return PARTLY
    return verdict


_QUALIFIERS = ("fully", "full capacity", "full utili", "commercial production", "mass production", "peak", "completed", "complete")


_PLAN = re.compile(r"\b(will|shall|going to|expect(ed|s)? to|plan(s|ning)? to|should|(is|are) expanding|on track|yet to)\b", re.I)
_DONE = re.compile(r"(?<!\bbe )\b(completed|started|commenced|began|achieved|delivered|crossed|reached|launched|closed|was|were|did)\b|\b(have|has|had)\s+(\w+\s+)?\w+(ed|en|un|ne)\b|\b(is|are)\s+(now\s+)?(operational|live)\b", re.I)


def _only_a_plan(evidence: str) -> bool:
    """"We are expanding" and "trials will start" say what's coming, so neither can show a promise was kept.
    A free model marked both delivered, and a second reading agreed."""
    return bool(_PLAN.search(evidence)) and not _DONE.search(evidence)


def _qualifier_met(claim: str, evidence: str) -> bool:
    """Words in a promise that raise the bar have to appear, in some form, in what's said to have met it."""
    a, b = claim.lower(), evidence.lower()
    needed = [q for q in _QUALIFIERS if q in a]
    return not needed or any(q in b or q.rstrip("d") in b for q in needed)


def quarter_of(calls: list[T.Call], sid: str) -> str:
    idx = int(sid[1:].split(".")[0])
    return next((c.label for c in calls if c.index == idx), "")


def _no_later(p: dict, latest_period: str) -> dict:
    due = parse_period(p.get("period"))
    if due and latest_period and due["end"] > latest_period:
        return {"verdict": NOT_DUE, "note": "Due date hasn't arrived"}
    if p["call"] == 0:
        return {"verdict": NOT_DUE if not due else NO_EVIDENCE, "note": "Said on the latest call"}
    return {"verdict": NO_EVIDENCE, "note": "No later mention found in management's remarks", "went_quiet": True}


def goalposts(promises: list[dict]) -> list[dict]:
    """The same metric, for the same period, restated lower on a later call."""
    groups: dict[tuple, list[dict]] = {}
    for p in promises:
        if p["low"] is not None and p.get("period") and p["metric"] != "other" and p.get("unit") in ("%", "crore"):
            groups.setdefault((p["metric"], p["period"].upper().replace(" ", ""), p["unit"]), []).append(p)
    moves = []
    for (metric, period, _unit), items in groups.items():
        items = sorted(items, key=lambda x: x["said_period"])
        for a, b in zip(items, items[1:]):
            if a["said_period"] == b["said_period"] or a["low"] in (None, 0) or b["low"] is None:
                continue
            if b["low"] != a["low"]:
                moves.append({
                    "metric": metric, "period": items[0]["period"], "from": a["low"], "to": b["low"], "unit": a.get("unit"),
                    "from_call": a["call_label"], "to_call": b["call_label"], "direction": "raised" if b["low"] > a["low"] else "cut",
                    "from_quote": a["quote"], "to_quote": b["quote"], "from_page": a["page"], "to_page": b["page"], "from_pages": a.get("pages"), "to_pages": b.get("pages"),
                    "from_url": a["url"], "to_url": b["url"],
                })
    return moves


def _label_business_lines(promises: list[dict], quarters: dict[str, Quarter]) -> None:
    """'FY26 revenue of Rs 7,000 cr' at a company selling Rs 50,000 cr can only be one business line."""
    recent = sorted(quarters)[-4:]
    annual = sum(abs(quarters[k].revenue or 0) for k in recent)
    for p in promises:
        if annual and p.get("unit") == "crore" and p.get("low") and p["low"] < annual * 0.4 and re.search(r"\b(revenue|sales)\b", p["claim"], re.I):
            p["scope"] = "segment"


def _company_sized(move: dict, quarters: dict[str, Quarter]) -> bool:
    """Revenue 'moving' from Rs 7,000 cr to Rs 5,200 cr at a company selling Rs 50,000 cr is a business line."""
    if move["metric"] not in ("revenue", "ebitda", "pat") or not quarters:
        return True
    recent = sorted(quarters)[-4:]
    field = {"revenue": "revenue", "ebitda": "ebitda", "pat": "pat"}[move["metric"]]
    annual = sum(abs(getattr(quarters[k], field) or 0) for k in recent)
    return max(move["from"], move["to"]) >= annual * 0.4


def dedupe(promises: list[dict]) -> list[dict]:
    """A target restated on three calls is one promise. Keep the earliest; note it was reaffirmed."""
    out: dict[tuple, dict] = {}
    for p in sorted(promises, key=lambda x: x["said_period"]):
        key = (p["metric"], (p.get("period") or "").upper().replace(" ", ""), p["low"]) if p["low"] is not None and p["metric"] != "other" else ("t", " ".join(sorted(_terms(p["claim"]))[:6]))
        if key in out:
            out[key].setdefault("reaffirmed", []).append(p["call_label"])
        else:
            out[key] = p
    return list(out.values())


GROUP_SYSTEM = (
    "You see commitments management made on different earnings calls. Group the ones that are the same underlying commitment restated, "
    "even if its date or figure changed between calls. Only group when it's clearly the same project or target. Work only from the text given."
)


def merge_restated(promises: list[dict], groups: list[list[int]]) -> list[dict]:
    """One commitment repeated on four calls counts once, and its moving deadline becomes the story."""
    drop: set[int] = set()
    for group in groups:
        members = sorted({i for i in group if isinstance(i, int) and 0 <= i < len(promises)} - drop, key=lambda i: promises[i]["said_period"])
        if len(members) < 2:
            continue
        items = [promises[i] for i in members]
        canon = items[0]
        # Judge the original promise if it can be judged; otherwise the first restatement that can.
        settled = next((p for p in items if p["verdict"] in (DELIVERED, PARTLY, MISSED)), None)
        # The card shows the promise the verdict is actually about, so its words and its verdict always match.
        lead = settled or canon
        lead["restated"] = list(dict.fromkeys(p["call_label"] for p in items if p is not lead))
        deadlines = list(dict.fromkeys(p["period"] for p in items if p.get("period")))
        if len(deadlines) > 1:
            lead["deadline_moves"] = deadlines
            if lead["verdict"] == NOT_DUE:
                lead["period"] = deadlines[-1]
        drop.update(i for i in members if promises[i] is not lead)
    return [p for i, p in enumerate(promises) if i not in drop]


def group_restated(promises: list[dict], terms: list[str]) -> list[dict]:
    pool = [p for p in promises if p["kind"] != "vague"]
    if len(pool) < 2:
        return promises
    if llm.frugal():
        # Grouping needs the list whole, so on the free tier only as many as fit in one request get grouped.
        keep = len(pool)
        while keep > 2 and sum(250 + len(p["claim"]) for p in pool[:keep]) > llm.source_chars(10**9):
            keep -= 1
        rest = pool[keep:]
        pool = pool[:keep]
    else:
        rest = []
    lines = "\n".join(f"[{i}] ({p['call_label']}, due {p.get('period') or 'unstated'}) {T.mask(p['claim'], terms)} | said: {T.mask(p['quote'], terms)[:160]}" for i, p in enumerate(pool[:70]))
    try:
        groups = llm.ask_json(
            "promise_group_v2", llm.CHEAP, GROUP_SYSTEM,
            f"Return a JSON array of groups, each an array of the numbers in square brackets. Only groups of two or more.\n\n<source>\n{lines}\n</source>",
            max_tokens=1500,
        )
    except (llm.NoModel, llm.BudgetExceeded, ValueError):
        return promises
    if not isinstance(groups, list):
        return promises
    merged = merge_restated(pool, [g for g in groups if isinstance(g, list)])
    return merged + rest + [p for p in promises if p["kind"] == "vague"]


SETTLED = (DELIVERED, PARTLY, MISSED)
_ORDER = {MISSED: 0, PARTLY: 1, DELIVERED: 2, NOT_DUE: 3, NO_EVIDENCE: 4, VAGUE: 5}


def _tallied(pr: dict) -> dict:
    pr["promises"].sort(key=lambda p: (_ORDER.get(p["verdict"], 9), -int(p["said_period"].replace("-", "") or 0)))
    pr["tally"] = {k: sum(p["verdict"] == k for p in pr["promises"]) for k in _ORDER}
    pr["testable"] = pr["tally"][DELIVERED] + pr["tally"][MISSED] + pr["tally"][PARTLY]
    return pr


def _promise_key(p: dict) -> str:
    return f"{p['call_label']}|{re.sub(r'[^a-z0-9]+', '', p['quote'].lower())[:120]}"


def keep_settled(pr: dict, previous: dict | None) -> dict:
    """Once settled, a verdict stands. Models aren't repeatable, so a rebuild alone mustn't change someone's
    record. It only moves if its new evidence comes from a call that wasn't there before, or if the filed
    figures behind a code-scored verdict changed."""
    old_pr = (previous or {}).get("promises") or {}
    known_calls = {c["label"] for c in old_pr.get("calls", [])}
    old = {_promise_key(p): p for p in old_pr.get("promises", []) if p["verdict"] in SETTLED and p.get("checked_by") != "code"}
    if not old:
        return pr
    current = {c["label"] for c in pr["calls"]}
    seen = set()
    for p in pr["promises"]:
        key = _promise_key(p)
        was = old.get(key)
        if not was:
            continue
        seen.add(key)
        newer = p["verdict"] in SETTLED and (p.get("evidence") or {}).get("call_label") not in known_calls
        if p.get("checked_by") != "code" and not newer:
            for field in ("verdict", "note", "evidence", "actual", "overturned"):
                p.pop(field, None)
                if field in was:
                    p[field] = was[field]
    # A model can also fail to notice a promise it found last time, and that shouldn't erase the record.
    pr["promises"] += [p for k, p in old.items() if k not in seen and p["call_label"] in current]
    return _tallied(pr)


def run(calls: list[T.Call], quarters: list[Quarter], company_name: str, log=lambda *_: None) -> dict:
    """Full pipeline for one company."""
    terms = T.mask_terms(company_name)
    by_end = {q.period_end: q for q in quarters}
    # With no filed results, later calls can still settle plants and launches.
    latest = max(by_end) if by_end else max((c.period_end for c in calls if c.period_end), default="")
    raw: list[dict] = []
    model = True
    unread = 0
    for call in calls:
        log(f"Reading {call.label} call")
        try:
            raw += extract(call, terms)
        except (llm.NoModel, llm.BudgetExceeded):
            model = False
            break
        except ValueError:
            unread += 1
    moves = [m for m in goalposts([p for p in raw if p["scope"] == "company"]) if _company_sized(m, by_end)]
    promises = dedupe(raw)
    need_judge = []
    for p in promises:
        due = parse_period(p.get("period"))
        if not p["material"] and p["kind"] != "financial":
            p.update({"verdict": VAGUE, "note": "Minor detail: not tracked"})
            continue
        if p["kind"] == "vague":
            p.update({"verdict": VAGUE, "note": "No figure or date to hold them to"})
            continue
        due = parse_period(p.get("period"))
        if p["kind"] == "financial" and p["metric"] != "other" and due:
            if latest and due["end"] > latest:
                p.update({"verdict": NOT_DUE, "note": f"Due {p['period']}"})
                continue
            a = actual(p, by_end)
            # Sanity check: a "revenue" target far below the company's actual revenue is really a
            # segment figure the model mislabelled, and scoring it would be a false "delivered".
            implausible = a and p["metric"] in ("revenue", "ebitda", "pat") and p["low"] and a["value"] and p["low"] < abs(a["value"]) * 0.4
            if a and _scope_doubt(p, a):
                # 13% guided against 45% reported means they were talking about something narrower.
                p.update({"verdict": NO_EVIDENCE, "note": f"The company-wide figure is {a['value']:g}%, too far from this target to be sure they measure the same thing."})
                continue
            if a and not implausible:
                p.update(score_financial(p, a))
                p["checked_by"] = "code"
                continue
            if implausible:
                p["scope"], p["metric"] = "segment", "other"
        need_judge.append(p)
    if need_judge:
        log("Judging non-financial promises")
        judge_operational(need_judge, calls, latest, terms)
        log("Getting a second opinion")
        second_opinion(need_judge, terms, calls)
    for p in promises:
        p.pop("_key", None)
        p.pop("_before", None)
        p.setdefault("verdict", NO_EVIDENCE)
        p.setdefault("note", "")
    promises = group_restated(promises, terms)
    _label_business_lines(promises, by_end)
    promises = [p for p in promises if p["material"] or p["verdict"] in SETTLED]
    return _tallied({
        "promises": promises, "moves": moves, "model": model, "unread_calls": unread,
        "calls": [{"label": c.label, "held": c.held, "url": c.url, "pages": c.pages, "scanned": c.scanned, "sentences": len(c.sentences)} for c in calls],
    })
