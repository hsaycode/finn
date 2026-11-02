"""Earnings-call transcripts as numbered, speaker-attributed sentences with page numbers.

Everything downstream cites these ids, so a quote shown to the user is always verbatim source
text: the model picks sentences, it never writes the quote.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from . import docs

BSE_HEADERS = {"Referer": "https://www.bseindia.com/"}
NSE_HEADERS = {"Referer": "https://www.nseindia.com/"}


def headers_for(url: str) -> dict:
    return NSE_HEADERS if "nseindia.com" in url else BSE_HEADERS


_MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_SPEAKER = re.compile(r"(?m)^[ \t]*((?:(?:Mr|Ms|Mrs|Dr|Shri)\.?[ \t]+)?[A-Z][\w.'’\-]*(?:[ \t]+[A-Z][\w.'’\-]*){0,4})[ \t]*:[ \t]+")
# Some transcripts mark a speaker with a timestamp instead: "Sh V Srikanth 00:00:01 - 00:09:00 (Group Performance)".
_SPEAKER_TIMED = re.compile(r"(?m)^[ \t]*((?:(?:Sh|Smt|Shri|Mr|Ms|Mrs|Dr)\.?[ \t]+)?[A-Z][\w.'’\-]*(?:[ \t]+[A-Z][\w.'’\-]*){0,4})[ \t]+\d{1,2}:\d{2}:\d{2}\b.*$")
_PARTICIPANT = re.compile(r"(?m)^[ \t]*[•\-*][ \t]*((?:(?:Sh|Smt|Shri|Mr|Ms|Mrs|Dr)\.?[ \t]+)?[A-Z][\w.'’\- ]{2,50}?),")
# A cover letter's "Sub: ..." and "Scrip Code: ..." look just like "Name: ...".
_NOT_A_SPEAKER = re.compile(
    r"^(sub|subject|re|ref|reference|scrip code|scrip symbol|security code|symbol|isin|date|note|notes|encl|enclosure|website|web|email|e-mail|tel|telephone|phone|fax|"
    r"cin|address|registered office|venue|time|place|for|disclaimer|management|participants|moderator note|attention|to|dear sir|kind attention|nse|bse|stock code)$",
    re.I,
)
_ANALYST_INTRO = re.compile(
    r"(?:question|line|follow[- ]?up)[^.?!]{0,60}?\bfrom\s+(?:the\s+line\s+of\s+)?(?:Mr\.?\s+|Ms\.?\s+)?([A-Z][\w.'’\-]+(?:\s+[A-Z][\w.'’\-]+){0,3})"
)
_FORWARD = re.compile(
    r"\b(expect|guid|target|aim|aspir|plan|will\b|shall\b|should\b|going to|intend|anticipat|commit|confident|on track|"
    r"by (?:the )?(?:end|q[1-4]|fy|march|next|mid)|next (?:year|quarter|fiscal)|fy\s?'?\d\d|q[1-4]\b|h[12]\b|outlook|double[- ]digit|trajectory|"
    r"medium[- ]term|long[- ]term|exit|runway|pipeline)",
    re.I,
)
_MILESTONE = re.compile(r"\b(operational|commission|commence|launch|ramp|capacity|plant|facility|capex|order book|go[- ]live|complete)\b", re.I)
_NOISE = re.compile(r"forward[- ]looking statements|safe harbou?r|disclaimer|thank you|welcome|please go ahead|press star|like to hand", re.I)


@dataclass
class Sentence:
    id: str
    page: int
    speaker: str
    role: str  # management | analyst | moderator | unknown
    text: str


@dataclass
class Call:
    index: int  # c0 is the most recent
    label: str  # 'Q2 FY27'
    period_end: str  # the quarter being discussed
    held: str  # 'Oct 2026'
    url: str
    pages: int
    sentences: list[Sentence] = field(default_factory=list)
    scanned: bool = False
    timed: bool = False  # the timestamped format, where roles can't be told apart

    def mgmt(self) -> list[Sentence]:
        return [s for s in self.sentences if s.role in ("management", "unknown")]

    def by_id(self) -> dict[str, Sentence]:
        return {s.id: s for s in self.sentences}

    def nearby(self, s: Sentence, before: int = 1, after: int = 0, width: int = 220) -> tuple[str, str]:
        """What surrounds a sentence. "It should hit 7,000 crores" means nothing without the line about exports."""
        i = next((n for n, x in enumerate(self.sentences) if x.id == s.id), None)
        if i is None:
            return "", ""
        clip = lambda xs: " ".join(x.text[:width] for x in xs)
        return clip(self.sentences[max(0, i - before):i]), clip(self.sentences[i + 1:i + 1 + after])

    @property
    def is_earnings_call(self) -> bool:
        """Analyst days and AGM speeches have no moderator or analysts, and promises in them read differently."""
        if self.timed:
            return True
        spoken = Counter(s.speaker for s in self.sentences if s.speaker)
        if sum(1 for n in spoken.values() if n >= 5) < 3:
            return True  # not enough real speakers to judge this format, so don't discard it
        return any(s.role in ("moderator", "analyst") for s in self.sentences)


def fy_quarter_end(q: int, fy: int) -> str:
    """India's fiscal year: Q1 FY27 is Apr-Jun 2026, so it ends 30 June 2026."""
    y = 2000 + fy if fy < 100 else fy
    return {1: f"{y-1}-06-30", 2: f"{y-1}-09-30", 3: f"{y-1}-12-31", 4: f"{y}-03-31"}[q]


def quarter_label(period_end: str) -> str:
    y, m = int(period_end[:4]), int(period_end[5:7])
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(m)
    fy = y + 1 if m >= 4 else y
    return f"Q{q} FY{fy % 100}" if q else period_end


def _date_quarter(held: str) -> str | None:
    """A call discusses the quarter that closed shortly before it was held."""
    hm = re.match(r"(\w{3}) (20\d\d)", held)
    if not hm or hm[1].lower() not in _MONTHS:
        return None
    mo, y = _MONTHS[hm[1].lower()], int(hm[2])
    for end_mo, ey in ((9, y), (6, y), (3, y), (12, y - 1)):
        gap = (y - ey) * 12 + mo - end_mo
        if 0 < gap <= 3:
            return f"{ey}-{end_mo:02d}-{31 if end_mo in (3, 12) else 30}"
    return None


def _reported_quarter(text: str, held: str) -> str | None:
    """Which quarter is this call about? The document's own words win, but only if they fit the
    call's date: a transcript mentions other quarters too, so the most frequent one can be wrong."""
    expected = _date_quarter(held)
    head = text[:8000]
    hits: Counter = Counter()
    for m in re.finditer(r"\bQ\s?([1-4])\s*(?:and\s+(?:the\s+)?full\s+year\s*)?[-' ]?\s*(?:FY|F\.Y\.)?\s*'?\s*(20\d\d|\d\d)\b", head, re.I):
        fy = int(m[2]) % 100
        if 15 <= fy <= 40:
            hits[fy_quarter_end(int(m[1]), fy)] += 1
    for m in re.finditer(r"\b(first|second|third|fourth)\s+quarter(?:\s+of)?(?:\s+(?:the\s+)?(?:fiscal|financial\s+year))?\s*(?:year\s*)?(?:FY\s*)?'?(20\d\d|\d\d)\b", head, re.I):
        fy = int(m[2]) % 100
        if 15 <= fy <= 40:
            hits[fy_quarter_end({"first": 1, "second": 2, "third": 3, "fourth": 4}[m[1].lower()], fy)] += 1
    if expected and hits.get(expected):
        return expected
    if hits and not expected:
        return hits.most_common(1)[0][0]
    if expected:
        # A late-filed transcript: only believe an earlier quarter if the document keeps saying so.
        for end, n in hits.most_common():
            if n >= 3 and 0 <= (date.fromisoformat(expected) - date.fromisoformat(end)).days <= 200:
                return end
        return expected
    return None


def _clean_page(t: str) -> str:
    t = re.sub(r"(?im)^\s*page\s+\d+\s+of\s+\d+.*$", "", t)
    t = re.sub(r"Page \d+ of \d+", " ", t)
    t = t.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("’", "'")
    return t


def _strip_running_headers(pages: list[str]) -> list[str]:
    """Lines repeating across pages are headers and footers; left in, they end up inside quotes."""
    seen: Counter = Counter()
    for p in pages:
        seen.update({ln.strip() for ln in p.splitlines() if ln.strip()})
    repeated = {ln for ln, n in seen.items() if n >= max(3, len(pages) // 4)}
    return ["\n".join(ln for ln in p.splitlines() if ln.strip() not in repeated) for p in pages]


def _sentences(text: str) -> list[str]:
    t = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.?!])\s+(?=[A-Z0-9\"'(])", t)
    return [p.strip() for p in parts if len(p.strip()) >= 12]


def parse(doc: docs.Doc, index: int, held: str, url: str) -> Call:
    pages = _strip_running_headers([_clean_page(p) for p in doc.pages])
    period = _reported_quarter("\n".join(pages[:3]), held) or ""
    call = Call(index=index, label=quarter_label(period) if period else held, period_end=period, held=held, url=url, pages=len(pages), scanned=doc.scanned)
    if doc.scanned:
        return call

    # First pass: who are the analysts? The moderator introduces each one.
    analysts: set[str] = set()
    for m in _ANALYST_INTRO.finditer(" ".join(re.sub(r"\s+", " ", p) for p in pages)):
        analysts.add(_norm(m[1]))

    joined = "\n".join(pages)
    timed = len(_SPEAKER_TIMED.findall(joined)) >= 3
    pattern = _SPEAKER_TIMED if timed else _SPEAKER
    call.timed = timed
    # This format lists the company's people up front. Questions run on under the last timestamp,
    # so roles stay unknown, and a name is only shown for someone on that list.
    team = {_norm(m[1]) for m in _PARTICIPANT.finditer(joined[:6000])} if timed else set()

    blocks: list[tuple[int, str, str]] = []
    speaker = ""
    for pno, page in enumerate(pages, 1):
        pos = 0
        for m in pattern.finditer(page):
            if m.start() > pos:
                blocks.append((pno, speaker, page[pos:m.start()]))
            speaker = m[1].strip()
            pos = m.end()
        blocks.append((pno, speaker, page[pos:]))

    def real(name: str) -> bool:
        return bool(name) and not _NOT_A_SPEAKER.match(name.strip())

    # Some formats (Reliance's, for one) label no speakers at all. Then every sentence is kept, unattributed.
    named = len({spk for _, spk, _ in blocks if real(spk)}) >= 3
    n = 0
    for pno, spk, body in blocks:
        if named and not spk:
            continue  # participant list, before anyone speaks
        for sent in _sentences(body):
            who = spk if named and real(spk) else ""
            # Letter text under a false "speaker" is dropped, but still numbered so that ids stay stable.
            if not (named and not real(spk)):
                if timed:
                    who, role = (who if _on_team(who, team) else ""), "unknown"
                else:
                    role = _role(who, analysts) if named else "unknown"
                call.sentences.append(Sentence(f"c{index}.s{n}", pno, who, role, sent))
            n += 1
    return call


_AR_CUES = re.compile(r"\b(management discussion|outlook|going forward|we expect|we plan|we will|will be commissioned|expansion|future prospects|chairman'?s? (?:message|letter|statement)|managing director'?s? (?:message|letter)|strategy|next (?:year|financial year))\b", re.I)


def from_annual_report(doc: docs.Doc, index: int, year: int, url: str, pages_to_read: int = 25) -> Call:
    """For companies that hold no earnings calls, the annual report is where management speaks.
    Only the pages that discuss the future are read, and sentences keep their real page numbers."""
    label = f"FY{year % 100} annual report"
    call = Call(index=index, label=label, period_end=f"{year}-03-31", held=str(year), url=url, pages=len(doc.pages), scanned=doc.scanned)
    if doc.scanned:
        return call
    ranked = sorted(range(len(doc.pages)), key=lambda i: -len(_AR_CUES.findall(doc.pages[i])))[:pages_to_read]
    n = 0
    for i in sorted(ranked):
        for sent in _sentences(_clean_page(doc.pages[i])):
            if 30 <= len(sent) <= 600:
                call.sentences.append(Sentence(f"c{index}.s{n}", i + 1, "Annual report", "management", sent))
            n += 1
    return call


def quote_from(sents: list[Sentence]) -> dict:
    """One speaker's words from one call, in document order, with every gap marked.

    A model can cite sentences pages apart. Joined silently they read as one passage that the PDF
    doesn't contain, which can change their meaning and would fail anyone checking the source.
    """
    first = sents[0]
    call = first.id.split(".")[0]
    number = lambda s: int(s.id.split(".s")[1])
    kept = sorted({s.id: s for s in sents if s.id.split(".")[0] == call and s.speaker == first.speaker}.values(), key=number)
    text = kept[0].text
    for before, s in zip(kept, kept[1:]):
        text += (" " if number(s) == number(before) + 1 else " […] ") + s.text
    return {"quote": text, "speaker": first.speaker, "page": kept[0].page, "pages": sorted({s.page for s in kept})}


def _norm(name: str) -> str:
    return re.sub(r"^(mr|ms|mrs|dr|shri|sh|smt)\.?\s+", "", name.lower()).strip()


def _on_team(speaker: str, team: set[str]) -> bool:
    s = _norm(speaker)
    last = s.split()[-1] if s else ""
    return bool(last) and any(s == t or last == t.split()[-1] for t in team)


def _role(speaker: str, analysts: set[str]) -> str:
    if not speaker:
        return "unknown"
    s = _norm(speaker)
    if s.startswith("moderator") or "operator" in s:
        return "moderator"
    last = s.split()[-1]
    for a in analysts:
        if s == a or last == a.split()[-1] or s.split()[0] == a.split()[0]:
            return "analyst"
    return "management"


def candidates(call: Call, limit: int = 110) -> list[Sentence]:
    """Pre-filter so the model reads roughly a tenth of the transcript. Most of the saving is here."""
    picks = []
    for s in call.mgmt():
        if len(s.text) > 500 or _NOISE.search(s.text):
            continue
        if s.role == "unknown" and s.text.endswith("?"):
            continue  # with no speaker labels, a question is probably an analyst's
        has_num = bool(re.search(r"\d", s.text))
        forward = bool(_FORWARD.search(s.text))
        if forward and (has_num or _MILESTONE.search(s.text) or re.search(r"\b(growth|margin|double|grow)\b", s.text, re.I)):
            picks.append((2 if has_num else 1, s))
    picks.sort(key=lambda x: -x[0])
    keep = {id(s) for _, s in picks[:limit]}
    return [s for s in call.mgmt() if id(s) in keep]


_MASK_DROP = {"ltd", "limited", "india", "the", "of", "and", "company", "co", "corporation", "industries", "technologies", "technology", "systems", "enterprises", "bank", "(india)", "services", "consultancy", "energy", "power", "group"}


def mask_terms(name: str) -> list[str]:
    tokens = [t for t in re.findall(r"[A-Za-z&]+", name) if t.lower() not in _MASK_DROP and len(t) >= 3]
    full = re.sub(r"\s+(ltd\.?|limited)$", "", name, flags=re.I).strip()
    return [full, *tokens[:2]] if tokens else [full]


def mask(text: str, terms: list[str]) -> str:
    """Hide the company's identity from the model, so it can't reach for what it remembers about it."""
    for t in sorted(terms, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(t)}\b", "the Company", text, flags=re.I)
    return text
